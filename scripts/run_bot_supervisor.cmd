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

set PYTHON_EXE=C:\Users\FIBI\AppData\Local\Programs\Python\Python313\python.exe
if not exist "%PYTHON_EXE%" set PYTHON_EXE=python

:loop
echo ===== [%date% %time%] main.py starting ===== >> logs\bot_supervisor.log
"%PYTHON_EXE%" main.py >> logs\bot_supervisor.log 2>&1
echo ===== [%date% %time%] main.py exited with code %errorlevel% -- restarting in 10s ===== >> logs\bot_supervisor.log
timeout /t 10 /nobreak >nul
goto loop
