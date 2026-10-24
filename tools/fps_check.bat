@echo off
cd /d "%~dp0.."
".venv\Scripts\python.exe" tools\fps_check.py > logs\fps_check_log.txt 2>&1
echo DONE>> logs\fps_check_log.txt
