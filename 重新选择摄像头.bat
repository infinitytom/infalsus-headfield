@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" src\headfield.py --choose-camera %*
if errorlevel 1 pause
