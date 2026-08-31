@echo off
setlocal
title Xbox Controller Tweaks - Setup

echo.
echo === Xbox Controller Tweaks setup ===
echo.

for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_find-python.ps1"`) do set "PYTHON=%%P"
if not defined PYTHON (
  echo ERROR: A supported 64-bit Python installation was not found.
  echo Install Python 3.12 x64 from the official website, then run Setup.bat again:
  echo   https://www.python.org/downloads/windows/
  echo.
  pause
  exit /b 1
)

echo Python:
echo   %PYTHON%
echo.

if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Creating the local Python environment...
  "%PYTHON%" -m venv "%~dp0.venv"
  if errorlevel 1 goto :package_error
)

echo Installing the required Python packages...
set "VGAMEPAD_SKIP_VIGEMBUS_INSTALL=true"
"%~dp0.venv\Scripts\python.exe" -m pip install --disable-pip-version-check --upgrade pip
if errorlevel 1 goto :package_error
"%~dp0.venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "%~dp0requirements.txt"
if errorlevel 1 goto :package_error

"%~dp0.venv\Scripts\python.exe" -c "import importlib.util, sys; packages=('customtkinter','pygame','vgamepad','pystray','PIL'); sys.exit(0 if all(importlib.util.find_spec(name) for name in packages) else 1)"
if errorlevel 1 goto :package_error

sc.exe query ViGEmBus >nul 2>nul
if errorlevel 1 goto :driver_missing

"%~dp0.venv\Scripts\python.exe" -c "import vgamepad" >nul 2>nul
if errorlevel 1 goto :driver_unavailable

echo.
echo Setup complete. ViGEmBus is installed and the app is ready.
echo Run Start Xbox Controller Tweaks.bat.
echo.
pause
exit /b 0

:driver_missing
echo.
echo Python packages are ready, but the required ViGEmBus driver was not found.
echo This retired driver must be installed manually from its official GitHub release.
echo The application cannot create a virtual Xbox controller without it.
echo.
choice /C YN /N /M "Open the official ViGEmBus v1.22.0 release page now? [Y/N] "
if errorlevel 2 exit /b 2
start "" "https://github.com/nefarius/ViGEmBus/releases/tag/v1.22.0"
exit /b 2

:driver_unavailable
echo.
echo Python packages are ready and the ViGEmBus service exists, but vgamepad
echo could not connect to the virtual controller bus.
echo Repair or reinstall ViGEmBus v1.22.0, restart Windows, and run Setup.bat again.
echo.
choice /C YN /N /M "Open the official ViGEmBus v1.22.0 release page now? [Y/N] "
if errorlevel 2 exit /b 2
start "" "https://github.com/nefarius/ViGEmBus/releases/tag/v1.22.0"
exit /b 2

:package_error
echo.
echo ERROR: The Python dependencies could not be installed.
echo Check your internet connection and run Setup.bat again.
echo.
pause
exit /b 1

rem Created by FajkPes with assistance from ChatGPT Codex and Claude Code.
