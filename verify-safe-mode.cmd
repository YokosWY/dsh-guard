@echo off
rem ASCII only. Verify DSH safe mode. Requires DSH to be fully closed.
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
set PY=python
where python >nul 2>nul || set PY=py -3
where %PY% >nul 2>nul || echo ERROR: python not found on PATH && exit /b 127

%PY% tools\verify_safe_mode.py %*
echo.
pause
