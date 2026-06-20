"""
Manual entrypoint for the watchlist evaluator (Phase 4 dry-run / Phase 5
apply / Phase 6 scheduler).

Run from the stocksage/ directory:

    python scripts/dry_run_evaluation.py --db db/stocksage.db
    python scripts/dry_run_evaluation.py --db db/stocksage.db --apply --yes
    python scripts/dry_run_evaluation.py --db db/stocksage.db --schedule-check
    python scripts/dry_run_evaluation.py --db db/stocksage.db --scheduled-dry-run
    python scripts/dry_run_evaluation.py --db db/stocksage.db --scheduled-apply --yes

Scheduler modes (Phase 6 — see services/watchlist_scheduler.py):
  --schedule-check     Read-only. Prints whether a scheduled evaluation is
                        due right now, the next due time, and the last
                        successful scheduled run. Writes nothing.
  --scheduled-dry-run  Runs services.watchlist_scheduler.run_scheduled_evaluation()
                        with apply=False. Only actually evaluates (and
                        writes one evaluation_runs row, run_type=scheduled)
                        if the scheduler decides it's due AND no other run
                        is in progress; otherwise just reports why it was
                        skipped — exactly like a real unattended scheduled
                        dry-run would behave.
  --scheduled-apply    Same due/concurrency checks, but apply=True. Still
                        requires --yes. This is an explicit manual override
                        of config.WATCHLIST_SCHEDULE_APPLY (which governs
                        what an *unattended* scheduled run would do by
                        default — still False unless you've set that env
                        var) — see the module docstring for the distinction.

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


def _schedule_check(args) -> None:
    import db.database as db
    if args.db:
        db.DB_PATH = Path(args.db)
        print(f"[schedule-check] Using DB: {db.DB_PATH}")
    else:
        print(f"[schedule-check] Using the real production DB (read-only): {db.DB_PATH}")

    from datetime import datetime, timezone
    import services.watchlist_scheduler as sched

    now = datetime.now(timezone.utc)
    due, reason = sched.should_run_watchlist_evaluation(now)
    next_due = sched.next_watchlist_evaluation_time(now)
    last_success = sched.get_last_successful_scheduled_evaluation()
    guard_ok, guard_reason = sched.can_start_evaluation_run(now_utc=now)

    print("=" * 60)
    print("WATCHLIST SCHEDULE CHECK")
    print("=" * 60)
    print(f"now (UTC):                {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"due now:                  {due}  ({reason})")
    print(f"next due (UTC):           {next_due.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"concurrency guard:        {'OK' if guard_ok else 'BLOCKED'}  ({guard_reason})")
    if last_success:
        print(f"last successful scheduled run: run_id={last_success['run_id']} "
              f"started_at={last_success['started_at']} status={last_success['status']}")
    else:
        print("last successful scheduled run: none yet")
    print(f"scheduled-apply config (WATCHLIST_SCHEDULE_APPLY): {__import__('config').WATCHLIST_SCHEDULE_APPLY}")
    print("=" * 60)
    print("Read-only check. Nothing was written.")


def _run_scheduled(args, apply_mode: bool) -> None:
    import db.database as db
    if args.db:
        db.DB_PATH = Path(args.db)
        print(f"[dry_run_evaluation] Using DB: {db.DB_PATH}")
    else:
        print(f"[dry_run_evaluation] WARNING: using the real production DB: {db.DB_PATH}")
        if apply_mode:
            print("[dry_run_evaluation] *** SCHEDULED APPLY against the REAL PRODUCTION DB. ***")

    import services.watchlist_scheduler as sched

    client = _build_client(args)
    outcome = sched.run_scheduled_evaluation(apply=apply_mode, client=client, triggered_by="manual-cli-scheduled")

    print("=" * 60)
    print(f"WATCHLIST SCHEDULED EVALUATION — market_date={outcome['market_date']}")
    print("=" * 60)
    print(f"ran:            {outcome['ran']}")
    print(f"skipped_reason: {outcome['skipped_reason']}")
    if outcome["result"] is not None:
        _print_summary(outcome["result"], apply_mode)
    else:
        print("No evaluation_runs row was written for this skipped attempt.")
    print("=" * 60)


def _build_client(args):
    if not args.mock:
        return None
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

    return _MockClient()


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
    parser.add_argument(
        "--schedule-check", action="store_true",
        help="Read-only: print whether a scheduled evaluation is due now, the next due time, "
             "and the last successful scheduled run. Writes nothing. Ignores --apply/--mock.",
    )
    parser.add_argument(
        "--scheduled-dry-run", action="store_true",
        help="Run services.watchlist_scheduler.run_scheduled_evaluation(apply=False). Only "
             "evaluates if actually due and no run is in progress.",
    )
    parser.add_argument(
        "--scheduled-apply", action="store_true",
        help="Same as --scheduled-dry-run but apply=True. Still requires --yes.",
    )
    args = parser.parse_args()

    if args.schedule_check:
        _schedule_check(args)
        return

    if args.scheduled_dry_run:
        _run_scheduled(args, apply_mode=False)
        return

    if args.scheduled_apply:
        if not args.yes:
            print("[dry_run_evaluation] --scheduled-apply was given without --yes — refusing. "
                  "Pass --scheduled-apply --yes to actually allow a scheduled apply attempt.")
            return
        _run_scheduled(args, apply_mode=True)
        return

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

    client = _build_client(args)
    result = run_watchlist_evaluation(apply=apply_mode, client=client, triggered_by="manual-cli")
    _print_summary(result, apply_mode)


if __name__ == "__main__":
    main()
