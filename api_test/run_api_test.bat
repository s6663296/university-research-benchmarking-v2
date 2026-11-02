@echo off
rem Elsevier API test launcher
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
    echo Python not found. Please install Python 3.8+ from https://www.python.org/
    pause
    exit /b 1
)

%PY% -X utf8 elsevier_api_test.py
echo.
pause
