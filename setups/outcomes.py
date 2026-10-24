"""Outcome simulation (docs/SETUP_SPEC.md §6, §12) -- walks forward through
the bars that came after a defined setup (setups/definition.py) and reports
what happened: fill, invalidation, chase-rejection, stop/target/timeout
resolution, or a data problem. Pure function: no network, no DB, no
scheduler -- importable by a future replay harness and a future live
reconciliation job without either one's side effects (§12: "a new, separate
module; scripts/populate_outcomes.py is not touched").

Parity-tested against scripts/populate_outcomes.py's compute_outcome() for
the parts the two share: the same-day stop-before-target tie-break, MAE
measured through the exit bar, and T+0 exclusion (§12) -- differing only in
horizon length (30 vs 10 sessions), target definition, and the added costs
model (§12: 10 bps slippage each way, settled 2026-10-22).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import pandas as pd

from setups.calendar_utils import advance_us_trading_days
from setups.definition import SetupDefinition
from setups.frame_utils import normalize_frame
from setups.params import OutcomeParams, V1_OUTCOME_PARAMS
from setups.reason_codes import (
    BARRIER_STOP,
    BARRIER_TARGET,
    BARRIER_TIMEOUT_30,
    CHASE_RR_BELOW_3,
    DATA_GAP,
    EXPIRED_UNFILLED,
    FILLED_STOPPED_SAME_DAY,
    INVALIDATED_BEFORE_FILL,
    OPEN_AT_END,
)


@dataclass
class OutcomeResult:
    status: str
    fill_date: date | None = None
    fill_price: float | None = None
    exit_date: date | None = None
    exit_price: float | None = None
    first_barrier_hit: str | None = None
    r_multiple: float | None = None
    mae_r: float | None = None
    mfe_r: float | None = None
    sessions_held: int | None = None
    # §6/§12 diagnostic only -- true when the fill session's own high
    # reached target and its low did not breach stop. The target is still
    # never credited on the fill session itself (conservative, stated rule).
    target_touched_on_fill_session: bool = False


def _normalize_date(d) -> date:
    """Same tz-aware/tz-naive normalization every other setups/ module uses
    (e.g. setups/definition.py's _last_bar_date): Timestamp.date() drops any
    tz info, a plain date passes through unchanged."""
    return d.date() if hasattr(d, "date") else d


def _index_dates(df: pd.DataFrame) -> list:
    return [_normalize_date(ts) for ts in df.index]


def _row_at(df: pd.DataFrame, index_dates: list, target_date: date):
    try:
        pos = index_dates.index(target_date)
    except ValueError:
        return None
    return df.iloc[pos]


def _bar_is_unusable(row: pd.Series) -> bool:
    for col in ("open", "high", "low", "close"):
        if pd.isna(row[col]):
            return True
    return False


def _missing_session_status(index_dates: list, expected_date: date) -> str:
    """A session missing from df_future is DATA_GAP if later data exists
    in df_future (an isolated hole, data continues past it); it's
    OPEN_AT_END if df_future simply has nothing after this point (the
    history just ends -- never guess what would have happened). Uses
    max(), not the last element -- defensive even though the unsorted/
    duplicate-index guard in simulate_outcome() already rejects any frame
    where index order could matter here."""
    last_date = max(index_dates) if index_dates else None
    if last_date is not None and last_date > expected_date:
        return DATA_GAP
    return OPEN_AT_END


def _validate_setup(setup: SetupDefinition) -> None:
    """A malformed SetupDefinition is a caller bug, not a data problem --
    raises ValueError rather than returning a result."""
    for name in ("trigger", "stop", "target", "sl_price"):
        value = getattr(setup, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"setup.{name} must be a finite number, got {value!r}")
    if not (setup.stop < setup.trigger < setup.target):
        raise ValueError(
            "setup must satisfy stop < trigger < target, got "
            f"stop={setup.stop}, trigger={setup.trigger}, target={setup.target}"
        )


def _frame_is_malformed(df: pd.DataFrame) -> bool:
    """True if df_future can't be used at all: missing a required OHLC
    column, or an index that isn't strictly increasing (unsorted or
    duplicate dates) -- never raises, caller returns DATA_GAP instead."""
    missing = {"open", "high", "low", "close"} - set(df.columns)
    if missing:
        return True
    normalized = [_normalize_date(ts) for ts in df.index]
    return any(normalized[i] >= normalized[i + 1] for i in range(len(normalized) - 1))


def _sessions_through(start_date: date, end_date: date, reference_sessions) -> list:
    """Expected session dates strictly after start_date, through end_date
    inclusive -- from reference_sessions (e.g. SPY's own index in replay,
    always present) if given, else walked from the holiday-aware calendar."""
    if reference_sessions is not None:
        ref_dates = sorted(_normalize_date(d) for d in reference_sessions)
        return [d for d in ref_dates if start_date < d <= end_date]
    dates = []
    d = start_date
    while True:
        d = advance_us_trading_days(d, 1)
        if d > end_date:
            break
        dates.append(d)
    return dates


def _next_n_sessions(start_date: date, n: int, reference_sessions) -> list:
    if reference_sessions is not None:
        ref_dates = sorted(_normalize_date(d) for d in reference_sessions)
        after = [d for d in ref_dates if d > start_date]
        return after[:n]
    dates = []
    d = start_date
    for _ in range(n):
        d = advance_us_trading_days(d, 1)
        dates.append(d)
    return dates


def _r_multiple(entry: float, exit_price: float, risk: float, slippage: float) -> float:
    """R net of costs (§12): slippage adjusts the entry/exit cash flows
    only -- the denominator stays the raw, pre-cost nominal risk fixed at
    fill, matching §6's own recomputed_RR formula."""
    entry_net = entry * (1 + slippage)
    exit_net = exit_price * (1 - slippage)
    return (exit_net - entry_net) / risk


