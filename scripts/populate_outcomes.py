"""
Nightly alert-outcome population job (schema v8: alert_signals/alert_outcomes).

Standalone offline job — touches nothing in the live alerting path. For every
alert_signals row without a COMPLETE alert_outcomes row (complete = close_t10
non-NULL), it fetches daily history and computes:

  close_t1/t3/t5/t10      closes N *trading days* after the alert
  max_adverse_excursion   worst % drawdown vs price_at_alert, T+1 through the
                          exit bar (barrier day, or T+10 on timeout)
  first_barrier_hit       'stop_loss' / 'take_profit' / 'none', checked day by
                          day on intraday high/low
  r_multiple              (exit - entry) / (entry - stop_loss); exit is the
                          barrier price if hit, else the T+10 close (timeout)

Semantics (confirmed 2026-07-12):
  * Trading days come from the BARS THEMSELVES: T+N is the Nth completed
    daily bar strictly after the alert's UTC date. Weekends/holidays and
    unscheduled closures never produce bars, so no calendar arithmetic is
    needed (and crypto symbols that trade weekends are counted correctly).
  * The alert-day bar (T+0) is EXCLUDED from all checks — its high/low
    include pre-alert price action (look-ahead contamination).
  * If one bar touches both barriers (low <= stop AND high >= TP), the STOP
    is assumed hit first (conservative backtesting convention).
  * Partial fills: an alert only N<10 trading days old gets whatever offsets
    exist now (e.g. t1/t3), the rest stay NULL; the row is recomputed from
    bars on every run until close_t10 fills. first_barrier_hit='none' and
    the timeout r_multiple are only assignable once 10 bars exist.

Idempotent: complete rows are never reselected; incomplete rows are
whole-row upserts recomputed deterministically from price history.

Usage (from repo root, nightly after US close via cron/Task Scheduler):
    python scripts/populate_outcomes.py
Exit code 0 normally; 1 when alerts were pending but every fetch failed
(so a scheduler can flag a dead data provider).
"""
import logging
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from logging_setup import setup_logging

logger = logging.getLogger("stocksage.outcomes")

HORIZON_DAYS = 10
_CLOSE_OFFSETS = {"close_t1": 1, "close_t3": 3, "close_t5": 5, "close_t10": 10}

_EMPTY_OUTCOME: dict = {
    "close_t1": None, "close_t3": None, "close_t5": None, "close_t10": None,
    "max_adverse_excursion": None, "first_barrier_hit": None, "r_multiple": None,
}


# ── Pure computation (no network, no DB — unit-tested directly) ──────────────

def compute_outcome(
    entry: float, stop_loss: float, take_profit: float, bars: pd.DataFrame
) -> dict:
    """Compute the outcome fields for one alert.

    `bars`: completed daily bars strictly AFTER the alert date (first row is
    T+1), chronological, with 'close'/'high'/'low' columns. May be empty or
    shorter than HORIZON_DAYS — missing offsets stay None (partial fill).
    """
    out = dict(_EMPTY_OUTCOME)
    bars = bars.iloc[:HORIZON_DAYS]
    n = len(bars)
    if n == 0:
        return out

    closes = bars["close"].astype(float).tolist()
    highs  = bars["high"].astype(float).tolist()
    lows   = bars["low"].astype(float).tolist()

    for key, offset in _CLOSE_OFFSETS.items():
        if n >= offset:
            out[key] = round(closes[offset - 1], 4)

    # Barrier scan, day by day. Stop checked before TP within a day —
    # daily bars can't reveal intraday order, so assume the worst.
    barrier: str | None = None
    exit_idx: int | None = None  # 0-based index of the bar the trade exits on
    for i in range(n):
        if lows[i] <= stop_loss:
            barrier, exit_idx = "stop_loss", i
            break
        if highs[i] >= take_profit:
            barrier, exit_idx = "take_profit", i
            break

    timeout = barrier is None and n >= HORIZON_DAYS
    if timeout:
        barrier, exit_idx = "none", HORIZON_DAYS - 1

    # first_barrier_hit stays NULL while the verdict is still open
    # (no barrier hit yet and fewer than 10 bars available).
    out["first_barrier_hit"] = barrier

    if exit_idx is not None:
        # MAE through the exit bar only — pain while the trade was open.
        worst_low = min(lows[: exit_idx + 1])
        out["max_adverse_excursion"] = round((worst_low - entry) / entry * 100, 2)

        risk = entry - stop_loss
        if risk > 0:
            exit_price = {
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "none": closes[exit_idx],
            }[barrier]
            out["r_multiple"] = round((exit_price - entry) / risk, 2)
    else:
        # Trade still open: report running MAE over the bars seen so far —
        # recomputed (and only ever widened) on later runs.
        out["max_adverse_excursion"] = round((min(lows) - entry) / entry * 100, 2)

    return out


