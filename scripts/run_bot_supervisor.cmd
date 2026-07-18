@echo off
REM Task Scheduler wrapper: keeps main.py alive for the life of the machine.
REM Restarts main.py whenever it exits, for ANY reason (crash, kill, normal
REM exit) -- this is the process-level crash-recovery layer. Every
REM start/stop is logged to logs\bot_supervisor.log.
REM
REM The auto-sync task (scripts\auto_sync.ps1) restarts the bot by ending
REM and re-running the "StockSage Bot" scheduled task, which kills this
REM whole process tree (this .cmd + its python.exe child) -- this loop then
REM simply relaunches on its own next iteration. Do not add exit conditions
REM here; the loop running forever is the point.

cd /d "%~dp0.."
if not exist logs mkdir logs

REM Resolve python.exe dynamically from PATH so this never breaks when the
REM machine, Windows account, or Python version changes. "where" returns one
REM full path per line if several pythons are on PATH; take the first.
set PYTHON_EXE=python
for /f "delims=" %%P in ('where python 2^>nul') do (
    set PYTHON_EXE=%%P
    goto :python_resolved
)
:python_resolved

:loop
echo ===== [%date% %time%] main.py starting ===== >> logs\bot_supervisor.log
"%PYTHON_EXE%" main.py >> logs\bot_supervisor.log 2>&1
echo ===== [%date% %time%] main.py exited with code %errorlevel% -- restarting in 10s ===== >> logs\bot_supervisor.log
timeout /t 10 /nobreak >nul
goto loop
