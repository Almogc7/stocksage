"""
Manual entrypoint for the Phase 4 dry-run watchlist evaluator.

Run from the stocksage/ directory:

    python scripts/dry_run_evaluation.py

What it does:
  - Reads the watchlist universe from db/stocksage.db (read-only — the
    evaluator never writes to the watchlist table).
  - Fetches live data via a real MarketDataClient (real yfinance calls).
  - Records ONE evaluation_runs row with dry_run=True (this is the only
    database write this script performs — by design, see Phase 2/4).
  - Prints a plain-text summary of proposed changes. Does not apply them.
  - Never sends a Telegram message — this script does not import the bot.

Safety:
  - Default mode is dry-run; there is no flag to make this script write to
    the watchlist table — that capability does not exist yet (Phase 5+).
  - Pass --db <path> to point at a temporary/test SQLite file instead of
    the real production database (useful for local experimentation without
    even writing the evaluation_runs bookkeeping row to the real DB).
  - Use --mock to skip all network calls entirely (writes a fake successful
    evaluation_runs row using synthetic data) — useful for testing this
    script itself without touching yfinance or needing real market hours.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _print_summary(result) -> None:
    print("=" * 60)
    print("WATCHLIST DRY-RUN EVALUATION SUMMARY")
    print("=" * 60)
    print(f"run_id:              {result.run_id}")
    print(f"started_at (UTC):    {result.started_at}")
    print(f"completed_at (UTC):  {result.completed_at}")
    print(f"duration_seconds:    {result.duration_seconds:.2f}")
    print(f"fatal_error:         {result.fatal_error}")
    print(f"provider_degraded:   {result.provider_degraded}")
    print()
    print(f"considered: {result.total_symbols_considered}   evaluated: {result.total_symbols_evaluated}"
          f"   skipped: {result.total_symbols_skipped}   failed: {result.total_symbols_failed}")
    print()
    print(f"ACTIVE:                {result.active_before} -> {result.active_after}")
    print(f"MONITOR:               {result.monitor_before} -> {result.monitor_after}")
    print(f"TEMPORARILY_INELIGIBLE:{result.temporarily_ineligible_before} -> {result.temporarily_ineligible_after}")
    print(f"ETF_INDEX_CONTEXT:     {result.context_count}")
    print(f"USER_REMOVED:          {result.user_removed_count}")
    print()
    print(f"Proposed promotions ({len(result.proposed_promotions)}): {result.proposed_promotions}")
    print(f"Proposed demotions  ({len(result.proposed_demotions)}): {result.proposed_demotions}")
    print(f"Proposed ineligible ({len(result.proposed_ineligible)}): {result.proposed_ineligible}")
    print(f"Proposed recoveries ({len(result.proposed_recoveries)}): {result.proposed_recoveries}")
    print()
    print(f"provider_error_count: {result.provider_error_count}   stale_data_count: {result.stale_data_count}"
          f"   invalid_symbol_count: {result.invalid_symbol_count}")
    print(f"cache_hits: {result.cache_hits}   cache_misses: {result.cache_misses}"
          f"   yfinance_request_count: {result.yfinance_request_count}")
    if result.warnings:
        print()
        print("Warnings:")
        for w in result.warnings:
            print(f"  - {w}")
    print("=" * 60)
    print("No watchlist state was changed. This was a dry run.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", default=None,
        help="Path to a SQLite file to read instead of the real production db/stocksage.db. "
             "Strongly recommended for local experimentation.",
    )
    parser.add_argument(
        "--mock", action="store_true",
        help="Skip all network calls; use a no-op fake MarketDataClient that reports every "
             "candidate as TEMPORARY_FAILURE. For exercising this script itself only.",
    )
    args = parser.parse_args()

    import db.database as db
    if args.db:
        db.DB_PATH = Path(args.db)
        print(f"[dry_run_evaluation] Using DB: {db.DB_PATH}")
    else:
        print(f"[dry_run_evaluation] WARNING: using the real production DB: {db.DB_PATH}")
        print("[dry_run_evaluation] This will write ONE evaluation_runs bookkeeping row "
              "(dry_run=True). It will NOT change any watchlist state.")

    from services.watchlist_evaluator import run_dry_run_evaluation

    client = None
    if args.mock:
        from data.market_data_validator import MarketDataResult, ProviderStatus

        class _MockClient:
            cache_hits = 0
            cache_misses = 0
            yfinance_request_count = 0
            provider_error_count = 0

            def validate_batch(self, symbols, security_types=None):
                return {
                    s: MarketDataResult(
                        symbol=s, normalized_symbol=s,
                        provider_status=ProviderStatus.TEMPORARY_FAILURE,
                        failure_type="provider_transient",
                        failure_reason="mock client: no real data fetched (--mock)",
                    )
                    for s in symbols
                }

            def get_history(self, symbol):
                return None, ProviderStatus.TEMPORARY_FAILURE

        client = _MockClient()

    result = run_dry_run_evaluation(client=client, triggered_by="manual-cli")
    _print_summary(result)


if __name__ == "__main__":
    main()
