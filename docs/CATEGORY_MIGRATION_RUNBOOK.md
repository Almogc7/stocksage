# Category migration runbook (PR B: English category names)

Manual, human-run procedure for `scripts/migrate_category_names.py`
against the real `db/stocksage.db`. This is the only place in the
"English category names" rollout (PR A → PR A.2 → PR B → later PR C)
that touches the live file — everything in the script's own test suite
(`tests/test_migrate_category_names.py`) runs against temp DBs only.

**PR A and PR A.2 must already be live on the server before this runbook
is run.** `add_to_watchlist()`'s canonicalize-on-write fix (PR A.2) is
what stops `/add` from silently re-Hebraizing a symbol's category after
this migration renames it — running this migration before that fix is
deployed would get incrementally undone by ordinary `/add` use.

**Run every command below from the repo root on the server
(`C:\stocksage`)** — `cd C:\stocksage` first, in the same PowerShell
session, before any of the commands in this runbook.

**Do not merge or pull anything into this repo while the bot is stopped**
(steps 1–8 below). The server's auto-sync restarts the "StockSage Bot"
task itself whenever it pulls a new commit — a merge landing mid-runbook
would silently bring the bot back up before you've finished verifying
the migration, and would also fight with your own manual stop/restart.

---

## 0. Pre-flight — on a disposable COPY of the webapp snapshot, not the live file

Before touching the real database at all, exercise the exact same script
against a copy of the periodic webapp snapshot
(`webapp/deploy/snapshot_bot_db.py`'s output, default
`C:\stocksage-deploy\snapshot\bot.db`, refreshed every 10 minutes by its
own Scheduled Task) — this is real, production-shaped data (actual row
counts, actual stray categories `--audit` might surface) that synthetic
test fixtures can't fully substitute for, and it carries zero risk since
it's a disposable copy of an already-read-only artifact.

1. Copy the snapshot file to a disposable temp path, e.g.:
   ```
   copy C:\stocksage-deploy\snapshot\bot.db C:\Temp\bot-snapshot-check.db
   ```
2. Dry run against the copy:
   ```
   python scripts\migrate_category_names.py --db-path C:\Temp\bot-snapshot-check.db
   ```
   Read the printed before/predicted-after count table.
3. Apply against the *copy* (no `--i-have-a-backup` needed — `--db-path`
   means it's not the default repo path):
   ```
   python scripts\migrate_category_names.py --db-path C:\Temp\bot-snapshot-check.db --apply
   ```
4. **Compare both count tables by eye against what step 3 of the real
   procedure below will show.** They should match closely (the snapshot
   is at most ~10 minutes stale relative to the live file).
5. Delete the temp copy and its own backup file when done; none of this
   touched the live database.

---

## 1. The real migration

1. **Stop** the "StockSage Bot" scheduled task (the task that runs
   `main.py`). Confirm no `/refresh_watchlist ... apply confirm` run is
   in flight.

   **Verify the process is actually gone** — stopping the scheduled task
   does not guarantee the process it launched has exited yet:
   ```powershell
   Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object CommandLine -match 'main.py'
   ```
   If this returns anything, the bot is still running — stop that
   process (e.g. `Stop-Process -Id <id>`) before continuing. Do not
   proceed to step 3 while this command still returns a result.
2. Confirm the time is **outside 01:30–03:00 IL** (the three nightly
   jobs' window: `log_composite_signals.py` 01:30, `populate_outcomes.py`
   02:00, the watchlist evaluation's scheduled apply 02:30).
3. **Dry run** against the real path:
   ```
   python scripts\migrate_category_names.py
   ```
   (No `--db-path` → defaults to the repo's `db/stocksage.db`. No write
   happens regardless — the connection is opened `mode=ro`.) Read the
   before/predicted-after count table.
4. **Apply**:
   ```
   python scripts\migrate_category_names.py --apply --i-have-a-backup
   ```
   The script takes its own online backup first (verified — row counts
   for both tables, plus `PRAGMA integrity_check` — before any write
   happens), then renames both tables in one transaction, verifies the
   result, then reprints the count table with actual after-counts.
5. **Verify counts** — old-name counts are 0, new-name counts match the
   dry run's prediction, per category. (The script's own internal
   verification, inside the transaction before commit, already checked
   this — this step is your own independent visual confirmation on top
   of that.)
6. **Audit**:
   ```
   python scripts\migrate_category_names.py --audit
   ```
   Confirm it lists only genuinely-stray categories you already know
   about (if any) — never something the rename should have caught.
7. **Confirm `git status` is still clean** on the server:
   ```
   git status --porcelain
   ```
   This should print nothing. The migration only ever touches the DB
   file (already gitignored) and creates a gitignored `.bak-*` sibling
   file — if this prints anything else, stop and investigate before
   restarting the bot or resuming auto-sync, since a dirty tree is
   exactly what makes the server's auto-sync skip every pull cycle.
8. **Restart** the "StockSage Bot" scheduled task.
9. **Check the bot**: send `/watchlist_active` and confirm categories
   display correctly (English names, no blank/garbled categories, counts
   look right).

## Rollback

If anything looks wrong at any point after step 4: stop the bot task
again (re-verify with the same `Get-CimInstance` check from step 1 that
the process actually exited), restore the timestamped backup file
(`db/stocksage.db.bak-<timestamp>`, printed by the script in step 4) over
`db/stocksage.db`, restart the task. No partial-state repair is ever
needed — the rename is one transaction with its own pre-commit
verification, so the live file is always either fully old names or
fully new names; restoring the backup is the only rollback action
required.
