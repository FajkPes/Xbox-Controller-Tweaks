@echo off
setlocal
title Xbox Controller Tweaks

if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  echo The local Python environment is not installed.
  choice /C YN /N /M "Run Setup.bat now? [Y/N] "
  if errorlevel 2 exit /b 1
  call "%~dp0Setup.bat"
  if errorlevel 1 exit /b 1
)

"%~dp0.venv\Scripts\python.exe" -c "import importlib.util, sys; packages=('customtkinter','pygame','vgamepad','pystray','PIL'); sys.exit(0 if all(importlib.util.find_spec(name) for name in packages) else 1)" >nul 2>nul
if errorlevel 1 (
  echo Required Python packages are missing. Run Setup.bat again.
  pause
  exit /b 1
)

sc.exe query ViGEmBus >nul 2>nul
if errorlevel 1 (
  echo The ViGEmBus driver is not installed.
  echo See README.md or run Setup.bat for the official download link.
  pause
  exit /b 1
)

"%~dp0.venv\Scripts\python.exe" -c "import vgamepad" >nul 2>nul
if errorlevel 1 (
  echo ViGEmBus is installed but vgamepad cannot connect to it.
  echo Repair or reinstall ViGEmBus v1.22.0, restart Windows, and try again.
  pause
  exit /b 1
)

start "Xbox Controller Tweaks" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0xbox_controller_tweaks.py"
exit /b 0

rem Created by FajkPes with assistance from ChatGPT Codex and Claude Code.
