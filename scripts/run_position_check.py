"""
Manual position check — current recommended stop + exit signals for one
open LONG position, via analyzers/position_management.py.

Advisory only: prints and logs recommendations, executes nothing, and is
not wired into check_alerts() or any alert-sending path.

Usage (from repo root):
    python scripts/run_position_check.py SYMBOL ENTRY_PRICE ENTRY_DATE
        [--stop STOP] [--score SCORE] [--prev-stop PREV]

    ENTRY_DATE  ISO date, e.g. 2026-06-15
    --stop      the initial stop you are actually using (skips reconstruction)
    --score     composite score at entry, for the ATR multiplier when
                reconstructing the initial stop (omitted -> conservative 2.0x)
    --prev-stop the stop as it currently stands, from your last check
                (enforces monotonicity across repeated runs)
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logging_setup import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="Advisory stop/exit check for an open long position.")
    parser.add_argument("symbol")
    parser.add_argument("entry_price", type=float)
    parser.add_argument("entry_date", type=date.fromisoformat)
    parser.add_argument("--stop", type=float, default=None,
                        help="initial stop actually in use (overrides reconstruction)")
    parser.add_argument("--score", type=float, default=None,
                        help="composite score at entry (multiplier for reconstruction)")
    parser.add_argument("--prev-stop", type=float, default=None,
                        help="current stop from the previous check (monotonic floor)")
    args = parser.parse_args()

    setup_logging()

    from analyzers.position_management import evaluate_position
    from data.fetcher import get_historical, is_market_open

    df = get_historical(args.symbol, period="1y")
    if df is None or len(df) == 0:
        print(f"No historical data for {args.symbol.upper()}")
        return 1

    try:
        result = evaluate_position(
            args.symbol, df, args.entry_price, args.entry_date,
            initial_stop=args.stop, entry_score=args.score,
            previous_stop=args.prev_stop, market_open=is_market_open(),
        )
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1

    trail = result["trailing_stop"]
    partial = result["partial_exit"]
    signals = result["exit_signals"]

    print(f"\n{result['symbol']} -- entry {result['entry_price']:,.2f} on {result['entry_date']}")
    print(f"  Last completed close : {result['last_completed_close']:,.2f}  "
          f"(R = {result['r_multiple']:+.2f})")
    stop_src = "reconstructed" if result["initial_stop_reconstructed"] else "as given"
    print(f"  Initial stop         : {result['initial_stop']:,.2f}  ({stop_src})")
    mult = f", {trail['chandelier_multiplier']}x ATR chandelier" if trail["chandelier_multiplier"] else ""
    print(f"  Recommended stop NOW : {trail['stop_price']:,.2f}  "
          f"(stage {trail['stage']}, basis={trail['basis']}{mult}, raised={trail['raised']})")
    if result["stop_breached"]:
        print("  *** STOP BREACHED    : last close is at/below the recommended stop ***")
    print(f"  Highest high / ATR   : {result['highest_high_since_entry']:,.2f} / "
          f"{result['current_atr']:,.4f}")
    if signals["signals"]:
        print(f"  Exit signals FIRED   : {', '.join(signals['signals'])}")
        for name in signals["signals"]:
            print(f"    - {name}: {signals['details'][name]}")
    else:
        print("  Exit signals         : none")
    print(f"  Partial exit         : {partial['reason']}")
    print(f"  ACTION               : {result['recommended_action']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
