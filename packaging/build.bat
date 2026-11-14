@echo off
rem Build dist\ResearchBenchmarking (PyInstaller) and the installer (Inno Setup 6)
chcp 65001 >nul
cd /d "%~dp0.."

set "PY=build\venv\Scripts\python.exe"
if not exist "%PY%" (
    echo Creating build environment...
    if exist ".venv\Scripts\python.exe" (
        ".venv\Scripts\python.exe" -m venv build\venv
    ) else (
        py -3 -m venv build\venv
    )
    if errorlevel 1 goto fail
)
"%PY%" -m pip install --quiet --upgrade pip
"%PY%" -m pip install --quiet ".[desktop]" pyinstaller
if errorlevel 1 goto fail

"%PY%" packaging\make_icon.py
if errorlevel 1 goto fail

"%PY%" -m PyInstaller --noconfirm --clean --distpath dist --workpath build\pyinstaller packaging\research_benchmarking.spec
if errorlevel 1 goto fail

for /f "usebackq delims=" %%V in (`%PY% -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])"`) do set "VER=%%V"

set "ISCC="
for %%D in ("%LOCALAPPDATA%\Programs\Inno Setup 6" "%ProgramFiles(x86)%\Inno Setup 6" "%ProgramFiles%\Inno Setup 6") do (
    if not defined ISCC if exist "%%~D\ISCC.exe" set "ISCC=%%~D\ISCC.exe"
)
if not defined ISCC (
    echo.
    echo Inno Setup 6 not found; skipped the installer. The app folder is dist\ResearchBenchmarking.
    echo Install it with: winget install JRSoftware.InnoSetup
    exit /b 0
)
"%ISCC%" /Qp /DAppVersion=%VER% packaging\installer.iss
if errorlevel 1 goto fail
echo.
echo Done: dist\ResearchBenchmarking-Setup-%VER%.exe
exit /b 0

:fail
echo.
echo Build failed, see the messages above.
exit /b 1
