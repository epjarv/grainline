$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

python -m pip install -r requirements.txt -r requirements-build.txt
python scripts/make_icon.py
python -m PyInstaller --noconfirm --clean Grainline.spec

Write-Host "Built dist/Grainline.exe"
