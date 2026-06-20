"""
Manual entrypoint for rolling back a successful apply-mode watchlist
evaluation run (Phase 5.5 — see services/watchlist_evaluator.rollback_evaluation_run).

Run from the stocksage/ directory:

    python scripts/rollback_evaluation_run.py --db db/stocksage.db --run-id 2 --yes

What it does:
  - Loads the audit trail (evaluation_run_changes) for the given run_id.
  - Refuses an unknown run_id, a dry-run run_id, or an already-rolled-back run.
  - Compares every audited symbol's CURRENT watchlist values against what
    the run wrote (new_values) — if anything differs (a manual edit, or a
    later run, touched the row since), the ENTIRE rollback is refused and
    every conflicting symbol is reported. Nothing is written in that case.
  - If there are no conflicts, restores every affected symbol's previous
    values and marks the audit rows rolled back, in ONE atomic transaction.
  - Records a new evaluation_runs row representing the rollback action.
  - Never sends a Telegram message — this script does not import the bot.

Safety:
  - Requires an explicit --run-id (no default).
  - Requires the explicit --yes confirmation flag — without it, this script
    only prints what WOULD be rolled back and exits without writing anything.
  - Pass --db <path> to point at a temporary/test SQLite file instead of
    the real production database. Strongly recommended until you've
    validated the rollback on a copy first.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--db", default=None,
        help="Path to a SQLite file instead of the real production db/stocksage.db. "
             "Strongly recommended until you've validated the rollback on a copy.",
    )
    parser.add_argument("--run-id", type=int, required=True, help="evaluation_runs.run_id to roll back.")
    parser.add_argument(
        "--yes", action="store_true",
        help="Required to actually write the rollback. Without it, only a preview is printed.",
    )
    args = parser.parse_args()

    import db.database as db
    if args.db:
        db.DB_PATH = Path(args.db)
        print(f"[rollback_evaluation_run] Using DB: {db.DB_PATH}")
    else:
        print(f"[rollback_evaluation_run] WARNING: using the real production DB: {db.DB_PATH}")

    changes = db.get_changes_for_run(args.run_id)
    run = db.get_evaluation_run(args.run_id)
    if run is None:
        print(f"[rollback_evaluation_run] No evaluation run with id {args.run_id}. Nothing to do.")
        return

    print(f"[rollback_evaluation_run] run_id={args.run_id}  run_type={run['run_type']}  "
          f"dry_run={bool(run['dry_run'])}  status={run['status']}")
    print(f"[rollback_evaluation_run] {len(changes)} audited symbol change(s) for this run: "
          f"{[c['symbol'] for c in changes]}")

    if not args.yes:
        print("[rollback_evaluation_run] Preview only (pass --yes to actually roll back). No changes written.")
        return

    from services.watchlist_evaluator import RollbackError, rollback_evaluation_run

    try:
        result = rollback_evaluation_run(args.run_id, triggered_by="manual-cli")
    except RollbackError as exc:
        print(f"[rollback_evaluation_run] REFUSED: {exc}")
        return

    print("=" * 60)
    print(f"status:            {result['status']}")
    print(f"run_id:            {result['run_id']}")
    print(f"rollback_run_id:   {result.get('rollback_run_id')}")
    print(f"restored_symbols:  {result['restored_symbols']}")
    if result["conflicts"]:
        print("conflicts (NOTHING was rolled back — resolve manually before retrying):")
        for c in result["conflicts"]:
            print(f"  - {c['symbol']}: {c['mismatched_columns']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
