@echo off
REM Task Scheduler wrapper for the auto-sync job (git pull + bot restart).
REM All real logging happens inside auto_sync.ps1 -> logs\auto_sync.log;
REM this wrapper only captures stray PowerShell errors and preserves the
REM exit code as the task's Last Run Result.

cd /d "%~dp0.."
if not exist logs mkdir logs

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0auto_sync.ps1" >> logs\auto_sync_task.log 2>&1
set rc=%errorlevel%
exit /b %rc%
