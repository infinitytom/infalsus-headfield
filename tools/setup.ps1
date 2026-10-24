# Creates an isolated .venv inside the project folder and installs dependencies.
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
New-Item -ItemType Directory -Force logs, models, profiles | Out-Null
Start-Transcript -Path "logs\setup_log.txt" -Force | Out-Null

# Find a Python 3.10+ : prefer the py launcher (3.13, 3.12, ...), then python on PATH.
$pycmd = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
  foreach ($v in '3.13', '3.12', '3.11', '3.14', '3.10') {
    & py "-$v" -c "import sys" 2>$null
    if ($LASTEXITCODE -eq 0) { $pycmd = @('py', "-$v"); break }
  }
}
if (-not $pycmd -and (Get-Command python -ErrorAction SilentlyContinue)) {
  & python -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] else 1)" 2>$null
  if ($LASTEXITCODE -eq 0) { $pycmd = @('python') }
}
if (-not $pycmd) {
  Write-Host "ERROR: Python 3.10+ not found."
  Write-Host "Install Python 3.13 (64-bit) from https://www.python.org/downloads/windows/"
  Write-Host "  or run:  winget install Python.Python.3.13"
  Write-Host "then run this installer again."
  Stop-Transcript; exit 1
}
Write-Host ("Using: " + ($pycmd -join ' '))

if (-not (Test-Path ".venv\Scripts\python.exe")) {
  $exe = $pycmd[0]; $rest = @(); if ($pycmd.Length -gt 1) { $rest = $pycmd[1..($pycmd.Length-1)] }
  & $exe @rest -m venv .venv
}
$vpy = Join-Path $root ".venv\Scripts\python.exe"

& $vpy -m pip install --upgrade pip --disable-pip-version-check
& $vpy -m pip install -r requirements.txt --disable-pip-version-check
if ($LASTEXITCODE -ne 0) {
  Write-Host "Default index failed, retrying with Tsinghua mirror..."
  & $vpy -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --disable-pip-version-check
}

$model = "models\face_landmarker.task"
if (-not (Test-Path $model)) {
  $url = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
  try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $model -UseBasicParsing
    Write-Host "model downloaded"
  } catch {
    Write-Host "MODEL DOWNLOAD FAILED: $($_.Exception.Message)"
    Write-Host "Download manually from $url and put it at $root\$model"
  }
}
if (Test-Path $model) { Write-Host ("model size: " + (Get-Item $model).Length + " bytes") }

& $vpy -c "import cv2, mediapipe, numpy; print('OK cv2', cv2.__version__, 'mediapipe', mediapipe.__version__, 'numpy', numpy.__version__)"
Write-Host "SETUP FINISHED"
Stop-Transcript | Out-Null
