"""
Historical backtest of the composite scoring engine (analyzers/composite.py)
across the full config.py WATCHLIST universe (~400 symbols), 2-3+ years back.

Purely observation/analysis. Nothing here is wired into check_alerts(),
log_composite_signals.py, or any live path — it writes ONLY to the new
composite_backtest_runs / composite_backtest_signals tables (schema v11),
append-only: every run gets its own run_id and old runs are never
overwritten, mirroring the append-only convention used for
docs/composite_vs_legacy_tracking.md.

Method (same look-ahead discipline as the live engine):
  For each symbol, fetch ~(years + 1) years of daily OHLCV (the extra year
  is warm-up: SMA200 needs 200 completed bars before the hard gate can ever
  pass, and the RS layer needs COMPOSITE_RS_WINDOW_DAYS on top of that).
  Walk forward one completed bar at a time from bar 200 onward, slicing the
  DataFrame to df.iloc[:i+1] at each step (so composite_score() only ever
  sees bars up to and including "today" — exactly what the live nightly job
  sees, no future data leaks in). The SPY regime context is precomputed
  ONCE per calendar day (not once per symbol per day — that would be ~400x
  redundant, since compute_market_context() only depends on SPY) and looked
  up by date for every symbol's walk.

  Every day flag_buy=True fires, the full layer breakdown is recorded
  (mirrors composite_signals) together with its forward outcome — computed
  immediately via populate_outcomes.compute_outcome(), REUSED UNMODIFIED,
  with take_profit=+inf exactly like the live composite_signal_outcomes
  job, since a backtest already has the full future price series in hand
  (no separate nightly outcome-population pass is needed here). SPY's own
  forward closes at the same offsets are also recorded so the analysis can
  compute SPY-relative (excess) return per signal.

Known risks — read before running at full scale (also printed at startup):

  1. SURVIVORSHIP BIAS: config.py's WATCHLIST is TODAY's curated universe.
     Symbols that were delisted, went bankrupt, or were simply never
     considered are absent — this backtest can only ever show what would
     have happened to stocks that turned out, in hindsight, to still be
     worth watching. Historical eligibility (what analyzers/eligibility.py
     would have promoted into ACTIVE on a given past date) is NOT
     reconstructed here; this is a much looser "how does the composite
     engine score on today's watchlist, historically" test, not a true
     point-in-time simulation of the watchlist lifecycle.
  2. DATA AVAILABILITY: Stooq (primary) and yfinance (fallback) both serve
     split/dividend-adjusted daily closes with their own adjustment
     methodologies, which can disagree with each other and with what a live
     signal on that historical date would actually have seen (adjustments
     are retroactive — a stock's "close 2 years ago" is adjusted using
     dividends paid SINCE then, which the live engine on that date never
     saw). Indices/ETFs (^GSPC, ^VIX, ...) and crypto (BTC-USD, ...) often
     fail Stooq's symbol mapping and fall back to yfinance; crypto trades
     7 days/week while SPY trades ~5, so the RS layer's positional
     (iloc-based) alignment between a crypto symbol's closes and SPY's
     closes is NOT calendar-aligned — this is a pre-existing limitation of
     analyzers/composite.py itself (not introduced by this script), but it
     is more likely to matter across a multi-year walk than in a single
     day's live scan. Very illiquid names may also have Stooq/yfinance gaps
     that silently shift the effective window.
  3. COMPUTATION TIME: composite_score() short-circuits cheaply on hard-gate
     failures (the expensive RSI/MACD/StochRSI/volume/RS layers only run on
     days the price/SMA150/SMA200 stack already passes), but a full run is
     still ~400 symbols x ~500-700 walked days x however many clear the
     gate. Expect this to take from several minutes to over an hour
     end-to-end depending on --years/--workers and network latency to
     Stooq/yfinance. Use --limit/--symbols for a fast sanity-check run
     before committing to the full universe, and --workers to parallelize
     the per-symbol fetch+walk (threads; the walk itself is CPU-bound
     Python/pandas so parallelism is I/O-bound-limited, not linear).
  4. A full-universe run (no --limit/--symbols) requires --yes.

Usage (from repo root):
    python scripts/backtest_composite.py --limit 15                 # smoke test
    python scripts/backtest_composite.py --symbols AAPL MSFT NVDA
    python scripts/backtest_composite.py --years 3 --workers 8 --yes # full run
    python scripts/backtest_composite.py --analyze-only [--run-id ID]
"""
from __future__ import annotations

