@echo off
REM Task Scheduler wrapper for the nightly alert-outcome population job.
REM Runs from the repo root, appends all output to logs\populate_outcomes_task.log,
REM and preserves the python exit code as the task's Last Run Result.
REM (Detailed per-symbol logging also lands in logs\stocksage.log via logging_setup.)

cd /d "%~dp0.."
if not exist logs mkdir logs

echo ===== [%date% %time%] populate_outcomes starting ===== >> logs\populate_outcomes_task.log
python scripts\populate_outcomes.py >> logs\populate_outcomes_task.log 2>&1
set rc=%errorlevel%
echo ===== [%date% %time%] finished, exit code %rc% ===== >> logs\populate_outcomes_task.log
exit /b %rc%
