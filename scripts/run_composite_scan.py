"""
Manual composite-vs-legacy comparison across the ACTIVE watchlist tier.

Read-only with respect to the DB (symbol list only); fetches live 1y
history per symbol via the alert loop's own fetcher. Prints, per symbol,
the legacy full_analysis() score/verdict next to the composite engine's
layer breakdown and BUY flag. Not wired into alerting — inspection only.

Usage (from repo root):
    python scripts/run_composite_scan.py [SYMBOL ...]
With no arguments, scans the ACTIVE tier.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzers.composite import compute_market_context, composite_score
from analyzers.technical import full_analysis
from data.fetcher import get_historical, is_market_open
from db.database import get_symbols_by_state


def main() -> None:
    symbols = [s.upper() for s in sys.argv[1:]] or sorted(get_symbols_by_state("ACTIVE"))
    if not symbols:
        print("No ACTIVE symbols found and none given on the command line.")
        return

    market_open = is_market_open()
    context = compute_market_context(market_open=market_open)
    print(
        f"Regime: {context['regime']}  "
        f"(SPY {context['spy_close']} vs SMA150 {context['spy_sma150']})  "
        f"required RS: {context['required_rs']}  "
        f"required score for BUY flag: {context['required_score']}  "
        f"market_open={market_open}\n"
    )

    header = (f"{'Symbol':<7} {'Old':>4} {'Verdict':<11} | "
              f"{'Gate':<20} {'Trend':>6} {'Mom':>5} {'Vol':>5} {'RS':>5} "
              f"{'Total':>6} {'BUY':>4}  {'Stop (xATR)':>14}")
    print(header)
    print("-" * len(header))

    for symbol in symbols:
        try:
            df = get_historical(symbol, period="1y")
        except Exception as e:
            print(f"{symbol:<7} fetch error: {type(e).__name__}")
            continue
        if df is None or len(df) < 60:
            print(f"{symbol:<7} insufficient history")
            continue

        price = float(df["close"].iloc[-1])
        old = full_analysis(symbol, df, price)
        comp = composite_score(symbol, df, context, market_open=market_open, log=False)

        gate = comp["hard_gate"]
        if not gate["passed"]:
            print(f"{symbol:<7} {old['score']:>4} {old['verdict']:<11} | "
                  f"{'FAIL: ' + (gate['reason'] or ''):<20} "
                  f"{'-':>6} {'-':>5} {'-':>5} {'-':>5} {0:>6} {'no':>4}")
            continue

        lay = comp["layers"]
        stop = comp["stop"]
        print(f"{symbol:<7} {old['score']:>4} {old['verdict']:<11} | "
              f"{'PASS':<20} "
              f"{lay['trend']['points']:>6} {lay['momentum']['points']:>5} "
              f"{lay['volume']['points']:>5} {lay['relative_strength']['points']:>5} "
              f"{comp['total_score']:>6} {('YES' if comp['flag_buy'] else 'no'):>4}  "
              f"{stop['stop_price']:>8,.2f} ({stop['multiplier']}x)")


if __name__ == "__main__":
    main()
