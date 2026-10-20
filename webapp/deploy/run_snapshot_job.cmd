@echo off
REM Task Scheduler wrapper for webapp\deploy\snapshot_bot_db.py. All
REM real logging happens inside the script itself (-> SNAPSHOT_LOG_PATH,
REM default C:\stocksage-deploy\logs\snapshot.log); this wrapper only
REM preserves the exit code as the task's Last Run Result.

set PYTHON_EXE=%~dp0..\backend\.venv\Scripts\python.exe

if not exist "%PYTHON_EXE%" (
    echo %PYTHON_EXE% not found -- create webapp\backend\.venv first 1>&2
    exit /b 1
)

"%PYTHON_EXE%" "%~dp0snapshot_bot_db.py"
set rc=%errorlevel%
exit /b %rc%
