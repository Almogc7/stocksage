"""
Watchlist eligibility evaluator — dry-run (Phase 4) and apply (Phase 5) of
the dynamic watchlist lifecycle (see WATCHLIST_AND_ALERTS_DESIGN.md).

What this module DOES:
  - Reads the current watchlist universe from SQLite.
  - Selects which symbols are eligible for evaluation this run, recording a
    skip_reason for every symbol that isn't.
  - Fetches/validates market data via data.market_data_validator.MarketDataClient.
  - Computes the relevance score via analyzers.eligibility (unchanged) and
    simulates the existing hysteresis state machine, including the ACTIVE
    cap (30), bank cap (8), and replacement margin (5).
  - Detects a broad yfinance outage and suppresses mass demotion proposals
    when one is detected.
  - Records an evaluation_runs row (Phase 2) every run.
  - In **apply mode** (`apply=True`), writes the computed changes to the
    watchlist table in one atomic transaction (db.apply_evaluation_changes).
    In **dry-run mode** (the default), it never does.

Dry-run vs apply (Phase 5):
  `run_watchlist_evaluation(apply=False)` (alias: `run_dry_run_evaluation()`)
  computes and reports proposed changes only — identical to Phase 4,
  unchanged. `run_watchlist_evaluation(apply=True)` runs the exact same
  computation and then persists it: relevance_score, last_evaluated,
  hysteresis counters, wl_state transitions, last_promoted/last_demoted,
  exclusion_reason, and reeval_date. USER_REMOVED and ETF_INDEX_CONTEXT
  rows are never touched in either mode — they are never gathered as
  candidates in the first place (see universe selection below).

Why a symbol's first real apply run usually promotes nobody:
  PROMOTION_CONSEC_REQUIRED = 2, and a database that has never had a live
  eligibility pass starts every symbol at consec_promote_count = 0. The
  first apply run can only bring qualifying MONITOR symbols to
  consec_promote_count = 1 — actual promotion to ACTIVE requires a SECOND
  consecutive qualifying evaluation (i.e. a second apply run after this
  one, on a later evaluation cycle). This is expected hysteresis warm-up
  behavior, not a bug — see WATCHLIST_LIVE_DRY_RUN_REPORT.md (Phase 4.5)
  for the live validation that first surfaced this.

What this module does NOT do:
  - Schedule anything, or send Telegram messages.
  - Modify analyzers/eligibility.py's existing scoring/hysteresis contract.
  - Add new schema columns. `data_timestamp_utc`/`provider_status` per
    symbol are not persisted (no such columns exist yet) — `last_evaluated`
    and `exclusion_reason` serve the equivalent bookkeeping role.

Recovery semantics (TEMPORARILY_INELIGIBLE -> MONITOR):
  analyzers.eligibility.determine_state_change() has no recovery branch —
  it only ever returns ('TEMPORARILY_INELIGIBLE', 'no change') for a
  TEMPORARILY_INELIGIBLE symbol with now-valid data, because its promotion
  branch only triggers from current_state == 'MONITOR'. Per the Phase 4
  spec, a recovered symbol must land in MONITOR, not be promoted directly
  to ACTIVE in the same cycle — that recovery transition is implemented
  here, in this module, rather than changing the shared, already-tested
  determine_state_change() contract used elsewhere.

Provider-outage suppression (documented scope):
  Only ACTIVE -> MONITOR demotions caused by a genuinely low relevance
  score are suppressed when the run is provider-degraded. Data-quality
  failures (STALE_DATA, MISSING_OHLCV, etc.) are treated as symbol-specific
  and are NOT suppressed, because the spec's outage definition is scoped
  to RATE_LIMITED/PROVIDER_ERROR/TEMPORARY_FAILURE specifically, not data
  quality statuses. A single symbol's own provider_transient failure never
  changes its own state (that was already true before any aggregate
  degraded-run detection) — the aggregate check only affects whether OTHER
  symbols' legitimate low-score demotions get suppressed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import db.database as db
from analyzers.eligibility import (
    classify_security_type,
    determine_state_change,
    evaluate_symbol_eligibility,
)
from analyzers.technical import full_analysis
from config import (
    ACTIVE_BANK_MAX,
    ACTIVE_MAX_SIZE,
    BANK_CATEGORIES,
    DEMOTION_THRESHOLD,
    MARKET_DATA_DATA_QUALITY_RETRY_HOURS,
    PROMOTION_THRESHOLD,
    RSI_VETO_MAX,
    RSI_VETO_MIN,
    WATCHLIST_PROVIDER_OUTAGE_THRESHOLD_PCT,
)
from data.market_data_validator import MarketDataClient, ProviderStatus

_NON_STOCK_TYPES = frozenset({"etf", "index", "crypto"})

# Sentinel score used only for the internal _ActiveTracker bookkeeping of
# symbols kept ACTIVE due to a transient provider failure this run (we have
# no real score for them). High enough that they are never picked as the
# "lowest" candidate for a same-run replacement eviction.
_PROTECTED_SCORE = 999


def _safe_avg_volume(value) -> int:
    """NaN/inf/None-safe conversion to int for compute_relevance_score's
    avg_volume parameter — NaN must never propagate into the final score."""
    if value is None:
        return 0
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0
    if v != v or v in (float("inf"), float("-inf")):  # NaN check without importing math
        return 0
    return int(round(v))


@dataclass
class SymbolEvalResult:
    symbol: str
    current_state: str | None = None
    proposed_state: str | None = None
    provider_status: str | None = None
    security_type: str | None = None
    relevance_score: int | None = None
    hard_eligibility_passed: bool | None = None
    reason: str = ""
    data_timestamp_utc: str | None = None
    latest_completed_candle_date: str | None = None
    average_daily_volume: float | None = None
    average_daily_dollar_volume: float | None = None
    skip_reason: str | None = None
    failure_reason: str | None = None

    # Apply-mode bookkeeping only — the real watchlist column values this
    # symbol would get written in apply mode. None means "do not write
    # anything for this symbol" (skipped, or a transient provider failure
    # we must leave untouched). Not part of the dry-run reporting contract.
    db_fields: dict | None = field(default=None, repr=False, compare=False)
    prev_fields: dict | None = field(default=None, repr=False, compare=False)
    change_type: str | None = field(default=None, repr=False, compare=False)


@dataclass
class DryRunEvaluationResult:
    run_id: int | None
    dry_run: bool = True
    applied: bool = False
    started_at: str = ""
    completed_at: str = ""
    duration_seconds: float = 0.0

    total_symbols_considered: int = 0
    total_symbols_evaluated: int = 0
    total_symbols_skipped: int = 0
    total_symbols_failed: int = 0

    active_before: int = 0
    active_after: int = 0
    monitor_before: int = 0
    monitor_after: int = 0
    context_count: int = 0
    temporarily_ineligible_before: int = 0
    temporarily_ineligible_after: int = 0
    user_removed_count: int = 0

    proposed_promotions: list[str] = field(default_factory=list)
    proposed_demotions: list[str] = field(default_factory=list)
    proposed_ineligible: list[str] = field(default_factory=list)
    proposed_recoveries: list[str] = field(default_factory=list)

    provider_error_count: int = 0
    stale_data_count: int = 0
    invalid_symbol_count: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    yfinance_request_count: int = 0

    provider_degraded: bool = False
    symbol_results: list[SymbolEvalResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fatal_error: str | None = None

    # Kept for naming-convention compatibility with the Phase 4 spec wording.
    @property
    def proposed_active_count(self) -> int:
        return self.active_after

    @property
    def proposed_monitor_count(self) -> int:
        return self.monitor_after

    @property
    def proposed_temporarily_ineligible_count(self) -> int:
        return self.temporarily_ineligible_after


class _ActiveTracker:
    """Tracks the simulated proposed ACTIVE set as decisions are made."""

    def __init__(self) -> None:
        self.scores: dict[str, int] = {}
        self.bank_members: set[str] = set()

    def add(self, symbol: str, score: int, is_bank: bool) -> None:
        self.scores[symbol] = score
        if is_bank:
            self.bank_members.add(symbol)

    def remove(self, symbol: str) -> None:
        self.scores.pop(symbol, None)
        self.bank_members.discard(symbol)

    @property
    def count(self) -> int:
        return len(self.scores)

    @property
    def bank_count(self) -> int:
        return len(self.bank_members)

    @property
    def lowest_score(self) -> int | None:
        return min(self.scores.values()) if self.scores else None

    def lowest_symbol(self) -> str | None:
        if not self.scores:
            return None
        # Deterministic tie-break: lowest score, then alphabetically first.
        return min(self.scores.items(), key=lambda kv: (kv[1], kv[0]))[0]


def _is_bank(categories: list[str]) -> bool:
    return any(cat in BANK_CATEGORIES for cat in categories)


def _price_data_from_result(market_result) -> dict:
    return {
        "price": market_result.latest_close or 0.0,
        "volume": market_result.latest_volume or 0,
    }


def _build_db_fields(
    row: dict, market_result, score: int, current_state: str,
    proposed_state: str, reason: str, now_str: str,
) -> dict:
    """
    Compute the real watchlist column values to persist for one evaluated
    symbol in apply mode, given its final proposed_state.

    determine_state_change() is a pure function that deliberately never
    writes to the DB ("callers are responsible for persistence" — see its
    docstring in analyzers/eligibility.py). This is that persistence layer:
    it mirrors the same promotion/demotion threshold semantics to decide
    hysteresis counter increments when no transition happens yet, and
    resets/stamps timestamps when one does.

    Always marks wl_classified=1 — once a symbol has been through a real
    apply pass, the startup classifier (Phase 1) must never touch it again.
    """
    fields: dict = {
        "symbol": row["symbol"],
        "relevance_score": score,
        "security_type": row["security_type"],
        "last_evaluated": now_str,
        "wl_classified": 1,
    }

    if proposed_state == current_state:
        if current_state == "MONITOR":
            if score >= PROMOTION_THRESHOLD:
                fields["consec_promote_count"] = row["consec_promote_count"] + 1
                fields["consec_demote_count"] = 0
            else:
                fields["consec_promote_count"] = 0
        elif current_state == "ACTIVE":
            if score < DEMOTION_THRESHOLD:
                fields["consec_demote_count"] = row["consec_demote_count"] + 1
                fields["consec_promote_count"] = 0
            else:
                fields["consec_demote_count"] = 0
            fields["dwell_days"] = row["dwell_days"] + 1
        fields["exclusion_reason"] = ""
        return fields

    # A real transition: reset the streak counters and dwell time, and
    # stamp the appropriate timestamp.
    fields["wl_state"] = proposed_state
    fields["consec_promote_count"] = 0
    fields["consec_demote_count"] = 0
    fields["dwell_days"] = 0

    if proposed_state == "ACTIVE":
        fields["last_promoted"] = now_str
        fields["exclusion_reason"] = ""
        fields["reeval_date"] = None
    elif proposed_state == "MONITOR":
        if current_state == "ACTIVE":
            fields["last_demoted"] = now_str
        fields["exclusion_reason"] = ""
        fields["reeval_date"] = None
    elif proposed_state == "TEMPORARILY_INELIGIBLE":
        fields["exclusion_reason"] = reason
        retry_date = None
        if market_result is not None and market_result.retry_after_utc:
            retry_date = market_result.retry_after_utc.split(" ")[0]
        if retry_date is None:
            retry_date = (
                datetime.now(timezone.utc) + timedelta(hours=MARKET_DATA_DATA_QUALITY_RETRY_HOURS)
            ).date().isoformat()
        fields["reeval_date"] = retry_date

    return fields


def _classify_change_type(current_state: str, proposed_state: str, fields: dict) -> str:
    """Audit change_type for one symbol's apply-mode write (Phase 5.5)."""
    if proposed_state == "ACTIVE" and current_state != "ACTIVE":
        return "promotion"
    if current_state == "ACTIVE" and proposed_state == "MONITOR":
        return "demotion"
    if proposed_state == "TEMPORARILY_INELIGIBLE":
        return "ineligible"
    if current_state == "TEMPORARILY_INELIGIBLE" and proposed_state == "MONITOR":
        return "recovery"
    if "consec_promote_count" in fields or "consec_demote_count" in fields:
        return "counter_update"
    return "score_update"