import argparse
import bisect
import logging
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from analyzers.composite import _completed_bars, _flatten, compute_market_context, composite_score
from data.market_data_service import MarketDataService
from logging_setup import setup_logging
from populate_outcomes import bars_after, compute_outcome

logger = logging.getLogger("stocksage.backtest_composite")

WARMUP_YEARS = 1.0          # extra history fetched so SMA200 has bars before "day 1"
HARD_GATE_WARMUP_BARS = 200  # SMA200 needs 200 completed bars
SPY_WARMUP_BARS = 150        # SMA150 needs 150 completed bars for the regime

_PERIOD_BUCKETS = [(1, "1y"), (2, "2y"), (5, "5y"), (10, "10y")]

_RISK_NOTICE = __doc__.split("Known risks", 1)[1].split("Usage", 1)[0]


def _period_for_years(years: float) -> str:
    for max_y, period in _PERIOD_BUCKETS:
        if years <= max_y:
            return period
    return "max"


def _trim_to_years(df: pd.DataFrame, years: float) -> pd.DataFrame:
    df = df.sort_index()
    cutoff = df.index.max() - pd.Timedelta(days=int(years * 365))
    return df[df.index >= cutoff]


def _full_universe() -> list[str]:
    import config
    symbols: set[str] = set()
    for group in config.WATCHLIST.values():
        symbols.update(s.upper() for s in group)
    return sorted(symbols)


# ── SPY regime timeline, computed ONCE (not once per symbol) ─────────────────

def build_spy_context_timeline(spy_df: pd.DataFrame, fetch_years: float) -> tuple[dict, pd.DataFrame]:
    spy_df = _trim_to_years(_flatten(spy_df), fetch_years)
    n = len(spy_df)
    timeline: dict[date, dict] = {}
    for i in range(SPY_WARMUP_BARS, n):
        sliced = spy_df.iloc[: i + 1]
        ctx = compute_market_context(spy_df=sliced, market_open=False)
        if not ctx.get("spy_available"):
            continue
        timeline[sliced.index[-1].date()] = ctx
    return timeline, spy_df


def _lookup_context(day: date, timeline: dict, sorted_dates: list) -> dict | None:
    if day in timeline:
        return timeline[day]
    i = bisect.bisect_right(sorted_dates, day)
    if i == 0:
        return None
    return timeline[sorted_dates[i - 1]]


def _spy_forward_row(spy_df: pd.DataFrame, signal_date: date) -> dict:
    prior = spy_df.index[pd.to_datetime(spy_df.index).date <= signal_date]
    if len(prior) == 0:
        return {}
    row = {"spy_close_at_signal": round(float(spy_df.loc[prior[-1], "close"]), 4)}
    window = bars_after(spy_df, signal_date)
    for key, offset in (("spy_close_t1", 1), ("spy_close_t3", 3), ("spy_close_t5", 5), ("spy_close_t10", 10)):
        row[key] = round(float(window["close"].iloc[offset - 1]), 4) if len(window) >= offset else None
    return row


# ── Per-symbol walk-forward ────────────────────────────────────────────────

