@echo off
rem Keep this file ASCII-only: cmd.exe parses batch files in the OEM codepage.
chcp 65001 >nul
cd /d "%~dp0"

set PYTHONIOENCODING=utf-8
set PY=python
where python >nul 2>nul || set PY=py -3
where %PY% >nul 2>nul || echo ERROR: python not found on PATH && exit /b 127

set RC=0

echo === [1/8] unit tests: lkg ===
%PY% tests\test_lkg.py
if errorlevel 1 set RC=1
echo.

echo === [2/8] unit tests: preflight ===
%PY% tests\test_preflight.py
if errorlevel 1 set RC=1
echo.

echo === [3/8] unit tests: guard ===
%PY% tests\test_guard.py
if errorlevel 1 set RC=1
echo.

echo === [4/8] unit tests: sessions backup ===
%PY% tests\test_sessions.py
if errorlevel 1 set RC=1
echo.

echo === [5/8] unit tests: multi target ===
%PY% tests\test_multi_target.py
if errorlevel 1 set RC=1
echo.

echo === [6/8] end-to-end: rescue drill ===
%PY% tests\test_rescue_e2e.py
if errorlevel 1 set RC=1
echo.

echo === [7/8] replay historical runs ===
%PY% tests\replay.py --quiet
if errorlevel 1 set RC=1
echo.

echo === [8/8] preflight on current config ===
%PY% preflight.py
if errorlevel 2 set RC=1
echo.

echo ============================================================
if "%RC%"=="0" goto PASS

:FAIL
echo RESULT: FAILED  -  see the step with errors above
goto TAIL

:PASS
echo RESULT: ALL PASS  -  6 test groups + replay + preflight

:TAIL
echo ============================================================
echo.
echo Tip: python guard.py --status      health in one screen
echo      python detect.py --all        both DSH instances
echo      python detect.py --all --csv  export for spreadsheets
echo.
pause
exit /b %RC%
