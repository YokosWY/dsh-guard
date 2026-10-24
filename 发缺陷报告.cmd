@echo off
rem ASCII only. Prepares the bug report: clipboard + opens GitHub Discussions.
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\send_report.ps1"
echo.
pause