def bars_after(df: pd.DataFrame, alert_date: date) -> pd.DataFrame:
    """Rows of `df` strictly after alert_date (T+0 excluded by design)."""
    dates = pd.to_datetime(df.index).date
    return df.loc[dates > alert_date]


def _drop_in_progress_bar(df: pd.DataFrame, today: date, market_open: bool) -> pd.DataFrame:
    """Nightly runs happen after the close, but if the job runs during US
    market hours yfinance's last daily row is the in-progress session —
    drop it so only completed bars are ever counted as trading days."""
    if market_open and len(df) and pd.to_datetime(df.index[-1]).date() == today:
        return df.iloc[:-1]
    return df


def _period_for(oldest_alert: date, today: date) -> str:
    """Smallest standard yfinance period covering the oldest pending alert
    plus a safety buffer."""
    span = (today - oldest_alert).days + 40
    for period, days in (("3mo", 85), ("6mo", 175), ("1y", 360)):
        if span <= days:
            return period
    return "2y"


# ── Job runner ────────────────────────────────────────────────────────────────

def run(client=None, today: date | None = None, market_open: bool | None = None) -> dict:
    """Process all pending alerts; returns the summary counts dict.

    `client`/`today`/`market_open` are injectable for tests; production
    callers pass nothing and get MarketDataClient + real clock/market state.
    """
    from db.database import get_alerts_pending_outcomes, upsert_alert_outcome

    pending = get_alerts_pending_outcomes()
    summary = {
        "processed": 0, "newly_completed": 0, "still_pending": 0,
        "fetch_failures": [],  # list of (symbol, alert_date, reason)
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
        oldest = min(date.fromisoformat(p["alert_date"]) for p in pending)
        client = MarketDataClient(period=_period_for(oldest, today))

    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for p in pending:
        by_symbol[p["symbol"]].append(p)

    for symbol in sorted(by_symbol):
        df, status = client.get_history(symbol)
        if df is None or df.empty:
            reason = getattr(status, "name", str(status)) if status else "EMPTY_DATA"
            for p in by_symbol[symbol]:
                summary["fetch_failures"].append((symbol, p["alert_date"], reason))
                logger.warning(
                    "[outcomes] fetch failed: %s alert_id=%s alert_date=%s (%s)",
                    symbol, p["alert_id"], p["alert_date"], reason,
                )
            continue

        df = _drop_in_progress_bar(df, today, market_open)

        for p in by_symbol[symbol]:
            alert_date = date.fromisoformat(p["alert_date"])
            window = bars_after(df, alert_date)
            outcome = compute_outcome(
                p["price_at_alert"], p["stop_loss"], p["take_profit"], window
            )
            if all(v is None for v in outcome.values()):
                # No completed trading day since the alert yet — nothing to
                # write; a future run will pick this row up again.
                summary["still_pending"] += 1
                continue

            upsert_alert_outcome(p["alert_id"], outcome)
            summary["processed"] += 1
            if outcome["close_t10"] is not None:
                summary["newly_completed"] += 1
            else:
                summary["still_pending"] += 1

    return summary


def main() -> int:
    setup_logging()
    logger.info("[outcomes] Starting alert-outcome population run")

    # Additive+idempotent — guarantees the v8 tables exist even if this job
    # runs before the app has started (and migrated) since the v8 change.
    from db.database import migrate_db
    migrate_db()

    summary = run()

    logger.info(
        "[outcomes] Done: %d row(s) written, %d newly completed, "
        "%d still pending more trading days, %d fetch failure(s)",
        summary["processed"], summary["newly_completed"],
        summary["still_pending"], len(summary["fetch_failures"]),
    )
    for symbol, alert_date, reason in summary["fetch_failures"]:
        logger.warning("[outcomes] FAILED %s (alert of %s): %s", symbol, alert_date, reason)

    had_pending = summary["processed"] or summary["still_pending"] or summary["fetch_failures"]
    all_failed = had_pending and not summary["processed"] and not summary["still_pending"]
    return 1 if all_failed else 0


if __name__ == "__main__":
    sys.exit(main())