def _set_db_fields(
    sr: SymbolEvalResult, row: dict, market_result, score: int,
    current_state: str, proposed_state: str, reason: str, now_str: str,
) -> None:
    """
    Compute and attach this symbol's apply-mode persistence fields
    (db_fields), their previous values (prev_fields, for rollback), and an
    audit change_type — onto its SymbolEvalResult. Harmless to compute in
    dry-run mode too (nothing reads these fields unless apply=True).
    """
    fields = _build_db_fields(row, market_result, score, current_state, proposed_state, reason, now_str)
    sr.db_fields = fields
    sr.prev_fields = {k: row.get(k) for k in fields if k != "symbol" and k in row}
    sr.change_type = _classify_change_type(current_state, proposed_state, fields)


def run_dry_run_evaluation(
    *,
    client: MarketDataClient | None = None,
    triggered_by: str = "manual",
    now: datetime | None = None,
) -> DryRunEvaluationResult:
    """Backwards-compatible alias for run_watchlist_evaluation(apply=False)."""
    return run_watchlist_evaluation(apply=False, client=client, triggered_by=triggered_by, now=now)


def run_watchlist_evaluation(
    *,
    apply: bool = False,
    client: MarketDataClient | None = None,
    triggered_by: str = "manual",
    now: datetime | None = None,
    run_type: str | None = None,
    extra_metadata: dict | None = None,
) -> DryRunEvaluationResult:
    """
    Run one full eligibility evaluation pass.

    apply=False (default, dry-run): computes and reports proposed changes
    only — never writes to the watchlist table.

    apply=True: runs the identical computation, then writes every
    evaluated symbol's resulting state/score/counters to the watchlist
    table in ONE atomic transaction (db.apply_evaluation_changes). If that
    write fails, the transaction rolls back entirely (handled inside
    apply_evaluation_changes) and this function marks the evaluation_runs
    row 'failed' — no partial state survives a failed apply.

    run_type defaults to "manual" if apply else "dry_run"; pass
    run_type="scheduled" (services/watchlist_scheduler.py does) to tag a
    run as scheduler-triggered regardless of its apply/dry-run flavor —
    the dry_run column already captures that orthogonally.

    extra_metadata is merged into the evaluation_runs metadata_json (e.g.
    the scheduler's market_date / schedule_reason) without this function
    needing to know what the caller wants to record.

    Always records an evaluation_runs row (dry_run=not apply).
    """
    client = client or MarketDataClient()
    now = now or datetime.now(timezone.utc)
    real_start = datetime.now(timezone.utc)  # wall clock, for genuine duration measurement
    started_at = now.strftime("%Y-%m-%d %H:%M:%S")
    today_str = now.date().isoformat()

    summary_before = db.get_watchlist_summary()
    run_type = run_type or ("manual" if apply else "dry_run")
    run_id = db.create_evaluation_run(
        run_type, dry_run=not apply, triggered_by=triggered_by, started_at=started_at,
        active_before=summary_before.get("ACTIVE", 0),
        monitor_before=summary_before.get("MONITOR", 0),
        context_count=summary_before.get("ETF_INDEX_CONTEXT", 0),
        ineligible_before=summary_before.get("TEMPORARILY_INELIGIBLE", 0),
        user_removed_count=summary_before.get("USER_REMOVED", 0),
    )

    result = DryRunEvaluationResult(
        run_id=run_id,
        dry_run=not apply,
        started_at=started_at,
        active_before=summary_before.get("ACTIVE", 0),
        monitor_before=summary_before.get("MONITOR", 0),
        context_count=summary_before.get("ETF_INDEX_CONTEXT", 0),
        temporarily_ineligible_before=summary_before.get("TEMPORARILY_INELIGIBLE", 0),
        user_removed_count=summary_before.get("USER_REMOVED", 0),
    )

    try:
        _run(result, client, today_str, now)
        if apply:
            changed = [sr for sr in result.symbol_results if sr.db_fields is not None]
            updates = [sr.db_fields for sr in changed]
            audit_entries = [
                {
                    "run_id": run_id,
                    "symbol": sr.symbol,
                    "change_type": sr.change_type,
                    "previous_values_json": json.dumps(sr.prev_fields, default=str),
                    "new_values_json": json.dumps(
                        {k: v for k, v in sr.db_fields.items() if k != "symbol"}, default=str
                    ),
                    "changed_columns_json": json.dumps(
                        [k for k in sr.db_fields if k != "symbol"]
                    ),
                    "created_at": started_at,
                    "dry_run": False,
                    "triggered_by": triggered_by,
                }
                for sr in changed
            ]
            db.apply_evaluation_changes(updates, audit_entries)
            result.applied = True
    except Exception as exc:  # fatal error — never leave a half-applied run
        result.fatal_error = f"{type(exc).__name__}: {exc}"
        real_elapsed = (datetime.now(timezone.utc) - real_start).total_seconds()
        completed = now + timedelta(seconds=real_elapsed)
        result.completed_at = completed.strftime("%Y-%m-%d %H:%M:%S")
        result.duration_seconds = real_elapsed
        db.update_evaluation_run_failure(run_id, result.fatal_error, completed_at=result.completed_at)
        return result

    real_elapsed = (datetime.now(timezone.utc) - real_start).total_seconds()
    completed = now + timedelta(seconds=real_elapsed)
    result.completed_at = completed.strftime("%Y-%m-%d %H:%M:%S")
    result.duration_seconds = real_elapsed

    counts = dict(
        total_symbols_considered=result.total_symbols_considered,
        total_symbols_evaluated=result.total_symbols_evaluated,
        total_symbols_skipped=result.total_symbols_skipped,
        total_symbols_failed=result.total_symbols_failed,
        active_after=result.active_after,
        monitor_after=result.monitor_after,
        ineligible_after=result.temporarily_ineligible_after,
        promotions_count=len(result.proposed_promotions),
        demotions_count=len(result.proposed_demotions),
        recovered_count=len(result.proposed_recoveries),
        newly_ineligible_count=len(result.proposed_ineligible),
        provider_error_count=result.provider_error_count,
        stale_data_count=result.stale_data_count,
        invalid_symbol_count=result.invalid_symbol_count,
        cache_hits=result.cache_hits,
        cache_misses=result.cache_misses,
        yfinance_request_count=result.yfinance_request_count,
    )
    metadata = {
        "applied": result.applied,
        "proposed_promotions": result.proposed_promotions,
        "proposed_demotions": result.proposed_demotions,
        "proposed_ineligible": result.proposed_ineligible,
        "proposed_recoveries": result.proposed_recoveries,
        "provider_degraded": result.provider_degraded,
        "warnings": result.warnings,
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    if result.provider_degraded:
        db.update_evaluation_run_partial_failure(
            run_id, "provider degraded: demotion proposals suppressed for this run",
            metadata=metadata, completed_at=result.completed_at, **counts,
        )
    else:
        db.update_evaluation_run_success(run_id, metadata=metadata, completed_at=result.completed_at, **counts)

    return result


def _run(
    result: DryRunEvaluationResult, client: MarketDataClient, today_str: str, now: datetime
) -> None:
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    active_symbols = db.get_symbols_by_state("ACTIVE")
    monitor_symbols = db.get_symbols_by_state("MONITOR")
    unclassified_symbols = db.get_unclassified_symbols()
    ineligible_symbols = db.get_symbols_by_state("TEMPORARILY_INELIGIBLE")

    result.total_symbols_considered = (
        len(active_symbols) + len(monitor_symbols) + len(unclassified_symbols) + len(ineligible_symbols)
    )

    candidates: list[dict] = []  # rows (get_symbol_status dicts) to fetch+evaluate

    for sym in active_symbols + monitor_symbols:
        row = db.get_symbol_status(sym)
        if row["security_type"] in _NON_STOCK_TYPES:
            result.symbol_results.append(SymbolEvalResult(
                symbol=sym, current_state=row["wl_state"], proposed_state=row["wl_state"],
                security_type=row["security_type"],
                skip_reason=f"security_type={row['security_type']} is not a stock candidate despite wl_state={row['wl_state']}",
            ))
            result.total_symbols_skipped += 1
            continue
        candidates.append(row)

    for sym in unclassified_symbols:
        row = db.get_symbol_status(sym)
        sec_type = classify_security_type(sym)
        if sec_type in _NON_STOCK_TYPES:
            result.symbol_results.append(SymbolEvalResult(
                symbol=sym, current_state=None, proposed_state=None, security_type=sec_type,
                skip_reason=f"unclassified symbol resolves to security_type={sec_type}, not a stock candidate",
            ))
            result.total_symbols_skipped += 1
            continue
        row["security_type"] = sec_type
        row["wl_state"] = "MONITOR"  # simulation baseline for a never-classified stock
        candidates.append(row)

    for sym in ineligible_symbols:
        row = db.get_symbol_status(sym)
        reeval = row.get("reeval_date")
        if reeval and str(reeval) > today_str:
            result.symbol_results.append(SymbolEvalResult(
                symbol=sym, current_state="TEMPORARILY_INELIGIBLE", proposed_state="TEMPORARILY_INELIGIBLE",
                security_type=row["security_type"],
                skip_reason=f"temporarily ineligible, retry not due until {reeval}",
            ))
            result.total_symbols_skipped += 1
            continue
        candidates.append(row)

    result.total_symbols_evaluated = len(candidates)

    if not candidates:
        result.active_after = result.active_before
        result.monitor_after = result.monitor_before
        result.temporarily_ineligible_after = result.temporarily_ineligible_before
        return

    rows_by_symbol = {row["symbol"]: row for row in candidates}
    security_types = {sym: row["security_type"] for sym, row in rows_by_symbol.items()}
    market_results = client.validate_batch(list(rows_by_symbol.keys()), security_types=security_types)

    result.cache_hits = client.cache_hits
    result.cache_misses = client.cache_misses
    result.yfinance_request_count = client.yfinance_request_count
    result.provider_error_count = client.provider_error_count

    transient_failures = sum(
        1 for r in market_results.values()
        if r.provider_status in (ProviderStatus.RATE_LIMITED, ProviderStatus.PROVIDER_ERROR, ProviderStatus.TEMPORARY_FAILURE)
    )
    outage_ratio = transient_failures / len(candidates) if candidates else 0.0
    result.provider_degraded = outage_ratio >= WATCHLIST_PROVIDER_OUTAGE_THRESHOLD_PCT
    if result.provider_degraded:
        result.warnings.append(
            f"provider degraded: {transient_failures}/{len(candidates)} symbols "
            f"({outage_ratio:.0%}) returned transient provider errors this run; "
            "mass demotion proposals suppressed"
        )

    tracker = _ActiveTracker()
    results_by_symbol: dict[str, SymbolEvalResult] = {}

    active_candidates = sorted(
        (row for row in candidates if row["wl_state"] == "ACTIVE"), key=lambda r: r["symbol"]
    )
    pending_promotion: list[tuple[int, str]] = []  # (score, symbol) for pass 2, sorted later

    for row in active_candidates:
        sym = row["symbol"]
        sr = _build_base_result(sym, row, market_results[sym])
        if market_results[sym].provider_status != ProviderStatus.OK:
            _apply_failure_outcome(sr, row, market_results[sym], result, now_str)
            results_by_symbol[sym] = sr
            if sr.proposed_state == "ACTIVE":
                # Transient provider failure: symbol stays ACTIVE this run.
                # Use a protected sentinel score so it's never picked as the
                # "lowest" candidate for a same-run replacement eviction —
                # we have no real score for it, so it must not be penalized.
                tracker.add(sym, _PROTECTED_SCORE, _is_bank(row["categories"]))
            continue

        df, _ = client.get_history(sym)
        eval_out = evaluate_symbol_eligibility(
            sym, _price_data_from_result(market_results[sym]), df,
            _safe_avg_volume(market_results[sym].average_daily_volume),
            current_state="ACTIVE",
            consec_promote=row["consec_promote_count"], consec_demote=row["consec_demote_count"],
            dwell_days=row["dwell_days"], security_type=row["security_type"],
            is_bank=_is_bank(row["categories"]), active_count=0, active_bank_count=0,
            lowest_active_score=None,
        )
        sr.relevance_score = eval_out["score"]
        sr.hard_eligibility_passed = eval_out["new_state"] != "TEMPORARILY_INELIGIBLE"
        new_state = eval_out["new_state"]
        reason = eval_out["reason"]

        if new_state == "MONITOR" and result.provider_degraded:
            new_state = "ACTIVE"
            reason = f"provider degraded this run; demotion suppressed (would have been: {eval_out['reason']})"

        sr.proposed_state = new_state
        sr.reason = reason
        _set_db_fields(sr, row, market_results[sym], sr.relevance_score, "ACTIVE", new_state, reason, now_str)
        if new_state == "ACTIVE":
            tracker.add(sym, sr.relevance_score, _is_bank(row["categories"]))
        elif new_state == "MONITOR":
            result.proposed_demotions.append(sym)
        elif new_state == "TEMPORARILY_INELIGIBLE":
            result.proposed_ineligible.append(sym)
        results_by_symbol[sym] = sr

    monitor_candidates = sorted(
        (row for row in candidates if row["wl_state"] == "MONITOR"), key=lambda r: r["symbol"]
    )

    for row in monitor_candidates:
        sym = row["symbol"]
        mr = market_results[sym]
        sr = _build_base_result(sym, row, mr)

        if mr.provider_status != ProviderStatus.OK:
            _apply_failure_outcome(sr, row, mr, result, now_str)
            results_by_symbol[sym] = sr
            continue

        df, _ = client.get_history(sym)
        eval_out = evaluate_symbol_eligibility(
            sym, _price_data_from_result(mr), df,
            _safe_avg_volume(mr.average_daily_volume),
            current_state="MONITOR",
            consec_promote=row["consec_promote_count"], consec_demote=row["consec_demote_count"],
            dwell_days=row["dwell_days"], security_type=row["security_type"],
            is_bank=_is_bank(row["categories"]), active_count=0, active_bank_count=0,
            lowest_active_score=None,
        )
        sr.relevance_score = eval_out["score"]
        sr.hard_eligibility_passed = eval_out["new_state"] != "TEMPORARILY_INELIGIBLE"

        if eval_out["new_state"] == "ACTIVE":
            pending_promotion.append((sr.relevance_score, sym))
            sr.proposed_state = "MONITOR"  # provisional; resolved (incl. db_fields) in pass 2 below
            sr.reason = "pending promotion-cap resolution"
        elif eval_out["new_state"] == "TEMPORARILY_INELIGIBLE":
            sr.proposed_state = "TEMPORARILY_INELIGIBLE"
            sr.reason = eval_out["reason"]
            _set_db_fields(sr, row, mr, sr.relevance_score, "MONITOR", "TEMPORARILY_INELIGIBLE", sr.reason, now_str)
            result.proposed_ineligible.append(sym)
        else:
            sr.proposed_state = "MONITOR"
            sr.reason = eval_out["reason"]
            _set_db_fields(sr, row, mr, sr.relevance_score, "MONITOR", "MONITOR", sr.reason, now_str)
        results_by_symbol[sym] = sr

    # TEMPORARILY_INELIGIBLE candidates whose retry time has arrived. A
    # successful re-fetch recovers them to MONITOR only — never a direct
    # promotion to ACTIVE in the same cycle (see module docstring).
    ineligible_due_candidates = sorted(
        (row for row in candidates if row["symbol"] in ineligible_symbols), key=lambda r: r["symbol"]
    )
    for row in ineligible_due_candidates:
        sym = row["symbol"]
        mr = market_results[sym]
        sr = _build_base_result(sym, row, mr)

        if mr.provider_status != ProviderStatus.OK:
            _apply_failure_outcome(sr, row, mr, result, now_str)
            results_by_symbol[sym] = sr
            continue

        df, _ = client.get_history(sym)
        eval_out = evaluate_symbol_eligibility(
            sym, _price_data_from_result(mr), df,
            _safe_avg_volume(mr.average_daily_volume),
            current_state="MONITOR", consec_promote=0, consec_demote=0, dwell_days=0,
            security_type=row["security_type"], is_bank=_is_bank(row["categories"]),
            active_count=0, active_bank_count=0, lowest_active_score=None,
        )
        sr.relevance_score = eval_out["score"]
        sr.hard_eligibility_passed = True
        sr.proposed_state = "MONITOR"
        sr.reason = "recovered: data valid again; returning to MONITOR for normal promotion cycle"
        _set_db_fields(sr, row, mr, sr.relevance_score, "TEMPORARILY_INELIGIBLE", "MONITOR", sr.reason, now_str)
        result.proposed_recoveries.append(sym)
        results_by_symbol[sym] = sr

    # Note: unclassified-stock candidates had wl_state forced to "MONITOR"
    # when gathered above, so they are already included in monitor_candidates
    # and handled by the loop above with identical logic.

    # Pass 2: resolve promotions against the live tracker, highest score
    # first, alphabetical tie-break — deterministic and respects the
    # ACTIVE cap, bank cap, and replacement margin.
    pending_promotion.sort(key=lambda t: (-t[0], t[1]))
    for score, sym in pending_promotion:
        row = rows_by_symbol[sym]
        sr = results_by_symbol[sym]
        new_state, reason = determine_state_change(
            current_state="MONITOR", new_score=score,
            consec_promote=row["consec_promote_count"], consec_demote=row["consec_demote_count"],
            dwell_days=row["dwell_days"], security_type=row["security_type"],
            price_data=_price_data_from_result(market_results[sym]),
            avg_volume=_safe_avg_volume(market_results[sym].average_daily_volume),
            is_bank=_is_bank(row["categories"]),
            active_count=tracker.count, active_bank_count=tracker.bank_count,
            lowest_active_score=tracker.lowest_score,
        )
        if new_state == "ACTIVE":
            if tracker.count >= ACTIVE_MAX_SIZE:
                evicted_symbol = tracker.lowest_symbol()
                tracker.remove(evicted_symbol)
                evicted_result = results_by_symbol.get(evicted_symbol)
                if evicted_result is not None:
                    evicted_result.proposed_state = "MONITOR"
                    evicted_result.reason = f"replaced by higher-scoring candidate {sym} (score {score})"
                    evicted_row = rows_by_symbol[evicted_symbol]
                    _set_db_fields(
                        evicted_result, evicted_row, market_results[evicted_symbol],
                        evicted_result.relevance_score, "ACTIVE", "MONITOR", evicted_result.reason, now_str,
                    )
                    if evicted_symbol not in result.proposed_demotions:
                        result.proposed_demotions.append(evicted_symbol)
            tracker.add(sym, score, _is_bank(row["categories"]))
            sr.proposed_state = "ACTIVE"
            sr.reason = reason
            _set_db_fields(sr, row, market_results[sym], score, "MONITOR", "ACTIVE", reason, now_str)
            result.proposed_promotions.append(sym)
        else:
            sr.proposed_state = new_state
            sr.reason = reason
            _set_db_fields(sr, row, market_results[sym], score, "MONITOR", new_state, reason, now_str)

    result.symbol_results.extend(results_by_symbol.values())
    result.active_after = tracker.count
    result.monitor_after = sum(1 for r in results_by_symbol.values() if r.proposed_state == "MONITOR") + sum(
        1 for r in result.symbol_results if r.skip_reason and r.current_state == "MONITOR"
    )
    result.temporarily_ineligible_after = sum(
        1 for r in results_by_symbol.values() if r.proposed_state == "TEMPORARILY_INELIGIBLE"
    ) + sum(
        1 for r in result.symbol_results
        if r.skip_reason and r.current_state == "TEMPORARILY_INELIGIBLE"
    )

    if tracker.count > ACTIVE_MAX_SIZE:
        result.warnings.append(
            f"proposed ACTIVE count {tracker.count} exceeds cap {ACTIVE_MAX_SIZE} — "
            "current live ACTIVE list already violates the configured limit; no state was changed"
        )
    if tracker.bank_count > ACTIVE_BANK_MAX:
        result.warnings.append(
            f"proposed ACTIVE bank count {tracker.bank_count} exceeds cap {ACTIVE_BANK_MAX} — "
            "current live ACTIVE list already violates the configured bank limit; no state was changed"
        )


def _build_base_result(symbol: str, row: dict, market_result) -> SymbolEvalResult:
    return SymbolEvalResult(
        symbol=symbol,
        current_state=row["wl_state"],
        security_type=row["security_type"],
        provider_status=market_result.provider_status.value,
        data_timestamp_utc=market_result.data_timestamp_utc,
        latest_completed_candle_date=market_result.latest_completed_candle_date,
        average_daily_volume=market_result.average_daily_volume,
        average_daily_dollar_volume=market_result.average_daily_dollar_volume,
    )


def _apply_failure_outcome(
    sr: SymbolEvalResult, row: dict, market_result, result: DryRunEvaluationResult, now_str: str
) -> None:
    result.total_symbols_failed += 1
    sr.relevance_score = 0
    sr.hard_eligibility_passed = False
    sr.failure_reason = market_result.failure_reason

    if market_result.provider_status == ProviderStatus.STALE_DATA:
        result.stale_data_count += 1
    if market_result.provider_status == ProviderStatus.INVALID_SYMBOL:
        result.invalid_symbol_count += 1

    current_state = row["wl_state"]
    if market_result.failure_type == "provider_transient":
        sr.proposed_state = current_state
        sr.reason = f"provider transient failure ({market_result.provider_status.value}); no change proposed this run"
        # db_fields stays None: a transient provider blip must never be
        # written — not even a refreshed last_evaluated/score — so the
        # symbol's real last-known-good data is left completely untouched.
    elif current_state == "TEMPORARILY_INELIGIBLE":
        sr.proposed_state = "TEMPORARILY_INELIGIBLE"
        sr.reason = f"still ineligible: {market_result.failure_reason}"
        _set_db_fields(
            sr, row, market_result, 0, "TEMPORARILY_INELIGIBLE", "TEMPORARILY_INELIGIBLE", sr.reason, now_str
        )
    else:
        sr.proposed_state = "TEMPORARILY_INELIGIBLE"
        sr.reason = f"data-quality failure: {market_result.failure_reason}"
        _set_db_fields(sr, row, market_result, 0, current_state, "TEMPORARILY_INELIGIBLE", sr.reason, now_str)
        result.proposed_ineligible.append(sr.symbol)


class RollbackError(Exception):
    """Raised when rollback_evaluation_run() refuses to proceed (unknown run,
    dry-run run, already rolled back). Conflicts are NOT raised as an
    exception — they're returned in the result dict, since a conflict is an
    expected, reportable outcome rather than a programming error."""


def _values_differ(a, b) -> bool:
    # SQLite round-trips ints/floats/strings/None faithfully; a plain
    # inequality check is sufficient and avoids any false-positive coercion.
    return a != b


def rollback_evaluation_run(run_id: int, *, triggered_by: str = "manual") -> dict:
    """
    Roll back one successful apply-mode evaluation run (Phase 5.5).

    Steps (see module docstring / CLAUDE_CHANGES.md Entry for full spec):
      1. Load the run and its audit rows (evaluation_run_changes).
      2. Refuse unknown run_id, dry-run run_id, or an already-rolled-back run.
      3. For every audited symbol, compare its CURRENT watchlist values
         against the audit row's new_values — if anything differs (a manual
         edit, or a later run, touched it since), abort the ENTIRE rollback
         with status='conflict' and report every conflicting symbol. No
         partial rollback is ever written.
      4. If there are no conflicts, restore every symbol's previous_values
         and mark every audit row rolled_back in ONE atomic transaction
         (db.apply_rollback).
      5. Record a new evaluation_runs row representing the rollback action
         itself, tagged in metadata_json with the run_id it rolled back.

    Returns a dict: {"status": "success"|"conflict"|"noop", "run_id": ...,
    "rollback_run_id": ..., "restored_symbols": [...], "conflicts": [...]}.
    """
    run = db.get_evaluation_run(run_id)
    if run is None:
        raise RollbackError(f"No evaluation run with id {run_id}")
    if run["dry_run"]:
        raise RollbackError(f"Run {run_id} was a dry-run — nothing was ever written, nothing to roll back")

    changes = db.get_changes_for_run(run_id)
    if not changes:
        return {"status": "noop", "run_id": run_id, "restored_symbols": [], "conflicts": []}

    if any(c["rollback_status"] == "rolled_back" for c in changes):
        raise RollbackError(f"Run {run_id} has already been rolled back")

    conflicts = []
    restores = []
    for change in changes:
        current = db.get_symbol_status(change["symbol"])
        new_values = json.loads(change["new_values_json"])
        changed_columns = json.loads(change["changed_columns_json"])
        mismatched = {
            col: {"expected": new_values.get(col), "actual": current.get(col)}
            for col in changed_columns
            if _values_differ(current.get(col), new_values.get(col))
        }
        if mismatched:
            conflicts.append({"symbol": change["symbol"], "mismatched_columns": mismatched})
        else:
            previous_values = json.loads(change["previous_values_json"])
            restores.append({"symbol": change["symbol"], **previous_values})

    if conflicts:
        return {"status": "conflict", "run_id": run_id, "restored_symbols": [], "conflicts": conflicts}

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    restored_symbols = [r["symbol"] for r in restores]
    rollback_run_id = db.create_evaluation_run(
        "manual", dry_run=False, triggered_by=triggered_by,
        metadata={"rollback_of_run_id": run_id},
    )
    try:
        db.apply_rollback(run_id, restores, rollback_run_id, now_str)
    except Exception as exc:
        db.update_evaluation_run_failure(rollback_run_id, f"{type(exc).__name__}: {exc}")
        raise

    db.update_evaluation_run_success(
        rollback_run_id,
        metadata={"rollback_of_run_id": run_id, "restored_symbols": restored_symbols},
    )
    return {
        "status": "success", "run_id": run_id, "rollback_run_id": rollback_run_id,
        "restored_symbols": restored_symbols, "conflicts": [],
    }


def explain_symbol(symbol: str, *, client: MarketDataClient | None = None) -> dict:
    """
    Read-only, on-demand explainability snapshot for one symbol — answers
    "why is this ACTIVE/MONITOR/INELIGIBLE, and does it currently look
    interesting?" without writing anything anywhere.

    This is NOT a dry-run or apply evaluation: it never touches the
    ACTIVE/bank cap bookkeeping for any OTHER symbol, never persists
    anything, and never changes wl_state. The "would_be_state"/
    "would_be_reason" fields are the same determine_state_change() output
    a real evaluation run would compute right now, shown purely as
    information — nothing is written even if they say "ACTIVE".

    Returns a dict:
      symbol, found (bool)
      lifecycle: current DB-persisted state/score/counters/exclusion_reason
      provider_status, data_ok, failure_reason (if data fetch failed)
      relevance: {score, components (0.0-1.0 each), weights (%)} — the
        watchlist relevance score, computed live, NOT persisted
      opportunity: {score, verdict, vetoed (reason or None), signals
        (per-indicator bool), rsi, sma150, sma200} — the live alert/
        "opportunity" score from analyzers.technical.full_analysis()
      would_be_state, would_be_reason: hypothetical promotion/demotion
        decision using the symbol's CURRENT persisted counters — informational only

    History window: uses a dedicated MarketDataClient(period="1y") by
    default — NOT config.MARKET_DATA_HISTORY_PERIOD (6mo), which is too
    short to compute SMA150/SMA200 (a rolling mean is NaN once its window
    exceeds the available row count). This
    matches agent/core.py's live alert path, which has always fetched
    get_historical(symbol, period="1y") via a different fetcher
    (data/fetcher.py rather than data/market_data_validator.py) — same
    lookback duration, different underlying mechanism. Changing this only
    affects what explain_symbol() itself fetches; it does not change
    config.MARKET_DATA_HISTORY_PERIOD or any other caller of
    MarketDataClient (e.g. the real evaluator's liquidity lookback).
    """
    sym = symbol.upper()
    row = db.get_symbol_status(sym)
    if row is None:
        return {"symbol": sym, "found": False}

    client = client or MarketDataClient(period="1y")
    sec_type = row["security_type"] or classify_security_type(sym)
    market_result = client.validate(sym, security_type=sec_type)

    result: dict = {
        "symbol": sym,
        "found": True,
        "lifecycle": {
            "state": row["wl_state"],
            "security_type": sec_type,
            "categories": row["categories"],
            "enabled": bool(row["enabled"]),
            "persisted_relevance_score": row["relevance_score"],
            "consec_promote_count": row["consec_promote_count"],
            "consec_demote_count": row["consec_demote_count"],
            "dwell_days": row["dwell_days"],
            "last_evaluated": row["last_evaluated"],
            "exclusion_reason": row["exclusion_reason"],
        },
        "provider_status": market_result.provider_status.value,
        "data_ok": market_result.provider_status == ProviderStatus.OK,
        "failure_reason": None,
        "relevance": None,
        "opportunity": None,
        "would_be_state": None,
        "would_be_reason": None,
    }

    if market_result.provider_status != ProviderStatus.OK:
        result["failure_reason"] = market_result.failure_reason
        return result

    df, _ = client.get_history(sym)
    price_data = {"price": market_result.latest_close or 0.0, "volume": market_result.latest_volume or 0}
    avg_volume = _safe_avg_volume(market_result.average_daily_volume)

    active_symbols = db.get_symbols_by_state("ACTIVE")
    active_scores: list[int] = []
    active_bank_count = 0
    for s in active_symbols:
        st = db.get_symbol_status(s)
        if st["relevance_score"] is not None:
            active_scores.append(st["relevance_score"])
        if _is_bank(st["categories"]):
            active_bank_count += 1
    lowest_active_score = min(active_scores) if active_scores else None

    # Pass the symbol's REAL persisted state, unlike the dry-run evaluator's
    # candidate-gathering step (which only ever forwards MONITOR/ACTIVE
    # rows here and forces a MONITOR baseline for never-classified ones).
    # explain_symbol() must see USER_REMOVED/TEMPORARILY_INELIGIBLE/
    # ETF_INDEX_CONTEXT exactly as they are, or determine_state_change()'s
    # own USER_REMOVED-immutability / ETF-permanence checks never fire.
    eval_out = evaluate_symbol_eligibility(
        sym, price_data, df, avg_volume,
        current_state=row["wl_state"],
        consec_promote=row["consec_promote_count"], consec_demote=row["consec_demote_count"],
        dwell_days=row["dwell_days"], security_type=sec_type, is_bank=_is_bank(row["categories"]),
        active_count=len(active_symbols), active_bank_count=active_bank_count,
        lowest_active_score=lowest_active_score,
    )

    result["relevance"] = {
        "score": eval_out["score"],
        "components": eval_out["components"],
        "weights_pct": {
            "data_quality": 25, "liquidity": 25, "trend": 20,
            "momentum": 15, "proximity": 10, "volatility": 5,
        },
    }
    if row["wl_state"] == "TEMPORARILY_INELIGIBLE":
        # Mirrors _run()'s real recovery rule: a TEMPORARILY_INELIGIBLE
        # symbol with valid data again always lands in MONITOR first, never
        # directly in ACTIVE in the same cycle — determine_state_change()
        # alone (used for the generic eval_out above) doesn't know this
        # special case, so it's corrected here for display accuracy only.
        result["would_be_state"] = "MONITOR"
        result["would_be_reason"] = "recovered: data valid again; would return to MONITOR first (never directly to ACTIVE)"
    else:
        result["would_be_state"] = eval_out["new_state"]
        result["would_be_reason"] = eval_out["reason"]

    analysis = None
    if df is not None and price_data["price"] > 0:
        try:
            analysis = full_analysis(sym, df, price_data["price"])
        except Exception:
            analysis = None

    if analysis is not None:
        sma150_val = analysis["sma150"]
        # NaN-safe: sma150_val != sma150_val is True only for NaN (no math
        # import needed — same idiom as _safe_avg_volume above).
        sma150_known = sma150_val is not None and sma150_val == sma150_val
        # sma150_val itself may still be NaN below — never let a NaN reach
        # the formatter as a "real" value (Telegram would print "nan").
        display_sma150 = sma150_val if sma150_known else None

        vetoed = None
        if not sma150_known:
            # full_analysis()'s own veto check is `current_price > sma150`,
            # which is unconditionally False when sma150 is NaN (any
            # comparison against NaN is False in Python) — so the SAME
            # underlying score/verdict (0, NEUTRAL) results whether SMA150
            # is genuinely below price or simply undefined for lack of
            # history. This branch only fixes the human-readable LABEL for
            # that second case; it does not change the score or verdict.
            vetoed = "insufficient SMA150 data (need 150+ completed daily candles)"
        elif not analysis["above_sma150"]:
            vetoed = "price below SMA150"
        elif analysis["rsi"] < RSI_VETO_MIN:
            vetoed = f"RSI {analysis['rsi']} < {RSI_VETO_MIN} (oversold veto)"
        elif analysis["rsi"] > RSI_VETO_MAX:
            vetoed = f"RSI {analysis['rsi']} > {RSI_VETO_MAX} (overbought veto)"

        triggered = set(analysis.get("triggered_signals", []))
        result["opportunity"] = {
            "score": analysis["score"],
            "verdict": analysis["verdict"],
            "vetoed": vetoed,
            "signals": {
                "price_above_sma150": "price_above_sma150" in triggered,
                "sma150_above_sma200": "sma150_above_sma200" in triggered,
                "macd_bullish_crossover": "macd_bullish_crossover" in triggered,
                "rsi_healthy_range": "rsi_healthy_range" in triggered,
                "rsi_acceptable_zone": "rsi_acceptable_zone" in triggered,
                "volume_spike": "volume_spike" in triggered,
                "stoch_rsi_bullish_cross": "stoch_rsi_bullish_cross" in triggered,
                "above_vwap": "above_vwap" in triggered,
            },
            "rsi": analysis["rsi"],
            "sma150": display_sma150,
            "sma200": analysis["sma200"],
        }

    return result
