@echo off
rem Research benchmarking system launcher
chcp 65001 >nul
cd /d "%~dp0"

rem ---- 1. Check the existing virtual environment (it breaks when Python is upgraded, moved or removed)
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
    if errorlevel 1 (
        echo The virtual environment is broken or uses Python older than 3.11. Recreating it...
        rmdir /s /q ".venv"
    )
)
if exist ".venv\Scripts\python.exe" goto install
if exist ".venv" rmdir /s /q ".venv"

echo First run: creating Python virtual environment...
call :find_python
if not defined PY (
    echo.
    echo Python 3.11 or newer was not found.
    echo Please install Python 3.12 from https://www.python.org/downloads/windows/
    echo and tick "Add python.exe to PATH" on the first screen of the installer.
    echo Note: the "python" shortcut that opens Microsoft Store does not work.
    echo.
    echo Diagnostics:
    where python 2>nul
    py -0p 2>nul
    pause
    exit /b 1
)
echo Using %PY%
%PY% -c "import sys; print(sys.executable, sys.version.split()[0])"
%PY% -m venv .venv
if errorlevel 1 goto fail
if not exist ".venv\Scripts\python.exe" (
    echo Creating the virtual environment did not produce .venv\Scripts\python.exe
    goto fail
)

:install
rem ---- 2. Install packages when missing (first run, a failed install, or new dependencies after an update)
".venv\Scripts\python.exe" -c "import app.main" >nul 2>nul
if not errorlevel 1 goto run
echo Installing packages, this can take a few minutes...
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -c "import app.main"
if errorlevel 1 goto fail

:run
if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Created .env - fill in ELSEVIER_API_KEY here or on the Settings page.
)
".venv\Scripts\python.exe" -X utf8 -m app
if errorlevel 1 (
    echo.
    echo The program stopped with an error. Please copy the messages above and send them to the developer.
)
pause
exit /b 0

:find_python
rem Each candidate is actually run, so broken PATH entries and the Microsoft Store shortcut are skipped.
set "PY="
for %%V in (3.12 3.13 3.11 3.14 3) do (
    if not defined PY (
        py -%%V -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul && set "PY=py -%%V"
    )
)
if defined PY exit /b 0
python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul && set "PY=python"
if defined PY exit /b 0
python3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul && set "PY=python3"
exit /b 0

:fail
echo.
echo Setup failed. Check the network connection (pip needs internet access) and the messages above.
echo If it keeps failing, delete the .venv folder and run start.bat again.
pause
exit /b 1
