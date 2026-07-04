"""
Manual entrypoint for StrongTrendScanner (Phase 9A).

Run from the stocksage/ directory:

    python scripts/run_strong_trend_scan.py NVDA AAPL MSFT
    python scripts/run_strong_trend_scan.py NVDA AAPL --timeframe 1wk
    python scripts/run_strong_trend_scan.py NVDA AAPL --dry-run
    python scripts/run_strong_trend_scan.py NVDA AAPL --db path/to/test.db
    python scripts/run_strong_trend_scan.py NVDA AAPL --top-n 10

What it does:
  - Runs scanners.strong_trend_scanner.StrongTrendScanner against an
    explicit list of symbols you pass on the command line.
  - Reads ONLY already-cached stock_prices data (via
    analyzers/cached_indicators.py + data/history_store.py's read helpers).
    Never fetches live market data, never calls Stooq/yfinance.
  - Default mode persists one scanner_runs row + one scanner_results row per
    symbol via scanners.scanner_runner.run_scanner() (Phase 8).
  - --dry-run skips persistence entirely: scans and prints, writes nothing.
  - Never sends a Telegram message or touches the live alert flow — this
    script does not import bot/telegram_bot.py or agent/core.py.
  - Not scheduled and not auto-run from main.py — must be invoked manually.

Safety:
  - Pass --db <path> to point at a temporary/test SQLite file instead of
    the real production database. Without it, this script uses the real
    production db/stocksage.db and WILL write to it (unless --dry-run).
  - Duplicate symbols in your input are de-duplicated (order preserved)
    before scanning, to avoid a known counting quirk in the underlying
    runner when the same symbol appears twice in one run.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _dedupe_preserve_order(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in symbols:
        sym = s.upper()
        if sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out


def _print_dry_run_summary(results: list[dict], top_n: int) -> None:
    print("=" * 60)
    print("STRONG TREND SCAN — MODE: DRY-RUN (NOTHING WRITTEN)")
    print("=" * 60)
    passed = [r for r in results if r.get("passed")]
    failed = [r for r in results if not r.get("passed")]
    print(f"symbols_scanned: {len(results)}")
    print(f"symbols_passed:  {len(passed)}")
    print(f"symbols_failed:  {len(failed)}")
    print()
    _print_passed(passed, top_n)
    _print_failed(failed)
    print("=" * 60)
    print("DRY RUN — nothing was written to scanner_runs/scanner_results.")


def _print_run_summary(run_id: int, summary: dict, results: list[dict], top_n: int) -> None:
    print("=" * 60)
    print(f"STRONG TREND SCAN — run_id={run_id}")
    print("=" * 60)
    print(f"status:          {summary['status']}")
    print(f"symbols_scanned: {summary['symbols_scanned']}")
    print(f"symbols_passed:  {summary['symbols_passed']}")
    print(f"symbols_failed:  {summary['symbols_failed']}")
    if summary["errors"]:
        print()
        print("Errors:")
        for symbol, message in summary["errors"].items():
            print(f"  - {symbol}: {message}")
    print()

    passed_rows = [r for r in results if r.get("passed")]
    failed_rows = [r for r in results if not r.get("passed")]
    _print_passed(passed_rows, top_n)
    _print_failed(failed_rows)
    print("=" * 60)


def _print_passed(rows: list[dict], top_n: int) -> None:
    ranked = sorted(rows, key=lambda r: (r.get("score") or 0), reverse=True)
    print(f"Top {min(top_n, len(ranked))} passed symbols:")
    if not ranked:
        print("  (none)")
    for r in ranked[:top_n]:
        print(f"  {r['symbol']:<8} score={r.get('score')}  {r.get('reason')}")
    print()


def _print_failed(rows: list[dict]) -> None:
    print(f"Failed/insufficient-data symbols ({len(rows)}):")
    if not rows:
        print("  (none)")
    for r in rows:
        print(f"  {r['symbol']:<8} {r.get('reason')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbols", nargs="+", help="Explicit symbols to scan, e.g. NVDA AAPL MSFT")
    parser.add_argument("--timeframe", default="1d", help="Timeframe to scan (default: 1d)")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Scan and print only — do not create scanner_runs/scanner_results rows.",
    )
    parser.add_argument(
        "--db", default=None,
        help="Path to a SQLite file to read/write instead of the real production db/stocksage.db.",
    )
    parser.add_argument(
        "--top-n", type=int, default=5,
        help="How many top-scoring passed symbols to display (default: 5).",
    )
    args = parser.parse_args()

    symbols = _dedupe_preserve_order(args.symbols)

    import db.database as db
    if args.db:
        db.DB_PATH = Path(args.db)
        print(f"[run_strong_trend_scan] Using DB: {db.DB_PATH}")
    else:
        print(f"[run_strong_trend_scan] WARNING: using the real production DB: {db.DB_PATH}")
        if not args.dry_run:
            print("[run_strong_trend_scan] This WILL write scanner_runs/scanner_results rows.")

    from scanners.strong_trend_scanner import StrongTrendScanner

    scanner = StrongTrendScanner()

    if args.dry_run:
        results = [scanner.scan(symbol, args.timeframe) for symbol in symbols]
        _print_dry_run_summary(results, args.top_n)
        return

    from scanners.scanner_runner import run_scanner

    summary = run_scanner(scanner, symbols, timeframe=args.timeframe)
    results = db.get_scanner_results(summary["run_id"])
    _print_run_summary(summary["run_id"], summary, results, args.top_n)


if __name__ == "__main__":
    main()
