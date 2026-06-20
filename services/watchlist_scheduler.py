"""
Scheduler and market-calendar logic for the watchlist evaluation lifecycle
(Phase 6 — see WATCHLIST_AND_ALERTS_DESIGN.md).

What this module DOES:
  - Decides whether it is safe/appropriate to run a watchlist eligibility
    evaluation right now (`should_run_watchlist_evaluation`).
  - Provides US market-calendar helpers (`is_us_market_day`,
    `is_after_regular_market_close`, `is_early_close_day`) computed
    algorithmically — no new dependency was added (see "Holiday
    limitations" below).
  - Guards against overlapping evaluation runs (`can_start_evaluation_run`),
    using the Phase 2 evaluation_runs table, and detects/clears stuck
    'started' runs after a configurable timeout.
  - Orchestrates one scheduled attempt end-to-end (`run_scheduled_evaluation`),
    which is a thin wrapper around services.watchlist_evaluator's existing
    run_watchlist_evaluation — no scoring/hysteresis logic is duplicated
    or modified here.

What this module does NOT do:
  - Run automatically in the background. Nothing in this module starts a
    thread, a loop, or a `schedule` job. It is a library + CLI only —
    something (a human, or later a real scheduler integration) must call
    `run_scheduled_evaluation()` explicitly. agent/core.py and main.py are
    untouched by this phase.
  - Send Telegram messages, or implement /refresh_watchlist (Phase 7).
  - Change scoring/hysteresis logic (analyzers/eligibility.py is untouched).

Timezone rules:
  All public functions that take a "now" parameter require a
  timezone-aware datetime and raise ValueError on a naive one — there is
  no implicit "assume UTC" or "assume local time" fallback. Internally,
  US market timing is evaluated in America/New_York (correctly handling
  DST via zoneinfo), and all evaluation_runs timestamps remain stored in
  UTC exactly as Phase 2 already does.

Holiday / early-close limitations (documented, no new dependency added):
  `is_us_market_day()` computes the standard fixed-date and nth-weekday US
  market holidays algorithmically (New Year's Day, MLK Day, Presidents
  Day, Good Friday via the Gregorian Easter algorithm, Memorial Day,
  Juneteenth, Independence Day, Labor Day, Thanksgiving, Christmas — with
  Saturday/Sunday observed-date shifting). This is a conservative
  approximation: NYSE can announce unscheduled closures (rare) that this
  cannot know about — use `config.WATCHLIST_EXTRA_HOLIDAY_DATES` to add
  those manually. `is_early_close_day()` flags the two predictable early
  closes (day after Thanksgiving, Christmas Eve) but is NOT used to change
  scheduling — the default 17:30 America/New_York threshold is already
  safely after both a normal (16:00) and an early (~13:00) close, so an
  undetected early-close day still behaves safely; `is_early_close_day()`
  is exposed for visibility/future use only.

Run-once-per-market-day:
  A scheduled run only counts as "already done" for a market date if a
  *scheduled* run (run_type='scheduled') *succeeded* (status='success' or
  'partial_failure' — both completed without a fatal error) for that
  date. A *failed* scheduled run does NOT block a retry the same day —
  the whole point of a stuck/failed run is that it should be retriable.
  Manual or dry-run-triggered runs (run_type in 'manual'/'dry_run') never
  count toward this check at all — only an actual scheduled run can
  satisfy "today's scheduled run already happened."
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import db.database as db
from config import (
    WATCHLIST_EXTRA_HOLIDAY_DATES,
    WATCHLIST_SCHEDULE_APPLY,
    WATCHLIST_SCHEDULE_HOUR_ET,
    WATCHLIST_SCHEDULE_MINUTE_ET,
    WATCHLIST_SCHEDULE_STUCK_RUN_TIMEOUT_MINUTES,
)
from data.market_data_validator import MarketDataClient
from services.watchlist_evaluator import run_watchlist_evaluation

ET = ZoneInfo("America/New_York")


def _require_aware(now_utc: datetime) -> None:
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be a timezone-aware datetime, not naive")


# ── Market calendar (algorithmic, no new dependency) ─────────────────────────

def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """weekday: Monday=0 .. Sunday=6. n=1 for the first occurrence, etc."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


