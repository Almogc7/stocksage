"""
Manual entrypoint for the watchlist evaluator (Phase 4 dry-run / Phase 5 apply).

Run from the stocksage/ directory:

    python scripts/dry_run_evaluation.py --db db/stocksage.db
    python scripts/dry_run_evaluation.py --db db/stocksage.db --apply --yes

What it does:
  - Reads the watchlist universe from the target DB.
  - Fetches live data via a real MarketDataClient (real yfinance calls),
    unless --mock is passed.
  - Records ONE evaluation_runs row every run (dry_run=True unless --apply).
  - In apply mode, additionally writes the computed state/score/counter
    changes to the watchlist table in one atomic transaction — see
    services/watchlist_evaluator.py's apply-mode docstring.
  - Prints a plain-text summary. Never sends a Telegram message — this
    script does not import the bot.

Safety:
  - Default mode is DRY-RUN. Applying real changes requires the explicit
    --apply flag.
  - --apply additionally requires --yes (a deliberate second flag) so a
    single typo/flag-order mistake cannot silently write to the watchlist.
  - Pass --db <path> to point at a temporary/test SQLite file instead of
    the real production database. Strongly recommended, especially for
    --apply, until you've validated the result on a copy first.
  - Use --mock to skip all network calls entirely (every candidate reports
    TEMPORARY_FAILURE) — useful for exercising this script itself without
    touching yfinance or needing real market hours.
  - This script will NOT run --apply against the real production DB_PATH
    without you passing both --apply and --yes explicitly; there is no
    "auto-confirm" default.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _print_summary(result, apply_mode: bool) -> None:
    mode_label = "APPLY (REAL CHANGES WRITTEN)" if apply_mode else "DRY-RUN (NO CHANGES WRITTEN)"
    print("=" * 60)
    print(f"WATCHLIST EVALUATION SUMMARY — MODE: {mode_label}")
    print("=" * 60)
    print(f"run_id:              {result.run_id}")
    print(f"dry_run:             {result.dry_run}")
    print(f"applied:             {result.applied}")
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
    if apply_mode and result.applied:
        print("Watchlist state WAS changed (apply mode).")
    elif apply_mode and not result.applied:
        print("Apply mode requested but the run failed before writing — no watchlist state was changed.")
    else:
        print("No watchlist state was changed. This was a dry run.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--db", default=None,
        help="Path to a SQLite file to read/write instead of the real production db/stocksage.db. "
             "Strongly recommended for local experimentation, and required in practice for --apply "
             "until you have validated the result on a copy.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Write the computed changes to the watchlist table. Default is dry-run (no writes). "
             "Must be combined with --yes.",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="Required together with --apply as an explicit confirmation. --apply alone does nothing.",
    )
    parser.add_argument(
        "--mock", action="store_true",
        help="Skip all network calls; use a no-op fake MarketDataClient that reports every "
             "candidate as TEMPORARY_FAILURE. For exercising this script itself only.",
    )
    args = parser.parse_args()

    apply_mode = args.apply and args.yes
    if args.apply and not args.yes:
        print("[dry_run_evaluation] --apply was given without --yes — refusing to apply. "
              "Falling back to dry-run. Pass both --apply --yes to actually write changes.")

    import db.database as db
    if args.db:
        db.DB_PATH = Path(args.db)
        print(f"[dry_run_evaluation] Using DB: {db.DB_PATH}")
    else:
        print(f"[dry_run_evaluation] WARNING: using the real production DB: {db.DB_PATH}")
        if apply_mode:
            print("[dry_run_evaluation] *** APPLY MODE against the REAL PRODUCTION DB. ***")
            print("[dry_run_evaluation] This WILL write real watchlist state changes.")
        else:
            print("[dry_run_evaluation] This will write ONE evaluation_runs bookkeeping row "
                  "(dry_run=True). It will NOT change any watchlist state.")

    from services.watchlist_evaluator import run_watchlist_evaluation

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

    result = run_watchlist_evaluation(apply=apply_mode, client=client, triggered_by="manual-cli")
    _print_summary(result, apply_mode)


if __name__ == "__main__":
    main()
