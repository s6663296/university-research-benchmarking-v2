@echo off
rem Check that in-set self-citation exclusion matches the SciVal web Publication Set numbers
chcp 65001 >nul
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
    echo Please run start.bat once first to create the virtual environment.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -X utf8 scripts\check_pubset_selfcite.py %*
echo.
pause