def simulate_outcome(
    setup: SetupDefinition,
    df_future: pd.DataFrame,
    params: OutcomeParams = V1_OUTCOME_PARAMS,
    reference_sessions=None,
) -> OutcomeResult:
    """df_future: OHLCV bars, chronological, holding ONLY bars strictly
    after setup.scan_date -- any bar at or before scan_date is defensively
    filtered out before use, never trusted silently.

    reference_sessions: an optional ordered series of expected session
    dates (e.g. SPY's own index in replay, always present and never
    missing a session). Used to tell "every expected session genuinely
    elapsed with no fill/resolution" (EXPIRED_UNFILLED / timeout_30) apart
    from "an expected session is simply missing from df_future" (DATA_GAP)
    apart from "the data just ran out" (OPEN_AT_END). Without one, expected
    sessions are instead walked forward from setups/calendar_utils.py's
    holiday-aware calendar.
    """
    _validate_setup(setup)  # caller bug -> raises, never a data problem

    # Frame guards -- never raise on data, always DATA_GAP instead.
    if not isinstance(df_future, pd.DataFrame):
        return OutcomeResult(status=DATA_GAP)
    df_future = normalize_frame(df_future)  # flatten MultiIndex, lowercase, numeric-coerce
    if _frame_is_malformed(df_future):
        return OutcomeResult(status=DATA_GAP)

    scan_date = _normalize_date(setup.scan_date)
    validity_end = _normalize_date(setup.validity_end)

    df_future = df_future.copy()
    df_future.index = pd.Index([_normalize_date(ts) for ts in df_future.index])
    # Defensive: a bar at or before scan_date must never be used, even if
    # the caller accidentally leaked one in.
    df_future = df_future[df_future.index > scan_date]
    index_dates = _index_dates(df_future)

    slippage = params.slippage_bps / 10000.0

    # ---- pre-fill scan (§6) ----
    fill_date = None
    fill_row = None
    expected_pre_fill = _sessions_through(scan_date, validity_end, reference_sessions)
    for expected_date in expected_pre_fill:
        row = _row_at(df_future, index_dates, expected_date)
        if row is None:
            return OutcomeResult(status=_missing_session_status(index_dates, expected_date))
        if _bar_is_unusable(row):
            return OutcomeResult(status=DATA_GAP)

        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])

        if high >= setup.trigger:
            fill_date, fill_row = expected_date, row
            break

        if low <= setup.stop or close < setup.sl_price:
            return OutcomeResult(status=INVALIDATED_BEFORE_FILL)

    if fill_date is None:
        # Only a genuine EXPIRED_UNFILLED if the expected-session list
        # actually reaches validity_end -- an empty list, or a
        # reference_sessions series that itself ends earlier, means we
        # never actually confirmed the window elapsed.
        if not expected_pre_fill or expected_pre_fill[-1] < validity_end:
            return OutcomeResult(status=OPEN_AT_END)
        return OutcomeResult(status=EXPIRED_UNFILLED)

    # ---- fill (§6) ----
    fill_open = float(fill_row["open"])
    fill_high = float(fill_row["high"])
    fill_low = float(fill_row["low"])
    fill_price = max(setup.trigger, fill_open)

    target_touched_on_fill_session = fill_high >= setup.target and fill_low > setup.stop

    risk = fill_price - setup.stop
    rr = (setup.target - fill_price) / risk if risk > 0 else float("-inf")
    if rr < params.min_rr:
        # R:R gate checked BEFORE the same-session stop check: a session
        # that both gaps to a sub-min_rr fill price AND trades down
        # through the stop is CHASE_RR_BELOW_3 (never taken), not a loss.
        return OutcomeResult(
            status=CHASE_RR_BELOW_3,
            fill_date=fill_date,
            fill_price=fill_price,
            target_touched_on_fill_session=target_touched_on_fill_session,
        )

    if fill_low <= setup.stop:
        exit_price = fill_open if fill_open <= setup.stop else setup.stop
        return OutcomeResult(
            status=FILLED_STOPPED_SAME_DAY,
            fill_date=fill_date,
            fill_price=fill_price,
            exit_date=fill_date,
            exit_price=exit_price,
            first_barrier_hit=BARRIER_STOP,
            r_multiple=_r_multiple(fill_price, exit_price, risk, slippage),
            mae_r=(fill_low - fill_price) / risk,
            mfe_r=(fill_high - fill_price) / risk,
            sessions_held=1,
            target_touched_on_fill_session=target_touched_on_fill_session,
        )

    # ---- post-fill monitoring (§12) ----
    worst_low = fill_low
    best_high = fill_high
    sessions_held = 1  # the fill session itself counts as session 1 held

    expected_post_fill = _next_n_sessions(fill_date, params.timeout_sessions, reference_sessions)
    for i, expected_date in enumerate(expected_post_fill, start=1):
        row = _row_at(df_future, index_dates, expected_date)
        if row is None:
            return OutcomeResult(
                status=_missing_session_status(index_dates, expected_date),
                fill_date=fill_date,
                fill_price=fill_price,
                target_touched_on_fill_session=target_touched_on_fill_session,
            )
        if _bar_is_unusable(row):
            return OutcomeResult(
                status=DATA_GAP,
                fill_date=fill_date,
                fill_price=fill_price,
                target_touched_on_fill_session=target_touched_on_fill_session,
            )

        open_ = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        worst_low = min(worst_low, low)
        best_high = max(best_high, high)
        sessions_held += 1

        mae_r = (worst_low - fill_price) / risk
        mfe_r = (best_high - fill_price) / risk

        if low <= setup.stop:
            # Stop checked before target on a same-bar tie. A session
            # opening at or below the stop exits at the open (gap through
            # stop realized in full); otherwise exits at the stop level.
            exit_price = open_ if open_ <= setup.stop else setup.stop
            return OutcomeResult(
                status=BARRIER_STOP,
                fill_date=fill_date, fill_price=fill_price,
                exit_date=expected_date, exit_price=exit_price,
                first_barrier_hit=BARRIER_STOP,
                r_multiple=_r_multiple(fill_price, exit_price, risk, slippage),
                mae_r=mae_r, mfe_r=mfe_r, sessions_held=sessions_held,
                target_touched_on_fill_session=target_touched_on_fill_session,
            )

        if high >= setup.target:
            # A gap above target is not credited -- exit is always the
            # target itself, never the open.
            exit_price = setup.target
            return OutcomeResult(
                status=BARRIER_TARGET,
                fill_date=fill_date, fill_price=fill_price,
                exit_date=expected_date, exit_price=exit_price,
                first_barrier_hit=BARRIER_TARGET,
                r_multiple=_r_multiple(fill_price, exit_price, risk, slippage),
                mae_r=mae_r, mfe_r=mfe_r, sessions_held=sessions_held,
                target_touched_on_fill_session=target_touched_on_fill_session,
            )

        if i == params.timeout_sessions:
            exit_price = close
            return OutcomeResult(
                status=BARRIER_TIMEOUT_30,
                fill_date=fill_date, fill_price=fill_price,
                exit_date=expected_date, exit_price=exit_price,
                first_barrier_hit=BARRIER_TIMEOUT_30,
                r_multiple=_r_multiple(fill_price, exit_price, risk, slippage),
                mae_r=mae_r, mfe_r=mfe_r, sessions_held=sessions_held,
                target_touched_on_fill_session=target_touched_on_fill_session,
            )

    # Ran out of data before reaching resolution or the 30th expected
    # post-fill session -- never guess what would have happened.
    return OutcomeResult(
        status=OPEN_AT_END,
        fill_date=fill_date,
        fill_price=fill_price,
        target_touched_on_fill_session=target_touched_on_fill_session,
    )
