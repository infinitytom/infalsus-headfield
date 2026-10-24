@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Environment not installed. Run 1_install first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" src\headfield.py %*
if errorlevel 1 pause
