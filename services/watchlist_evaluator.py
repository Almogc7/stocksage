"""
Dry-run watchlist eligibility evaluator (Phase 4 of the dynamic watchlist
lifecycle — see WATCHLIST_AND_ALERTS_DESIGN.md).

What this module DOES:
  - Reads the current watchlist universe from SQLite (read-only).
  - Selects which symbols are eligible for evaluation this run, recording a
    skip_reason for every symbol that isn't.
  - Fetches/validates market data via data.market_data_validator.MarketDataClient.
  - Computes the relevance score via analyzers.eligibility (unchanged) and
    simulates the existing hysteresis state machine, including the ACTIVE
    cap (30), bank cap (8), and replacement margin (5).
  - Detects a broad yfinance outage and suppresses mass demotion proposals
    when one is detected.
  - Records an evaluation_runs row (Phase 2) with dry_run=True.

What this module does NOT do:
  - Write any proposed state/score/counter change to the watchlist table.
    Every change is "proposed" only — see DryRunEvaluationResult.
  - Schedule anything, or send Telegram messages.
  - Modify analyzers/eligibility.py's existing scoring/hysteresis contract.

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

from dataclasses import dataclass, field
from datetime import datetime, timezone

import db.database as db
from analyzers.eligibility import (
    classify_security_type,
    determine_state_change,
    evaluate_symbol_eligibility,
)
from config import (
    ACTIVE_BANK_MAX,
    ACTIVE_MAX_SIZE,
    BANK_CATEGORIES,
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


@dataclass
class DryRunEvaluationResult:
    run_id: int | None
    dry_run: bool = True
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


def run_dry_run_evaluation(
    *,
    client: MarketDataClient | None = None,
    triggered_by: str = "manual",
    now: datetime | None = None,
) -> DryRunEvaluationResult:
    """
    Run one full dry-run eligibility evaluation pass. Never writes to the
    watchlist table. Always records an evaluation_runs row (dry_run=True).
    """
    client = client or MarketDataClient()
    now = now or datetime.now(timezone.utc)
    started_at = now.strftime("%Y-%m-%d %H:%M:%S")
    today_str = now.date().isoformat()

    summary_before = db.get_watchlist_summary()
    run_id = db.create_evaluation_run(
        "dry_run", dry_run=True, triggered_by=triggered_by,
        active_before=summary_before.get("ACTIVE", 0),
        monitor_before=summary_before.get("MONITOR", 0),
        context_count=summary_before.get("ETF_INDEX_CONTEXT", 0),
        ineligible_before=summary_before.get("TEMPORARILY_INELIGIBLE", 0),
        user_removed_count=summary_before.get("USER_REMOVED", 0),
    )

    result = DryRunEvaluationResult(
        run_id=run_id,
        started_at=started_at,
        active_before=summary_before.get("ACTIVE", 0),
        monitor_before=summary_before.get("MONITOR", 0),
        context_count=summary_before.get("ETF_INDEX_CONTEXT", 0),
        temporarily_ineligible_before=summary_before.get("TEMPORARILY_INELIGIBLE", 0),
        user_removed_count=summary_before.get("USER_REMOVED", 0),
    )

    try:
        _run(result, client, today_str)
    except Exception as exc:  # fatal evaluator error — never leave a half-applied run
        result.fatal_error = f"{type(exc).__name__}: {exc}"
        completed = datetime.now(timezone.utc)
        result.completed_at = completed.strftime("%Y-%m-%d %H:%M:%S")
        result.duration_seconds = (completed - now).total_seconds()
        db.update_evaluation_run_failure(run_id, result.fatal_error)
        return result

    completed = datetime.now(timezone.utc)
    result.completed_at = completed.strftime("%Y-%m-%d %H:%M:%S")
    result.duration_seconds = (completed - now).total_seconds()

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
        "proposed_promotions": result.proposed_promotions,
        "proposed_demotions": result.proposed_demotions,
        "proposed_ineligible": result.proposed_ineligible,
        "proposed_recoveries": result.proposed_recoveries,
        "provider_degraded": result.provider_degraded,
        "warnings": result.warnings,
    }
    if result.provider_degraded:
        db.update_evaluation_run_partial_failure(
            run_id, "provider degraded: demotion proposals suppressed for this run",
            metadata=metadata, **counts,
        )
    else:
        db.update_evaluation_run_success(run_id, metadata=metadata, **counts)

    return result


def _run(result: DryRunEvaluationResult, client: MarketDataClient, today_str: str) -> None:
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
            _apply_failure_outcome(sr, row, market_results[sym], result)
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
            _apply_failure_outcome(sr, row, mr, result)
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
            sr.proposed_state = "MONITOR"  # provisional; resolved in pass 2 below
            sr.reason = "pending promotion-cap resolution"
        elif eval_out["new_state"] == "TEMPORARILY_INELIGIBLE":
            sr.proposed_state = "TEMPORARILY_INELIGIBLE"
            sr.reason = eval_out["reason"]
            result.proposed_ineligible.append(sym)
        else:
            sr.proposed_state = "MONITOR"
            sr.reason = eval_out["reason"]
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
            _apply_failure_outcome(sr, row, mr, result)
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
                    if evicted_symbol not in result.proposed_demotions:
                        result.proposed_demotions.append(evicted_symbol)
            tracker.add(sym, score, _is_bank(row["categories"]))
            sr.proposed_state = "ACTIVE"
            sr.reason = reason
            result.proposed_promotions.append(sym)
        else:
            sr.proposed_state = new_state
            sr.reason = reason

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
    sr: SymbolEvalResult, row: dict, market_result, result: DryRunEvaluationResult
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
    elif current_state == "TEMPORARILY_INELIGIBLE":
        sr.proposed_state = "TEMPORARILY_INELIGIBLE"
        sr.reason = f"still ineligible: {market_result.failure_reason}"
    else:
        sr.proposed_state = "TEMPORARILY_INELIGIBLE"
        sr.reason = f"data-quality failure: {market_result.failure_reason}"
        result.proposed_ineligible.append(sr.symbol)