def process_symbol(
    symbol: str, years: float, service: MarketDataService,
    spy_timeline: dict, spy_sorted_dates: list, spy_df: pd.DataFrame,
) -> tuple[str, list[dict], str | None]:
    fetch_years = years + WARMUP_YEARS
    result = service.fetch_history(symbol, period=_period_for_years(fetch_years))
    if not result.ok:
        return symbol, [], f"fetch_failed: {result.failures}"

    df = _trim_to_years(_flatten(result.df), fetch_years)
    n = len(df)
    if n < HARD_GATE_WARMUP_BARS + 10:
        return symbol, [], "insufficient_history"

    signals: list[dict] = []
    for i in range(HARD_GATE_WARMUP_BARS, n):
        completed = df.iloc[: i + 1]
        day = completed.index[-1].date()
        ctx = _lookup_context(day, spy_timeline, spy_sorted_dates)
        if ctx is None:
            continue
        try:
            comp = composite_score(symbol, completed, ctx, market_open=False, log=False)
        except Exception as e:
            logger.debug("[backtest] %s %s scoring error: %s", symbol, day, e)
            continue
        if not comp["flag_buy"]:
            continue

        price = comp["last_completed_close"]
        stop_price = comp["stop"]["stop_price"]
        outcome = compute_outcome(price, stop_price, float("inf"), bars_after(df, day))
        lay = comp["layers"]
        row = {
            "symbol": symbol,
            "signal_date": str(day),
            "regime": ctx.get("regime", "UNKNOWN"),
            "total_score": comp["total_score"],
            "required_score": int(ctx.get("required_score")),
            "trend_pts": lay["trend"]["points"],
            "momentum_pts": lay["momentum"]["points"],
            "volume_pts": lay["volume"]["points"],
            "rs_pts": lay["relative_strength"]["points"],
            "rs_ratio": lay["relative_strength"]["rs_ratio"],
            "required_rs": lay["relative_strength"]["required_rs"],
            "price": price,
            "atr": comp["stop"]["atr"],
            "stop_price": stop_price,
            "stop_multiplier": comp["stop"]["multiplier"],
            **outcome,
            **_spy_forward_row(spy_df, day),
        }
        signals.append(row)

    return symbol, signals, None


# ── Run orchestration ────────────────────────────────────────────────────────

def run(
    symbols: list[str], years: float, workers: int, run_id: str | None = None,
) -> str:
    from db.database import finish_backtest_run, insert_backtest_signals, migrate_db, start_backtest_run

    migrate_db()
    run_id = run_id or f"bt_{pd.Timestamp.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    service = MarketDataService()
    fetch_years = years + WARMUP_YEARS
    spy_result = service.fetch_history("SPY", period=_period_for_years(fetch_years))
    if not spy_result.ok:
        raise RuntimeError(f"SPY fetch failed, cannot compute regime context: {spy_result.failures}")
    spy_timeline, spy_df = build_spy_context_timeline(spy_result.df, fetch_years)
    spy_sorted_dates = sorted(spy_timeline)
    logger.info("[backtest] SPY regime timeline: %d trading days", len(spy_timeline))

    start_backtest_run(run_id, years, len(symbols))
    logger.info("[backtest] run_id=%s symbols=%d years=%s workers=%d", run_id, len(symbols), years, workers)

    ok = failed = total_signals = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(process_symbol, sym, years, service, spy_timeline, spy_sorted_dates, spy_df): sym
            for sym in symbols
        }
        done = 0
        for fut in as_completed(futures):
            sym = futures[fut]
            done += 1
            try:
                _, sig_rows, err = fut.result()
            except Exception as e:
                err, sig_rows = str(e), []
            if err:
                failed += 1
                logger.warning("[backtest] %s FAILED: %s", sym, err)
            else:
                ok += 1
                if sig_rows:
                    written = insert_backtest_signals(run_id, sig_rows)
                    total_signals += written
            if done % 25 == 0 or done == len(symbols):
                logger.info(
                    "[backtest] progress %d/%d (ok=%d failed=%d signals=%d)",
                    done, len(symbols), ok, failed, total_signals,
                )

    finish_backtest_run(run_id, ok, failed, total_signals)
    logger.info(
        "[backtest] DONE run_id=%s ok=%d failed=%d total_signals=%d",
        run_id, ok, failed, total_signals,
    )
    return run_id


# ── Analysis ──────────────────────────────────────────────────────────────────

