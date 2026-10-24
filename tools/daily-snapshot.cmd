@echo off
rem Daily backups for every DSH target in config.json (config snapshot + sessions).
rem ASCII only. Uses pythonw so no console flash.
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
set PYW=pythonw.exe
where pythonw >nul 2>nul || set PYW=python

if not exist logs mkdir logs
echo [%DATE% %TIME%] daily backup>>logs\daily-snapshot.log
"%PYW%" tools\backup_all.py >>logs\daily-snapshot.log 2>&1
exit /b %ERRORLEVEL%
