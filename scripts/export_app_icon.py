"""Export the Qt-generated app icon to a Windows .ico file."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.meta.icon import build_app_icon
from app.meta.identity import APP_ICON_PATH


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_target(path: str) -> Path:
    target = Path(path)
    if not target.is_absolute():
        target = PROJECT_ROOT / target
    return target


def export_icon(target: Path) -> None:
    existing_app = QApplication.instance()
    app = existing_app or QApplication([])
    target.parent.mkdir(parents=True, exist_ok=True)
    pixmap = build_app_icon(256).pixmap(256, 256)
    if not pixmap.save(str(target), "ICO"):
        raise RuntimeError(f"failed to save icon to {target}")
    if existing_app is None:
        app.quit()


def main() -> None:
    export_icon(resolve_target(APP_ICON_PATH))


if __name__ == "__main__":
    main()
