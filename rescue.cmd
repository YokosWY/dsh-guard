@echo off
rem Keep this file ASCII-only: cmd.exe parses batch files in the OEM codepage,
rem Chinese text here turns into mojibake. Chinese output comes from Python.
chcp 65001 >nul
cd /d "%~dp0"

set PYTHONIOENCODING=utf-8
set PY=python
where python >nul 2>nul || set PY=py -3
where %PY% >nul 2>nul || echo ERROR: python not found on PATH && exit /b 127

%PY% rescue.py %*
set RC=%ERRORLEVEL%

echo.
echo ---------------- exit %RC% ----------------
pause
exit /b %RC%
