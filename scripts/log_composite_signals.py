"""
Nightly composite-signal logging job (schema v10: composite_signals /
composite_signal_outcomes).

Silent, observation-mode-only: computes analyzers/composite.py's
composite_score() for every symbol currently in the ACTIVE tier and writes
one row per symbol that clears the BUY threshold (flag_buy=True) into
composite_signals, with the full layer breakdown (trend/momentum/volume/RS
points, rs_ratio, required_rs) and stop sizing — the same level of detail
tracked by hand in docs/composite_vs_legacy_tracking.md. Sends NO alert,
NO Telegram message, writes to NO table the live alert path reads.
check_alerts() and the legacy engine (full_analysis()) are the only things
wired into alerting; this job never touches them.

Runs once per trading day, intentionally scheduled BEFORE the nightly
watchlist evaluation (02:30 IL, scripts/run_watchlist_evaluation.cmd) so
"ACTIVE tier" here means the tier as it stood during the trading day just
closed, not the tier after that night's promotions/demotions.

Idempotent: composite_signals has a UNIQUE(symbol, signal_date) constraint
and log_composite_signal() uses INSERT OR IGNORE — re-running (or a
delayed/duplicate Task Scheduler run) is a silent no-op for a day already
logged, since a completed trading day's historical bars never change. No
explicit weekend/holiday guard is needed either: on a non-trading day
get_historical() simply returns the same last completed bar as the prior
run, so signal_date resolves to the same already-logged date.

Usage (from repo root, nightly after US close via Task Scheduler):
    python scripts/log_composite_signals.py
Exit code 0 normally; 1 if every symbol's fetch failed (dead data
provider) or the ACTIVE tier is empty of fetchable history.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzers.composite import _completed_bars, _flatten, compute_market_context, composite_score
from data.fetcher import get_historical, is_market_open
from db.database import get_symbols_by_state, log_composite_signal
from logging_setup import setup_logging

logger = logging.getLogger("stocksage.composite_signals")


def _signal_date(df, market_open: bool) -> str:
    """Date of the last COMPLETED bar — mirrors composite_score()'s own
    _completed_bars() so the logged date always matches what the score was
    actually computed on, in-progress session included/excluded the same way."""
    completed = _completed_bars(_flatten(df), market_open)
    return str(completed.index[-1].date())


def run(
    symbols: list[str] | None = None,
    market_open: bool | None = None,
    spy_df=None,
    history_fn=None,
) -> dict:
    """Score every ACTIVE symbol; log the ones that clear flag_buy.

    `symbols`/`market_open`/`spy_df`/`history_fn` are injectable for tests
    (same spirit as compute_market_context()'s own spy_df=None parameter
    and populate_outcomes.run()'s client=None); production callers pass
    nothing and get the live ACTIVE tier + real clock/market state.
    `history_fn`, if given, replaces get_historical(symbol, period="1y")
    with a symbol -> DataFrame | None callable.
    """
    summary = {"scanned": 0, "logged": 0, "already_logged": 0, "fetch_failures": []}

    if symbols is None:
        symbols = get_symbols_by_state("ACTIVE")
    if not symbols:
        logger.warning("[composite_signals] ACTIVE tier is empty, nothing to scan")
        return summary
    if market_open is None:
        market_open = is_market_open()
    if history_fn is None:
        history_fn = lambda s: get_historical(s, period="1y")

    if spy_df is None:
        try:
            spy_df = get_historical("SPY", period="1y")
        except Exception as e:
            logger.error("[composite_signals] SPY fetch failed, aborting: %s", e)
            summary["fetch_failures"].append(("SPY", type(e).__name__))
            return summary
    if spy_df is None:
        logger.error("[composite_signals] SPY fetch returned no data, aborting")
        summary["fetch_failures"].append(("SPY", "EMPTY_DATA"))
        return summary

    context = compute_market_context(spy_df=spy_df, market_open=market_open)
    logger.info(
        "[composite_signals] regime=%s required_score=%s required_rs=%s market_open=%s",
        context["regime"], context["required_score"], context["required_rs"], market_open,
    )

    for symbol in sorted(symbols):
        summary["scanned"] += 1
        try:
            df = history_fn(symbol)
        except Exception as e:
            logger.warning("[composite_signals] fetch failed for %s: %s", symbol, e)
            summary["fetch_failures"].append((symbol, type(e).__name__))
            continue
        if df is None or len(df) < 60:
            summary["fetch_failures"].append((symbol, "EMPTY_OR_SHORT_DATA"))
            continue

        try:
            comp = composite_score(symbol, df, context, market_open=market_open, log=False)
        except Exception as e:
            logger.warning("[composite_signals] scoring failed for %s: %s", symbol, e)
            summary["fetch_failures"].append((symbol, type(e).__name__))
            continue

        if not comp["flag_buy"]:
            continue

        lay = comp["layers"]
        stop = comp["stop"]
        signal_date = _signal_date(df, market_open)

        signal_id = log_composite_signal(
            symbol=symbol,
            signal_date=signal_date,
            regime=context.get("regime", "UNKNOWN"),
            total_score=comp["total_score"],
            required_score=int(context.get("required_score")),
            trend_pts=lay["trend"]["points"],
            momentum_pts=lay["momentum"]["points"],
            volume_pts=lay["volume"]["points"],
            rs_pts=lay["relative_strength"]["points"],
            rs_ratio=lay["relative_strength"]["rs_ratio"],
            required_rs=lay["relative_strength"]["required_rs"],
            price=comp["last_completed_close"],
            atr=stop.get("atr"),
            stop_price=stop.get("stop_price"),
            stop_multiplier=stop.get("multiplier"),
        )
        if signal_id is not None:
            summary["logged"] += 1
            logger.info(
                "[composite_signals] LOGGED %s %s score=%.1f regime=%s stop=%.2f",
                symbol, signal_date, comp["total_score"], context.get("regime"),
                stop.get("stop_price") or 0.0,
            )
        else:
            summary["already_logged"] += 1

    return summary


def main() -> int:
    setup_logging()
    summary = run()
    logger.info(
        "[composite_signals] done: scanned=%s logged=%s already_logged=%s fetch_failures=%s",
        summary["scanned"], summary["logged"], summary["already_logged"],
        len(summary["fetch_failures"]),
    )
    if summary["fetch_failures"] and summary["scanned"] > 0 and \
            len(summary["fetch_failures"]) == summary["scanned"]:
        logger.error("[composite_signals] every symbol's fetch/scoring failed")
        return 1
    if summary["scanned"] == 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
