@echo off
rem Keep this file ASCII-only: cmd.exe parses batch files in the OEM codepage.
chcp 65001 >nul
cd /d "%~dp0"

set PYTHONIOENCODING=utf-8
set PY=python
where python >nul 2>nul || set PY=py -3
where %PY% >nul 2>nul || echo ERROR: python not found on PATH && exit /b 127

echo === [1/7] unit tests: lkg ===
%PY% tests\test_lkg.py
echo.
echo === [2/7] unit tests: preflight ===
%PY% tests\test_preflight.py
echo.
echo === [3/7] unit tests: guard ===
%PY% tests\test_guard.py
echo.
echo === [4/7] unit tests: sessions backup ===
%PY% tests\test_sessions.py
echo.
echo === [5/7] unit tests: multi target ===
%PY% tests\test_multi_target.py
echo.
echo === [6/7] end-to-end + replay ===
%PY% tests\test_rescue_e2e.py
%PY% tests\replay.py --quiet
echo replay exit %ERRORLEVEL%
echo.
echo === [7/7] preflight on current config ===
%PY% preflight.py
echo.
pause
