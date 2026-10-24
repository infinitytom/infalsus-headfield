@echo off
cd /d "%~dp0"
rem Lists camera NAMES only; does not open any camera.
".venv\Scripts\python.exe" src\headfield.py --list-cameras
pause
