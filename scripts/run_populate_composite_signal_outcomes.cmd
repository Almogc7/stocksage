@echo off
REM Task Scheduler wrapper for the nightly composite-signal-outcome
REM population job. Runs from the repo root, appends all output to
REM logs\composite_outcomes_task.log, and preserves the python exit code
REM as the task's Last Run Result.
REM (Detailed per-symbol logging also lands in logs\stocksage.log via logging_setup.)

cd /d "%~dp0.."
if not exist logs mkdir logs

echo ===== [%date% %time%] populate_composite_signal_outcomes starting ===== >> logs\composite_outcomes_task.log
python scripts\populate_composite_signal_outcomes.py >> logs\composite_outcomes_task.log 2>&1
set rc=%errorlevel%
echo ===== [%date% %time%] finished, exit code %rc% ===== >> logs\composite_outcomes_task.log
exit /b %rc%
