@echo off
REM Task Scheduler wrapper: keeps the webapp alive for the life of the
REM machine. Restarts "python run.py" whenever it exits, for ANY reason
REM (crash, kill, normal exit). Mirrors scripts\run_bot_supervisor.cmd's
REM shape, as a SEPARATE new file -- nothing under the bot's own
REM scripts\ is touched.
REM
REM Uses webapp\backend\.venv directly (not "where python" on PATH) --
REM this must always be the webapp's own venv, never whatever Python
REM happens to be on PATH for this account.
REM
REM Logs outside the repo (C:\stocksage-deploy\logs\webapp_supervisor.log)
REM so an in-repo log file can never make the bot's auto_sync.ps1 see a
REM dirty tree and skip a cycle. Rotated at 5 MB (renamed to .log.1,
REM overwriting any previous .1) since there's no new dependency to
REM reach for (no logrotate on Windows).

cd /d "%~dp0..\backend"

set PYTHON_EXE=%~dp0..\backend\.venv\Scripts\python.exe
set LOG_DIR=C:\stocksage-deploy\logs
set LOG_FILE=%LOG_DIR%\webapp_supervisor.log
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

if not exist "%PYTHON_EXE%" (
    echo ===== [%date% %time%] FATAL: %PYTHON_EXE% not found -- create webapp\backend\.venv first ===== >> "%LOG_FILE%"
    exit /b 1
)

:loop
call :rotate_log_if_large
echo ===== [%date% %time%] run.py starting ===== >> "%LOG_FILE%"
"%PYTHON_EXE%" run.py >> "%LOG_FILE%" 2>&1
echo ===== [%date% %time%] run.py exited with code %errorlevel% -- restarting in 10s ===== >> "%LOG_FILE%"
REM timeout can exit immediately when run with no console attached (as
REM under Task Scheduler), turning a failing start into a hot loop; ping
REM always blocks for the full duration regardless of console presence.
ping -n 11 127.0.0.1 >nul
goto loop

:rotate_log_if_large
if not exist "%LOG_FILE%" exit /b 0
for %%F in ("%LOG_FILE%") do set LOG_SIZE=%%~zF
if %LOG_SIZE% GTR 5242880 (
    if exist "%LOG_FILE%.1" del "%LOG_FILE%.1"
    ren "%LOG_FILE%" "webapp_supervisor.log.1"
)
exit /b 0
