@echo off
rem ASCII only. Prints guard-ui diagnostics.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PY=python
where python >nul 2>nul || set PY=py -3
where %PY% >nul 2>nul || echo ERROR: python not found on PATH && exit /b 127

%PY% tools\diag.py %*
echo.
pause
