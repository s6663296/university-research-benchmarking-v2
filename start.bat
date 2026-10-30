@echo off
rem Research benchmarking system launcher
chcp 65001 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto run

echo First run: creating Python virtual environment...
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    echo Python not found. Please install Python 3.11+ from https://www.python.org/
    pause
    exit /b 1
)
%PY% -m venv .venv
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto fail

:run
if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Created .env - fill in ELSEVIER_API_KEY here or on the Settings page.
)
".venv\Scripts\python.exe" -X utf8 -m app
pause
exit /b 0

:fail
echo Setup failed.
pause
exit /b 1
