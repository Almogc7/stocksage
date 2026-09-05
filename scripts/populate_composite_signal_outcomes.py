"""
Nightly composite-signal-outcome population job (schema v10:
composite_signals/composite_signal_outcomes).

Same job as scripts/populate_outcomes.py, retargeted at composite_signals
instead of alert_signals — standalone, offline, touches nothing in the
live alerting path. For every composite_signals row without a COMPLETE
composite_signal_outcomes row (complete = close_t10 non-NULL), fetches
daily history and computes close_t1/t3/t5/t10, max_adverse_excursion,
first_barrier_hit, and r_multiple via populate_outcomes.compute_outcome() —
reused unmodified, called with take_profit=float('inf') so its TP branch
can never fire (the composite engine's stop dict has no take-profit level,
only a stop_price). first_barrier_hit therefore only ever lands on
'stop_loss' or 'none', never 'take_profit'.

Same semantics as populate_outcomes.py (see its docstring for the full
T+1/3/5/10, T+0-exclusion, and MAE conventions) — trading days come from
the bars themselves, the signal-day bar is excluded, idempotent (complete
rows never reselected, incomplete rows are whole-row upserts recomputed
deterministically from price history on every run).

Usage (from repo root, nightly after US close via Task Scheduler):
    python scripts/populate_composite_signal_outcomes.py
Exit code 0 normally; 1 when signals were pending but every fetch failed.
"""
import logging
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from logging_setup import setup_logging
from populate_outcomes import bars_after, compute_outcome, _drop_in_progress_bar, _period_for

logger = logging.getLogger("stocksage.composite_outcomes")


def run(client=None, today: date | None = None, market_open: bool | None = None) -> dict:
    """Process all pending composite_signals rows; returns summary counts.

    `client`/`today`/`market_open` are injectable for tests; production
    callers pass nothing and get MarketDataClient + real clock/market state.
    """
    from db.database import get_composite_signals_pending_outcomes, upsert_composite_signal_outcome

    pending = get_composite_signals_pending_outcomes()
    summary = {
        "processed": 0, "newly_completed": 0, "still_pending": 0,
        "fetch_failures": [],  # list of (symbol, signal_date, reason)
    }
    if not pending:
        return summary

    if today is None:
        today = datetime.now(timezone.utc).date()
    if market_open is None:
        from data.fetcher import is_market_open
        market_open = is_market_open()
    if client is None:
        from data.market_data_validator import MarketDataClient
        oldest = min(date.fromisoformat(p["signal_date"]) for p in pending)
        client = MarketDataClient(period=_period_for(oldest, today))

    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for p in pending:
        by_symbol[p["symbol"]].append(p)

    for symbol in sorted(by_symbol):
        df, status = client.get_history(symbol)
        if df is None or df.empty:
            reason = getattr(status, "name", str(status)) if status else "EMPTY_DATA"
            for p in by_symbol[symbol]:
                summary["fetch_failures"].append((symbol, p["signal_date"], reason))
                logger.warning(
                    "[composite_outcomes] fetch failed: %s signal_id=%s signal_date=%s (%s)",
                    symbol, p["signal_id"], p["signal_date"], reason,
                )
            continue

        df = _drop_in_progress_bar(df, today, market_open)

        for p in by_symbol[symbol]:
            signal_date = date.fromisoformat(p["signal_date"])
            window = bars_after(df, signal_date)
            # No take-profit in the composite engine's stop sizing — pass
            # +inf so compute_outcome()'s TP branch can never trigger.
            outcome = compute_outcome(
                p["price"], p["stop_price"], float("inf"), window
            )
            if all(v is None for v in outcome.values()):
                summary["still_pending"] += 1
                continue

            upsert_composite_signal_outcome(p["signal_id"], outcome)
            summary["processed"] += 1
            if outcome["close_t10"] is not None:
                summary["newly_completed"] += 1
            else:
                summary["still_pending"] += 1

    return summary


def main() -> int:
    setup_logging()
    summary = run()
    logger.info(
        "[composite_outcomes] done: processed=%s newly_completed=%s "
        "still_pending=%s fetch_failures=%s",
        summary["processed"], summary["newly_completed"],
        summary["still_pending"], len(summary["fetch_failures"]),
    )
    if summary["fetch_failures"] and not summary["processed"]:
        logger.error("[composite_outcomes] every pending fetch failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
