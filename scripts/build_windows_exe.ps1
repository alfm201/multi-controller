$ErrorActionPreference = "Stop"

Write-Host "[build] export app icon"
python scripts/export_app_icon.py

if (-not (Test-Path assets/multi-screen-pass.ico)) {
    throw "assets/multi-screen-pass.ico was not created"
}
$iconPath = (Resolve-Path assets/multi-screen-pass.ico).Path

Write-Host "[build] PyInstaller onefile windowed build"
python -m PyInstaller --noconfirm --onefile --windowed main.py --name MultiScreenPass --icon $iconPath --distpath build/dist --workpath build/pyinstaller --specpath build/spec

if (-not (Test-Path build/dist/MultiScreenPass.exe)) {
    throw "build/dist/MultiScreenPass.exe was not created"
}

Write-Host "[build] complete -> build/dist/MultiScreenPass.exe"
