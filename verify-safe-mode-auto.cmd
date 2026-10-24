@echo off
rem ASCII only. Waits for DSH to exit, then verifies --next-safe-mode and writes a report.
rem Double-click this, then close DSH. This window keeps running on its own.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PY=python
where python >nul 2>nul || set PY=py -3
where %PY% >nul 2>nul || echo ERROR: python not found on PATH && exit /b 127

echo.
echo   This window waits for DSH to exit, then verifies safe mode automatically.
echo   Close DSH now. After verification DSH is relaunched in normal mode.
echo.
%PY% tools\wait_and_verify.py --relaunch %*
set RC=%ERRORLEVEL%
echo.
echo ---------------- exit %RC% ----------------
pause