def analyze_run(run_id: str) -> None:
    from db.database import get_backtest_signals

    rows = get_backtest_signals(run_id)
    if not rows:
        print(f"No signals found for run {run_id}.")
        return
    df = pd.DataFrame(rows)
    n = len(df)
    complete = df[df["r_multiple"].notna()]

    print(f"\n=== Backtest analysis: run {run_id} ===")
    print(f"Total signals fired: {n}")
    print(f"Unique symbols: {df['symbol'].nunique()}")
    print(f"Date range: {df['signal_date'].min()} .. {df['signal_date'].max()}")
    print(f"Regime split: {df['regime'].value_counts().to_dict()}")
    print(f"Signals with a fully resolved outcome (r_multiple known): {len(complete)} "
          f"({len(complete) / n * 100:.1f}%)")

    if len(complete) == 0:
        print("No resolved outcomes yet (all signals too recent) — nothing further to analyze.")
        return

    win_rate = (complete["r_multiple"] > 0).mean() * 100
    print(f"\nWin rate (r_multiple > 0): {win_rate:.1f}%  (n={len(complete)})")
    print(f"Mean r_multiple: {complete['r_multiple'].mean():.3f}")
    print(f"Median r_multiple: {complete['r_multiple'].median():.3f}")
    print(f"Stop-loss hit rate: {(complete['first_barrier_hit'] == 'stop_loss').mean() * 100:.1f}%")

    print("\n-- Per-layer correlation with r_multiple --")
    for col in ("total_score", "trend_pts", "momentum_pts", "volume_pts", "rs_pts", "rs_ratio"):
        corr = complete[[col, "r_multiple"]].dropna().corr().iloc[0, 1] if complete[col].notna().any() else float("nan")
        print(f"  {col:15s} r={corr:+.3f}" if corr == corr else f"  {col:15s} r=  n/a")

    print("\n-- SPY-relative (excess) return --")
    spy_complete = complete.dropna(subset=["spy_close_at_signal"])
    for label, close_col, spy_col in (
        ("T+1", "close_t1", "spy_close_t1"), ("T+3", "close_t3", "spy_close_t3"),
        ("T+5", "close_t5", "spy_close_t5"), ("T+10", "close_t10", "spy_close_t10"),
    ):
        sub = spy_complete.dropna(subset=[close_col, spy_col])
        if sub.empty:
            print(f"  {label}: n/a")
            continue
        stock_ret = sub[close_col] / sub["price"] - 1.0
        spy_ret = sub[spy_col] / sub["spy_close_at_signal"] - 1.0
        excess = stock_ret - spy_ret
        beat_pct = (excess > 0).mean() * 100
        print(f"  {label}: n={len(sub)} avg_stock_ret={stock_ret.mean()*100:+.2f}% "
              f"avg_spy_ret={spy_ret.mean()*100:+.2f}% avg_excess={excess.mean()*100:+.2f}% "
              f"beat_spy={beat_pct:.1f}%")

    print("\n-- Symbol concentration (top 10 by signal count) --")
    print(complete["symbol"].value_counts().head(10).to_string())
    print(
        "\nNote: signals from the same symbol are NOT independent trials (overlapping "
        "windows, shared trend) — treat per-symbol clusters as one observation, not N."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--years", type=float, default=2.0, help="Years of walk-forward analysis window (default 2)")
    parser.add_argument("--workers", type=int, default=6, help="Thread-pool size for per-symbol fetch+walk (default 6)")
    parser.add_argument("--symbols", nargs="*", help="Restrict to these symbols instead of the full WATCHLIST")
    parser.add_argument("--limit", type=int, help="Cap the universe to the first N symbols (sanity-check runs)")
    parser.add_argument("--yes", action="store_true", help="Required to run the full (no --symbols/--limit) universe")
    parser.add_argument("--run-id", help="Explicit run id (also used by --analyze-only)")
    parser.add_argument("--analyze-only", action="store_true", help="Skip fetching/scoring; analyze an existing run")
    args = parser.parse_args()

    setup_logging()
    print(_RISK_NOTICE)

    if args.analyze_only:
        from db.database import get_latest_backtest_run_id
        run_id = args.run_id or get_latest_backtest_run_id()
        if not run_id:
            print("No backtest runs found in the database.")
            return 1
        analyze_run(run_id)
        return 0

    if args.symbols:
        universe = [s.upper() for s in args.symbols]
    else:
        universe = _full_universe()
        if args.limit is None and not args.yes:
            print(
                f"Refusing to run the full {len(universe)}-symbol universe without --yes "
                "(see computation-time risk above). Pass --limit N for a quick sanity check, "
                "or --yes to run at full scale."
            )
            return 1
    if args.limit:
        universe = universe[: args.limit]

    run_id = run(universe, args.years, args.workers, run_id=args.run_id)
    analyze_run(run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
