$ErrorActionPreference = "Stop"

Write-Host "[smoke] pytest"
python -m pytest -q

Write-Host "[smoke] ruff"
python -m ruff check .

Write-Host "[smoke] layout diagnostics"
python main.py --config examples/configs/logical-1x6-physical-3x2.json --node-name A --layout-diagnostics

Write-Host "[smoke] onefile windowed build"
python -m PyInstaller --noconfirm --onefile --windowed main.py --name multi-controller --distpath build/dist --workpath build/pyinstaller --specpath build/spec

if (-not (Test-Path build/dist/multi-controller.exe)) {
    throw "build/dist/multi-controller.exe was not created"
}

Write-Host "[smoke] complete"
