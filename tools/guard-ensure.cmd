@echo off
rem Guard watchdog: every 5 minutes, start it only if enabled and not running.
rem Uses pythonw so no console window flashes. Log keeps the last few lines.
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
set PYW=pythonw.exe
where pythonw >nul 2>nul || set PYW=python

"%PYW%" guard.py --ensure >>logs\guard-ensure.log 2>&1
exit /b %ERRORLEVEL%
