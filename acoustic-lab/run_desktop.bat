@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem SilentNetwork Acoustic Lab SN-015 launcher.
rem Default: FILE-ONLY GUI, no sound is played.
rem Hardware playback: run_desktop.bat --hardware-test
rem Smoke test:          run_desktop.bat --smoke-test

set "PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON=%~dp0.venv\Scripts\python.exe"

"%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [SN-015] Python 3.10 or newer was not found.
    echo         Install Python 3.12 with Tcl/Tk and add it to PATH.
    pause
    exit /b 3
)

"%PYTHON%" -c "import numpy, scipy, tkinter" >nul 2>nul
if errorlevel 1 (
    echo [SN-015] Required Python packages are missing.
    echo         Run:
    echo             "%PYTHON%" -m pip install -r requirements.txt
    pause
    exit /b 4
)

"%PYTHON%" -c "import matplotlib" >nul 2>nul
if errorlevel 1 (
    echo [SN-015] Warning: matplotlib is missing; PSD and spectrogram PNG will be skipped.
)

if /i not "%~1"=="" (
    echo %* | findstr /C:"--hardware-test" >nul
    if not errorlevel 1 (
        "%PYTHON%" -c "import sounddevice" >nul 2>nul
        if errorlevel 1 (
            echo [SN-015] sounddevice is required for --hardware-test.
            echo         Run:
            echo             "%PYTHON%" -m pip install -r requirements.txt
            pause
            exit /b 4
        )
    )
)

"%PYTHON%" desktop_app.py %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
    echo.
    echo [SN-015] desktop_app.py exited with code %CODE%.
    pause
)
exit /b %CODE%