def _observed(d: date) -> date:
    """Saturday holidays are observed the preceding Friday, Sunday the following Monday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _easter_sunday(year: int) -> date:
    """Anonymous Gregorian algorithm — well-known public-domain formula, no dependency."""
    a = year % 19
    b = year // 100
    c = year % 100
    d_ = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d_ - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def us_market_holidays(year: int) -> frozenset[date]:
    """Standard NYSE full-closure holidays for one calendar year, computed
    algorithmically. See module docstring for the documented limitation
    (cannot know about rare unscheduled closures)."""
    good_friday = _easter_sunday(year) - timedelta(days=2)
    return frozenset({
        _observed(date(year, 1, 1)),
        _nth_weekday_of_month(year, 1, 0, 3),
        _nth_weekday_of_month(year, 2, 0, 3),
        good_friday,
        _last_weekday_of_month(year, 5, 0),
        _observed(date(year, 6, 19)),
        _observed(date(year, 7, 4)),
        _nth_weekday_of_month(year, 9, 0, 1),
        _nth_weekday_of_month(year, 11, 3, 4),
        _observed(date(year, 12, 25)),
    })


def is_us_market_holiday(d: date) -> bool:
    if d.isoformat() in WATCHLIST_EXTRA_HOLIDAY_DATES:
        return True
    return d in us_market_holidays(d.year)


def is_us_market_day(d: date) -> bool:
    return d.weekday() < 5 and not is_us_market_holiday(d)


def is_early_close_day(d: date) -> bool:
    """Day-after-Thanksgiving and Christmas-Eve-on-a-weekday. Informational
    only — see module docstring; does not affect scheduling decisions."""
    if not is_us_market_day(d):
        return False
    thanksgiving = _nth_weekday_of_month(d.year, 11, 3, 4)
    day_after_thanksgiving = thanksgiving + timedelta(days=1)
    christmas_eve = date(d.year, 12, 24)
    return d in (day_after_thanksgiving, christmas_eve)


def is_after_regular_market_close(now_utc: datetime) -> bool:
    _require_aware(now_utc)
    now_et = now_utc.astimezone(ET)
    threshold_et = now_et.replace(
        hour=WATCHLIST_SCHEDULE_HOUR_ET, minute=WATCHLIST_SCHEDULE_MINUTE_ET,
        second=0, microsecond=0,
    )
    return now_et >= threshold_et


# ── Run-once-per-market-day tracking ──────────────────────────────────────────

def _market_date_of_run(run: dict) -> date:
    started = datetime.strptime(run["started_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return started.astimezone(ET).date()


def _scheduled_runs(limit: int = 200) -> list[dict]:
    return [r for r in db.list_recent_evaluation_runs(limit=limit) if r["run_type"] == "scheduled"]


def already_ran_successfully_for_market_date(market_date: date) -> bool:
    """True if a *scheduled* run completed (success or partial_failure — not
    'failed') for this market date. A failed scheduled run does NOT block a
    same-day retry — see module docstring."""
    for run in _scheduled_runs():
        if run["status"] in ("success", "partial_failure") and _market_date_of_run(run) == market_date:
            return True
    return False


def get_last_successful_scheduled_evaluation() -> dict | None:
    for run in _scheduled_runs():
        if run["status"] in ("success", "partial_failure"):
            return run
    return None


# ── Concurrency guard ──────────────────────────────────────────────────────────

def mark_stuck_runs_failed(
    *, now_utc: datetime | None = None, stuck_timeout_minutes: int | None = None
) -> list[int]:
    """Mark every 'started' run older than the timeout as 'failed'. Bounded:
    re-queries get_in_progress_evaluation_run() after each mark, so it
    naturally terminates once nothing stuck remains. Does not implement
    distributed locking — single-process use only, per Phase 6 scope."""
    now_utc = now_utc or datetime.now(timezone.utc)
    timeout = (
        stuck_timeout_minutes if stuck_timeout_minutes is not None
        else WATCHLIST_SCHEDULE_STUCK_RUN_TIMEOUT_MINUTES
    )
    marked: list[int] = []
    while True:
        run = db.get_in_progress_evaluation_run()
        if run is None:
            break
        started = datetime.strptime(run["started_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        age_minutes = (now_utc - started).total_seconds() / 60
        if age_minutes <= timeout:
            break
        db.update_evaluation_run_failure(
            run["run_id"],
            f"marked failed by scheduler: stuck in 'started' status for {age_minutes:.0f} "
            f"minute(s), exceeding the {timeout}-minute stuck-run timeout",
        )
        marked.append(run["run_id"])
    return marked


def can_start_evaluation_run(
    *, now_utc: datetime | None = None, stuck_timeout_minutes: int | None = None
) -> tuple[bool, str]:
    """Concurrency guard. Sweeps stuck 'started' runs first, then refuses to
    start if a genuinely fresh run is still in progress."""
    now_utc = now_utc or datetime.now(timezone.utc)
    _require_aware(now_utc)
    cleared = mark_stuck_runs_failed(now_utc=now_utc, stuck_timeout_minutes=stuck_timeout_minutes)

    in_progress = db.get_in_progress_evaluation_run()
    if in_progress is None:
        reason = "no run in progress"
        if cleared:
            reason += f" (cleared {len(cleared)} stuck run(s): {cleared})"
        return True, reason

    started = datetime.strptime(in_progress["started_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    age_minutes = (now_utc - started).total_seconds() / 60
    return False, (
        f"run {in_progress['run_id']} is already in progress ({age_minutes:.0f} minute(s) old); "
        "refusing to start a concurrent evaluation run"
    )


# ── Scheduling decision ───────────────────────────────────────────────────────

def should_run_watchlist_evaluation(now_utc: datetime) -> tuple[bool, str]:
    """
    The single source of truth for "is it due right now?". Does not check
    the concurrency guard — that's a separate, independent gate (see
    can_start_evaluation_run) checked separately by run_scheduled_evaluation.
    """
    _require_aware(now_utc)
    now_et = now_utc.astimezone(ET)
    market_date = now_et.date()

    if market_date.weekday() >= 5:
        return False, f"{market_date.isoformat()} is a weekend"
    if is_us_market_holiday(market_date):
        return False, f"{market_date.isoformat()} is a US market holiday"
    if not is_after_regular_market_close(now_utc):
        return False, (
            f"before the {WATCHLIST_SCHEDULE_HOUR_ET:02d}:{WATCHLIST_SCHEDULE_MINUTE_ET:02d} "
            "America/New_York threshold"
        )
    if already_ran_successfully_for_market_date(market_date):
        return False, f"already ran successfully for market date {market_date.isoformat()}"
    return True, "due"


def next_watchlist_evaluation_time(now_utc: datetime) -> datetime:
    """Next UTC instant at which a scheduled run would become due, stepping
    over weekends/holidays and skipping today if already satisfied."""
    _require_aware(now_utc)
    now_et = now_utc.astimezone(ET)
    d = now_et.date()

    if (
        is_us_market_day(d)
        and not is_after_regular_market_close(now_utc)
        and not already_ran_successfully_for_market_date(d)
    ):
        target_et = now_et.replace(
            hour=WATCHLIST_SCHEDULE_HOUR_ET, minute=WATCHLIST_SCHEDULE_MINUTE_ET,
            second=0, microsecond=0,
        )
        return target_et.astimezone(timezone.utc)

    d += timedelta(days=1)
    while not is_us_market_day(d):
        d += timedelta(days=1)
    target_et = datetime(
        d.year, d.month, d.day, WATCHLIST_SCHEDULE_HOUR_ET, WATCHLIST_SCHEDULE_MINUTE_ET, tzinfo=ET
    )
    return target_et.astimezone(timezone.utc)


# ── Orchestration ─────────────────────────────────────────────────────────────

def run_scheduled_evaluation(
    *,
    apply: bool | None = None,
    now: datetime | None = None,
    client: MarketDataClient | None = None,
    force: bool = False,
    triggered_by: str = "scheduler",
) -> dict:
    """
    Run one scheduled evaluation attempt, if (and only if) it's actually
    due and no other run is in progress — unless force=True, which skips
    both the schedule-due check and the concurrency guard (for manual CLI
    testing only; never set force=True from an unattended process).

    apply=None resolves to config.WATCHLIST_SCHEDULE_APPLY (default False —
    automatic scheduled runs are dry-run unless explicitly configured).
    An explicit apply=True/False here is always honored regardless of the
    config default (e.g. a deliberate `--scheduled-apply --yes` CLI call).

    Returns {"ran": bool, "skipped_reason": str | None, "market_date": str,
    "result": DryRunEvaluationResult | None}. No evaluation_runs row is
    written for a skipped attempt — see module docstring.
    """
    now = now or datetime.now(timezone.utc)
    _require_aware(now)
    market_date = now.astimezone(ET).date()
    resolved_apply = WATCHLIST_SCHEDULE_APPLY if apply is None else apply

    if not force:
        guard_ok, guard_reason = can_start_evaluation_run(now_utc=now)
        if not guard_ok:
            return {"ran": False, "skipped_reason": guard_reason, "market_date": market_date.isoformat(), "result": None}

        due, due_reason = should_run_watchlist_evaluation(now)
        if not due:
            return {"ran": False, "skipped_reason": due_reason, "market_date": market_date.isoformat(), "result": None}

    result = run_watchlist_evaluation(
        apply=resolved_apply,
        client=client,
        triggered_by=triggered_by,
        now=now,
        run_type="scheduled",
        extra_metadata={"market_date": market_date.isoformat(), "scheduled_apply_config": resolved_apply},
    )
    return {"ran": True, "skipped_reason": None, "market_date": market_date.isoformat(), "result": result}
