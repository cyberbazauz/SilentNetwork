@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem SilentNetwork Acoustic Lab SN-016 launcher.
rem Default: light GUI, system speaker is selected automatically.
rem Playback: PLAY after generating a signal and confirming the dialog.
rem The --hardware-test flag is kept for compatibility and no longer required.
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

"%PYTHON%" -c "import sounddevice" >nul 2>nul
if errorlevel 1 (
    echo [SN-016] Warning: sounddevice is missing; playback will be unavailable.
    echo         Run:
    echo             "%PYTHON%" -m pip install -r requirements.txt
)

"%PYTHON%" desktop_app.py %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
    echo.
    echo [SN-015] desktop_app.py exited with code %CODE%.
    pause
)
exit /b %CODE%
