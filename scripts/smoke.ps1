$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
$iconRelativePath = "assets/multi-screen-pass.ico"
$iconPath = Join-Path $repoRoot $iconRelativePath
$distPath = Join-Path $repoRoot "build/dist"
$workPath = Join-Path $repoRoot "build/pyinstaller"
$specPath = Join-Path $repoRoot "build/spec"
$recoveryWorkPath = Join-Path $repoRoot "build/pyinstaller-recovery"
$recoverySpecPath = Join-Path $repoRoot "build/spec-recovery"
$mainPath = Join-Path $repoRoot "main.py"
$recoveryScriptPath = Join-Path $repoRoot "scripts/mouse_unlock_tool.py"
$exportScriptPath = Join-Path $repoRoot "scripts/export_app_icon.py"
$recoveryBuildName = "MouseUnlockRecovery"

function Get-RecoveryOutputName {
    return (
        -join @(
            [char]0x5B, [char]0xC7A5, [char]0xC560, [char]0xBCF5, [char]0xAD6C, [char]0xC6A9, [char]0x5D,
            [char]0x20,
            [char]0xB9C8, [char]0xC6B0, [char]0xC2A4,
            [char]0x20,
            [char]0xC7A0, [char]0xAE08,
            [char]0x20,
            [char]0xD574, [char]0xC81C,
            ".exe"
        )
    )
}

$recoveryOutputName = Get-RecoveryOutputName

Push-Location $repoRoot
try {
Write-Host "[smoke] pytest"
python -m pytest -q

Write-Host "[smoke] ruff"
python -m ruff check .

Write-Host "[smoke] layout diagnostics"
python $mainPath --config examples/configs/logical-1x6-physical-3x2.json --node-name A --layout-diagnostics

Write-Host "[smoke] qt gui boot"
@'
import os
import sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from runtime.qt_app import QtRuntimeApp
from runtime.config_loader import load_config
from runtime.context import build_runtime_context
from network.peer_registry import PeerRegistry

config, path = load_config("config/config.json")
ctx = build_runtime_context(config, override_name="A", config_path=path)
registry = PeerRegistry()

app = QApplication.instance() or QApplication([])
QTimer.singleShot(200, app.quit)
ui = QtRuntimeApp(
    ctx=ctx,
    registry=registry,
    coordinator_resolver=lambda: ctx.get_node("A"),
    ui_mode="gui",
)
ui.run(lambda: None)
print("qt-gui-ok")
'@ | python -

Write-Host "[smoke] qt tray boot"
@'
import os
import sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from runtime.qt_app import QtRuntimeApp
from runtime.config_loader import load_config
from runtime.context import build_runtime_context
from network.peer_registry import PeerRegistry

config, path = load_config("config/config.json")
ctx = build_runtime_context(config, override_name="A", config_path=path)
registry = PeerRegistry()

app = QApplication.instance() or QApplication([])
QTimer.singleShot(200, app.quit)
ui = QtRuntimeApp(
    ctx=ctx,
    registry=registry,
    coordinator_resolver=lambda: ctx.get_node("A"),
    ui_mode="tray",
)
ui.run(lambda: None)
print("qt-tray-ok")
'@ | python -

Write-Host "[smoke] export app icon"
python $exportScriptPath

if (-not (Test-Path $iconPath)) {
    throw "$iconRelativePath was not created"
}

Write-Host "[smoke] onefile windowed build"
python -m PyInstaller --noconfirm --onefile --windowed $mainPath --name MultiScreenPass --icon $iconPath --hidden-import certifi --collect-data certifi --distpath $distPath --workpath $workPath --specpath $specPath

if (-not (Test-Path (Join-Path $distPath "MultiScreenPass.exe"))) {
    throw "build/dist/MultiScreenPass.exe was not created"
}

Write-Host "[smoke] recovery build"
python -m PyInstaller --noconfirm --onefile --windowed $recoveryScriptPath --name $recoveryBuildName --icon $iconPath --distpath $distPath --workpath $recoveryWorkPath --specpath $recoverySpecPath

$recoveryBuiltExePath = Join-Path $distPath "$recoveryBuildName.exe"
if (-not (Test-Path $recoveryBuiltExePath)) {
    throw "build/dist/$recoveryBuildName.exe was not created"
}

$recoveryExePath = Join-Path $distPath $recoveryOutputName
if (Test-Path -LiteralPath $recoveryExePath) {
    Remove-Item -LiteralPath $recoveryExePath -Force
}
[System.IO.File]::Move($recoveryBuiltExePath, $recoveryExePath)

if (-not (Test-Path -LiteralPath $recoveryExePath)) {
    throw "build/dist/$recoveryOutputName was not created"
}

Write-Host "[smoke] complete"
}
finally {
    Pop-Location
}
