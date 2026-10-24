# Headless check: lists cameras, then runs 8 s of tracking on the configured camera.
# No images are saved; only numbers go to logs\.
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$vpy = ".venv\Scripts\python.exe"
& $vpy src\headfield.py --list-cameras --report logs\cameras.json *> logs\selftest_log.txt
& $vpy src\headfield.py --selftest 8 --report logs\selftest_report.json *>> logs\selftest_log.txt
"DONE" | Out-File -Append logs\selftest_log.txt
