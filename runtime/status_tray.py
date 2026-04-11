"""Qt system tray helpers and wrapper."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


@dataclass(frozen=True)
class TrayTargetAction:
    node_id: str
    label: str
    enabled: bool
    selected: bool


def build_tray_title(view) -> str:
    coordinator = view.coordinator_id or "-"
    target = view.selected_target or "-"
    state = view.router_state or "-"
    return f"multi-controller [{view.self_id}] | 코디네이터 {coordinator} | 대상 {target} | 상태 {state}"


def build_tray_target_actions(view):
    actions = []
    for target in view.targets:
        parts = [target.node_id, "연결" if target.online else "오프라인"]
        if target.selected:
            parts.append(target.state or "선택")
        peer = next((item for item in view.peers if item.node_id == target.node_id), None)
        if peer is not None:
            parts.append(peer.freshness_label)
            if peer.has_monitor_diff:
                parts.append("배치 차이")
        actions.append(
            TrayTargetAction(
                node_id=target.node_id,
                label=" | ".join(parts),
                enabled=target.online,
                selected=target.selected,
            )
        )
    return tuple(actions)


class StatusTray(QObject):
    def __init__(self, controller, *, coord_client=None, window=None, quit_callback=None, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.coord_client = coord_client
        self.window = window
        self.quit_callback = quit_callback
        self._icon = None
        self._menu = None

    def available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def start(self) -> bool:
        if not self.available():
            return False
        self._icon = QSystemTrayIcon(self._build_icon(), self)
        self._menu = QMenu()
        self._icon.setContextMenu(self._menu)
        self._icon.activated.connect(self._on_activated)
        self.controller.summaryChanged.connect(lambda _view: self.refresh())
        self.controller.targetsChanged.connect(lambda _targets: self.refresh())
        self.controller.messageChanged.connect(lambda _message, _tone: self.refresh())
        self.refresh()
        self._icon.show()
        return True

    def stop(self) -> None:
        if self._icon is not None:
            self._icon.hide()
            self._icon.deleteLater()
            self._icon = None

    def refresh(self) -> None:
        if self._icon is None:
            return
        view = self.controller.current_view
        if view is None:
            return
        self._icon.setToolTip(build_tray_title(view))
        self._menu.clear()

        toggle_label = "메인 창 숨기기" if self.window is not None and self.window.isVisible() else "메인 창 열기"
        toggle_action = QAction(toggle_label, self._menu)
        toggle_action.triggered.connect(self.toggle_window)
        self._menu.addAction(toggle_action)
        self._menu.addSeparator()
        quit_action = QAction("종료", self._menu)
        quit_action.triggered.connect(self._quit)
        self._menu.addAction(quit_action)

    def toggle_window(self) -> None:
        if self.window is None:
            return
        if self.window.isVisible():
            self.window.hide()
        else:
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()
        self.refresh()

    def _request_target(self, node_id: str) -> None:
        if self.coord_client is None:
            return
        self.coord_client.request_target(node_id)
        self.controller.set_message(f"{node_id} PC로 전환을 요청했습니다.", "accent")

    def _clear_target(self) -> None:
        if self.coord_client is None:
            return
        self.coord_client.clear_target()
        self.controller.set_message("선택한 대상을 해제했습니다.", "neutral")

    def _quit(self) -> None:
        if callable(self.quit_callback):
            self.quit_callback()

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.toggle_window()

    def _build_icon(self) -> QIcon:
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor("#1f4d3a"))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(6, 6, 52, 52, 10, 10)
        painter.setBrush(QColor("#f6f7fb"))
        painter.drawRect(18, 18, 28, 12)
        painter.setBrush(QColor("#f0b429"))
        painter.drawRect(18, 34, 12, 12)
        painter.setBrush(QColor("#9fd3c7"))
        painter.drawRect(34, 34, 12, 12)
        painter.end()
        return QIcon(pixmap)
