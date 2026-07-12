@echo off
REM Task Scheduler wrapper for the nightly watchlist evaluation (APPLY mode).
REM Runs from the repo root and appends the full evaluation summary to
REM logs\watchlist_evaluation_task.log, preserving the python exit code.
REM
REM --scheduled-apply --yes is the confirmed integration point (2026-07-12):
REM services.watchlist_scheduler.run_scheduled_evaluation(apply=True) with
REM ALL of its guards active -- concurrency + stuck-run sweep, US-market-day
REM check, after-17:30-ET threshold, once-per-market-day. A skipped attempt
REM (weekend/holiday/already ran) writes nothing and logs the reason.
REM WATCHLIST_SCHEDULE_APPLY deliberately stays false -- apply intent lives
REM HERE, explicitly, not in a global env default.
REM Safety net: hysteresis, provider-outage suppression, /watchlist_changes
REM audit trail, scripts/rollback_evaluation_run.py. No circuit breaker by
REM design -- the user reviews each morning.

cd /d "%~dp0.."
if not exist logs mkdir logs

echo ===== [%date% %time%] watchlist scheduled-apply starting ===== >> logs\watchlist_evaluation_task.log
python scripts\dry_run_evaluation.py --scheduled-apply --yes >> logs\watchlist_evaluation_task.log 2>&1
set rc=%errorlevel%
echo ===== [%date% %time%] finished, exit code %rc% ===== >> logs\watchlist_evaluation_task.log
exit /b %rc%
