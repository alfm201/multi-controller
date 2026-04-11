$ErrorActionPreference = "Stop"

Write-Host "[smoke] pytest"
python -m pytest -q

Write-Host "[smoke] ruff"
python -m ruff check .

Write-Host "[smoke] layout diagnostics"
python main.py --config examples/configs/logical-1x6-physical-3x2.json --node-name A --layout-diagnostics

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

Write-Host "[smoke] onefile windowed build"
python -m PyInstaller --noconfirm --onefile --windowed main.py --name multi-controller --distpath build/dist --workpath build/pyinstaller --specpath build/spec

if (-not (Test-Path build/dist/multi-controller.exe)) {
    throw "build/dist/multi-controller.exe was not created"
}

Write-Host "[smoke] complete"
