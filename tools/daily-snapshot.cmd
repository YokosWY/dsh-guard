@echo off
rem Daily backups: config LKG (small, deduped) + sessions (incremental, hardlinked).
rem ASCII only. Uses pythonw so no console flash.
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
set PYW=pythonw.exe
where pythonw >nul 2>nul || set PYW=python

if not exist logs mkdir logs
echo [%DATE% %TIME%] daily backup>>logs\daily-snapshot.log
"%PYW%" lkg.py snapshot --reason daily --skip-identical >>logs\daily-snapshot.log 2>&1
"%PYW%" sessions.py snapshot >>logs\daily-snapshot.log 2>&1
exit /b %ERRORLEVEL%
