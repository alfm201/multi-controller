"""Qt system tray helpers and wrapper."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from runtime.app_icon import build_app_icon
from runtime.app_identity import APP_DISPLAY_NAME
from runtime.status_controller import normalize_status_message
from runtime.toast_notification import ToastNotification


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
    return f"{APP_DISPLAY_NAME} [{view.self_id}] | 코디네이터 {coordinator} | 대상 {target} | 상태 {state}"


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
        self._toast = ToastNotification()

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

    def show_notification(
        self,
        message: str,
        *,
        title: str = APP_DISPLAY_NAME,
        timeout_ms: int = 2500,
    ) -> None:
        normalized_message = normalize_status_message(message)
        if self._icon is None or not normalized_message:
            return
        self._toast.show_message(normalized_message, title=title, timeout_ms=timeout_ms)

    def notify_message(
        self,
        message: str,
        *,
        tone: str = "neutral",
        title: str = APP_DISPLAY_NAME,
        timeout_ms: int = 2500,
    ) -> None:
        normalized_message = normalize_status_message(message)
        if not normalized_message:
            return
        try:
            self.show_notification(normalized_message, title=title, timeout_ms=timeout_ms)
        except TypeError:
            self.show_notification(normalized_message)
        if self.controller is not None and hasattr(self.controller, "publish_message"):
            self.controller.publish_message(
                normalized_message,
                tone,
                show_banner=False,
                record_history=True,
            )
        elif self.controller is not None and hasattr(self.controller, "record_message"):
            self.controller.record_message(normalized_message, tone)

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
            tray_message = "트레이에서 계속 실행 중입니다."
            self.notify_message(tray_message, tone="neutral")
        else:
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()
        self.refresh()

    def _request_target(self, node_id: str) -> None:
        if self.coord_client is None:
            return
        self.coord_client.request_target(node_id, source="tray")
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
        return build_app_icon(64)
