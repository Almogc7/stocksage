@echo off
REM Task Scheduler wrapper for the nightly composite-signal logging job.
REM Runs from the repo root, appends all output to logs\composite_signals_task.log,
REM and preserves the python exit code as the task's Last Run Result.
REM (Detailed per-symbol logging also lands in logs\stocksage.log via logging_setup.)
REM
REM Silent, observation-mode-only: no Telegram, no live-alert-path writes.
REM Scheduled BEFORE the 02:30 watchlist evaluation so "ACTIVE tier" here
REM reflects today's session, not tonight's promotions/demotions.

cd /d "%~dp0.."
if not exist logs mkdir logs

echo ===== [%date% %time%] log_composite_signals starting ===== >> logs\composite_signals_task.log
python scripts\log_composite_signals.py >> logs\composite_signals_task.log 2>&1
set rc=%errorlevel%
echo ===== [%date% %time%] finished, exit code %rc% ===== >> logs\composite_signals_task.log
exit /b %rc%
