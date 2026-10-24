@echo off
rem Runner for the scheduled task: wait for DSH exit, verify safe mode, relaunch DSH normally.
rem ASCII only. No console flash because the task runs hidden via wscript-free pythonw.
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
set PYW=pythonw.exe
where pythonw >nul 2>nul || set PYW=python

if not exist logs mkdir logs
echo [%DATE% %TIME%] scheduled verify start>>logs\wait-verify.log
"%PYW%" tools\wait_and_verify.py --timeout 7200 --relaunch >>logs\wait-verify.log 2>&1
echo [%DATE% %TIME%] scheduled verify end rc=%ERRORLEVEL%>>logs\wait-verify.log
exit /b %ERRORLEVEL%
