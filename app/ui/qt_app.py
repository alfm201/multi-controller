"""Qt application runner for the runtime GUI and tray."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QMetaObject, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication

from app.logging.app_logging import TAG_STARTUP, tag_message
from app.update.app_update import read_remote_update_outcomes
from app.meta.icon import build_app_icon
from app.meta.identity import APP_DISPLAY_NAME, APP_ID
from app.ui.gui_style import apply_gui_theme
from app.ui.status_tray import StatusTray
from app.ui.status_window import StatusWindow
from app.update.update_domain import (
    UPDATE_ACTION_INSTALL,
    UPDATE_ORIGIN_OUTCOME_REPLAY,
    UPDATE_TARGET_REMOTE_NODE,
    make_remote_update_status_payload,
    normalize_update_event,
)
from app.ui.window_chrome import apply_app_user_model_id, apply_window_chrome


class _QuitBridge(QObject):
    def __init__(self, runtime_app):
        super().__init__()
        self._runtime_app = runtime_app

    @Slot()
    def perform_quit(self) -> None:
        self._runtime_app._perform_quit()


class _NotificationBridge(QObject):
    notificationRequested = Signal(str)

    def __init__(self, runtime_app):
        super().__init__()
        self._runtime_app = runtime_app
        self.notificationRequested.connect(self.deliver_notification, Qt.QueuedConnection)

    @Slot(str)
    def deliver_notification(self, message: str) -> None:
        self._runtime_app._deliver_notification(message)


class _NotificationEventBridge(QObject):
    notificationEventRequested = Signal(object)

    def __init__(self, runtime_app):
        super().__init__()
        self._runtime_app = runtime_app
        self.notificationEventRequested.connect(self.deliver_notification_event, Qt.QueuedConnection)

    @Slot(object)
    def deliver_notification_event(self, payload: object) -> None:
        self._runtime_app._deliver_notification_event(payload)


class _StatusBridge(QObject):
    statusRequested = Signal(str, str)

    def __init__(self, runtime_app):
        super().__init__()
        self._runtime_app = runtime_app
        self.statusRequested.connect(self.deliver_status, Qt.QueuedConnection)

    @Slot(str, str)
    def deliver_status(self, message: str, tone: str) -> None:
        self._runtime_app._deliver_status_message(message, tone)


class _GlobalWheelBridge(QObject):
    wheelRequested = Signal(int, int, int, int)

    def __init__(self, runtime_app):
        super().__init__()
        self._runtime_app = runtime_app
        self.wheelRequested.connect(self.deliver_wheel, Qt.QueuedConnection)

    @Slot(int, int, int, int)
    def deliver_wheel(self, x: int, y: int, dx: int, dy: int) -> None:
        self._runtime_app._deliver_global_layout_wheel(x, y, dx, dy)


class _RemoteUpdateBridge(QObject):
    remoteUpdateRequested = Signal(object)

    def __init__(self, runtime_app):
        super().__init__()
        self._runtime_app = runtime_app
        self.remoteUpdateRequested.connect(self.deliver_remote_update, Qt.QueuedConnection)

    @Slot(object)
    def deliver_remote_update(self, payload: object) -> None:
        self._runtime_app._deliver_remote_update(payload)


class _RemoteUpdateStatusBridge(QObject):
    remoteUpdateStatusRequested = Signal(object)

    def __init__(self, runtime_app):
        super().__init__()
        self._runtime_app = runtime_app
        self.remoteUpdateStatusRequested.connect(self.deliver_remote_update_status, Qt.QueuedConnection)

    @Slot(object)
    def deliver_remote_update_status(self, payload: object) -> None:
        self._runtime_app._deliver_remote_update_status(payload)


class QtRuntimeApp:
    def __init__(
        self,
        *,
        ctx,
        registry,
        coordinator_resolver,
        router=None,
        sink=None,
        coord_client=None,
        config_reloader=None,
        monitor_inventory_manager=None,
        ui_mode: str = "gui",
        deferred_startup_callback=None,
    ):
        self.ctx = ctx
        self.registry = registry
        self.coordinator_resolver = coordinator_resolver
        self.router = router
        self.sink = sink
        self.coord_client = coord_client
        self.config_reloader = config_reloader
        self.monitor_inventory_manager = monitor_inventory_manager
        self.ui_mode = ui_mode
        self.deferred_startup_callback = deferred_startup_callback
        self._app = None
        self._window = None
        self._tray = None
        self._quit_bridge = _QuitBridge(self)
        self._notification_bridge = None
        self._notification_event_bridge = None
        self._status_bridge = None
        self._global_wheel_bridge = None
        self._remote_update_bridge = None
        self._remote_update_status_bridge = None
        self._pending_remote_update_retry_timer = None
        self._deferred_startup_scheduled = False

    def _ensure_bridges(self) -> None:
        if self._notification_bridge is None:
            self._notification_bridge = _NotificationBridge(self)
        if self._notification_event_bridge is None:
            self._notification_event_bridge = _NotificationEventBridge(self)
        if self._status_bridge is None:
            self._status_bridge = _StatusBridge(self)
        if self._global_wheel_bridge is None:
            self._global_wheel_bridge = _GlobalWheelBridge(self)
        if self._remote_update_bridge is None:
            self._remote_update_bridge = _RemoteUpdateBridge(self)
        if self._remote_update_status_bridge is None:
            self._remote_update_status_bridge = _RemoteUpdateStatusBridge(self)

    def run(self, on_close) -> int:
        apply_app_user_model_id(APP_ID)
        app = QApplication.instance() or QApplication([])
        apply_gui_theme(app)
        app.setApplicationName(APP_DISPLAY_NAME)
        app.setApplicationDisplayName(APP_DISPLAY_NAME)
        app.setWindowIcon(build_app_icon())
        app.setQuitOnLastWindowClosed(False)
        self._app = app
        self._ensure_bridges()
        self._quit_bridge.moveToThread(app.thread())
        self._notification_bridge.moveToThread(app.thread())
        self._notification_event_bridge.moveToThread(app.thread())
        self._status_bridge.moveToThread(app.thread())
        self._global_wheel_bridge.moveToThread(app.thread())
        self._remote_update_bridge.moveToThread(app.thread())
        self._remote_update_status_bridge.moveToThread(app.thread())
        self._window = StatusWindow(
            self.ctx,
            self.registry,
            self.coordinator_resolver,
            router=self.router,
            sink=self.sink,
            coord_client=self.coord_client,
            config_reloader=self.config_reloader,
            monitor_inventory_manager=self.monitor_inventory_manager,
            request_quit=self.request_quit,
            ui_mode=self.ui_mode,
        )
        if self.coord_client is not None and hasattr(self.coord_client, "set_remote_update_handler"):
            self.coord_client.set_remote_update_handler(self.request_remote_update)
        if self.coord_client is not None and hasattr(self.coord_client, "set_remote_update_status_handler"):
            self.coord_client.set_remote_update_status_handler(self.request_remote_update_status)
        if self.coord_client is not None and hasattr(self.coord_client, "set_auto_switch_change_handler"):
            self.coord_client.set_auto_switch_change_handler(self._handle_remote_auto_switch_change)
        if self.coord_client is not None and hasattr(self.coord_client, "set_one_shot_timeout_handler"):
            self.coord_client.set_one_shot_timeout_handler(self.request_notification)
        if self.coord_client is not None and hasattr(self.coord_client, "add_node_list_change_listener"):
            self.coord_client.add_node_list_change_listener(self._handle_node_list_change)
        self._window.setWindowIcon(build_app_icon())
        apply_window_chrome(self._window)
        self._tray = StatusTray(
            self._window.controller,
            coord_client=self.coord_client,
            window=self._window,
            quit_callback=self.request_quit,
        )
        self._window.attach_tray(self._tray)
        tray_started = self._tray.start()
        if self.ui_mode == "tray" and tray_started:
            self._window.hide()
        else:
            self._window.show()
            apply_window_chrome(self._window)
        self._schedule_deferred_startup()
        if self._pending_remote_update_retry_timer is None:
            self._pending_remote_update_retry_timer = QTimer(app)
            self._pending_remote_update_retry_timer.setInterval(1000)
            self._pending_remote_update_retry_timer.timeout.connect(self._deliver_pending_remote_update_outcomes)
        self._pending_remote_update_retry_timer.start()
        self._deliver_pending_remote_update_outcomes()
        try:
            return app.exec()
        finally:
            if self._pending_remote_update_retry_timer is not None:
                self._pending_remote_update_retry_timer.stop()
            if self._tray is not None:
                self._tray.stop()
            if self._window is not None:
                self._window.force_close()
            on_close()

    def _schedule_deferred_startup(self) -> None:
        if self._deferred_startup_scheduled or not callable(self.deferred_startup_callback):
            return
        self._deferred_startup_scheduled = True
        app = self._app or QApplication.instance()
        if app is None:
            return
        QTimer.singleShot(0, self._run_deferred_startup)

    def _run_deferred_startup(self) -> None:
        if not callable(self.deferred_startup_callback):
            return
        try:
            self.deferred_startup_callback()
        except Exception as exc:
            logging.warning(tag_message(TAG_STARTUP, "deferred startup callback failed: %s"), exc)

    def _perform_quit(self) -> None:
        if self._tray is not None:
            self._tray.stop()
        if self._window is not None:
            self._window.force_close()
        app = self._app or QApplication.instance()
        if app is not None:
            app.quit()

    def request_quit(self) -> None:
        if self._app is None and QApplication.instance() is None:
            return
        QMetaObject.invokeMethod(
            self._quit_bridge,
            "perform_quit",
            Qt.QueuedConnection,
        )

    def request_tray_notification(self, message: str, *, record_history: bool = True) -> None:
        if not message:
            return
        self._ensure_bridges()
        if self._tray is None and self._app is None and QApplication.instance() is None:
            return
        if not record_history:
            self._notification_event_bridge.notificationEventRequested.emit(
                {
                    "message": message,
                    "tone": "neutral",
                    "show_banner": False,
                    "record_history": False,
                    "show_tray": True,
                }
            )
            return
        self._notification_bridge.notificationRequested.emit(message)

    def request_status_message(self, message: str, tone: str = "neutral") -> None:
        if not message:
            return
        self._ensure_bridges()
        if self._window is None and self._app is None and QApplication.instance() is None:
            return
        self._status_bridge.statusRequested.emit(message, tone)

    def request_notification(self, message: str, tone: str = "neutral") -> None:
        if not message:
            return
        self.request_status_message(message, tone)
        try:
            self.request_tray_notification(message, record_history=False)
        except TypeError:
            self.request_tray_notification(message)

    def request_global_layout_wheel(self, x: int, y: int, dx: int, dy: int) -> bool:
        self._ensure_bridges()
        if self._window is None and self._app is None and QApplication.instance() is None:
            return False
        if self._window is None or not self._window.should_handle_global_layout_wheel(x, y, dx, dy):
            return False
        self._global_wheel_bridge.wheelRequested.emit(int(x), int(y), int(dx), int(dy))
        return True

    def request_remote_update(self, payload: object | None = None) -> None:
        self._ensure_bridges()
        if self._window is None and self._app is None and QApplication.instance() is None:
            return
        self._remote_update_bridge.remoteUpdateRequested.emit(payload or {})

    def request_remote_update_status(self, payload: object | None = None) -> None:
        self._ensure_bridges()
        if self._window is None and self._app is None and QApplication.instance() is None:
            return
        self._remote_update_status_bridge.remoteUpdateStatusRequested.emit(payload or {})

    def _deliver_notification(self, message: str) -> None:
        tray = self._tray
        should_show = (
            tray is not None
            and (self.ui_mode == "tray" or (self._window is not None and not self._window.isVisible()))
        )
        controller = None if self._window is None else getattr(self._window, "controller", None)
        if controller is not None and hasattr(controller, "publish_message"):
            controller.publish_message(
                message,
                "neutral",
                show_banner=False,
                record_history=True,
            )
        if not should_show:
            return
        tray.show_notification(message)

    def _deliver_notification_event(self, payload: object) -> None:
        payload = {} if not isinstance(payload, dict) else dict(payload)
        message = str(payload.get("message") or "")
        tone = str(payload.get("tone") or "neutral")
        show_banner = bool(payload.get("show_banner", True))
        record_history = bool(payload.get("record_history", True))
        show_tray = bool(payload.get("show_tray", True))
        controller = None if self._window is None else getattr(self._window, "controller", None)
        if controller is not None and hasattr(controller, "publish_message"):
            controller.publish_message(
                message,
                tone,
                show_banner=show_banner,
                record_history=record_history,
            )
        tray = self._tray
        should_show = (
            show_tray
            and tray is not None
            and (self.ui_mode == "tray" or (self._window is not None and not self._window.isVisible()))
        )
        if not should_show:
            return
        tray.show_notification(message)

    def _deliver_status_message(self, message: str, tone: str) -> None:
        if self._window is None:
            return
        self._window.controller.set_message(message, tone)

    def _deliver_global_layout_wheel(self, x: int, y: int, dx: int, dy: int) -> None:
        if self._window is None:
            return
        self._window.handle_global_layout_wheel(x, y, dx, dy)

    def _deliver_remote_update(self, payload: object) -> None:
        if self._window is None:
            return
        self._window.handle_remote_update_command(payload if isinstance(payload, dict) else {})

    def _deliver_remote_update_status(self, payload: object) -> None:
        if self._window is None:
            return
        self._window.handle_remote_update_status(payload if isinstance(payload, dict) else {})

    def _deliver_pending_remote_update_outcomes(self) -> None:
        if self.coord_client is None or not hasattr(self.coord_client, "report_remote_update_status"):
            return
        for outcome_path, payload in read_remote_update_outcomes():
            event = normalize_update_event(
                payload,
                default_target_kind=UPDATE_TARGET_REMOTE_NODE,
                default_action=UPDATE_ACTION_INSTALL,
                default_origin=UPDATE_ORIGIN_OUTCOME_REPLAY,
            )
            requester_id = event["requester_id"]
            target_id = event["target_id"]
            status = event["status"]
            if not requester_id or not target_id or not status:
                try:
                    outcome_path.unlink()
                except OSError:
                    pass
                continue
            report_payload = make_remote_update_status_payload(
                target_id=target_id,
                requester_id=requester_id,
                status=event["stage"],
                reason=event["reason"],
                detail=event["detail"],
                request_id=event["request_id"],
                event_id=event["event_id"] or None,
                session_id=event["session_id"] or None,
                current_version=event["current_version"],
                latest_version=event["target_version"],
                action=event["action"] or UPDATE_ACTION_INSTALL,
                origin=event["origin"] or UPDATE_ORIGIN_OUTCOME_REPLAY,
            )
            reported = self.coord_client.report_remote_update_status(
                target_id=report_payload["target_id"],
                requester_id=report_payload["requester_id"],
                status=report_payload["status"],
                reason=report_payload["reason"],
                detail=report_payload["detail"],
                request_id=report_payload["request_id"],
                event_id=report_payload["event_id"],
                session_id=report_payload["session_id"],
                current_version=report_payload["current_version"],
                latest_version=report_payload["latest_version"],
            )
            if reported:
                try:
                    outcome_path.unlink()
                except OSError:
                    pass

    def _handle_remote_auto_switch_change(self, payload: dict | None = None) -> None:
        payload = {} if payload is None else dict(payload)
        requester_id = str(payload.get("requester_id") or "").strip()
        self_node = None if self.ctx is None else getattr(self.ctx, "self_node", None)
        self_node_id = None if self_node is None else getattr(self_node, "node_id", None)
        if not requester_id or requester_id == self_node_id:
            return
        enabled = bool(payload.get("enabled"))
        label = self._node_display_label(requester_id)
        message = (
            f"{label} 노드가 자동 경계 전환을 켰습니다."
            if enabled
            else f"{label} 노드가 자동 경계 전환을 껐습니다."
        )
        self.request_notification(message, "accent" if enabled else "neutral")

    def _node_display_label(self, node_id: str) -> str:
        if self.ctx is None or not hasattr(self.ctx, "get_node"):
            return "알 수 없는 노드"
        node = self.ctx.get_node(node_id)
        if node is None:
            return "알 수 없는 노드"
        if hasattr(node, "display_label") and callable(node.display_label):
            return node.display_label()
        name = str(getattr(node, "name", "") or node_id).strip()
        ip = str(getattr(node, "ip", "") or "").strip()
        return f"{name}({ip})" if ip else name

    def _handle_node_list_change(self, payload: dict | None = None) -> None:
        payload = {} if payload is None else dict(payload)
        reject_reason = str(payload.get("reject_reason") or "").strip()
        if reject_reason == "timeout":
            if not self._has_online_peer():
                return
            self.request_notification(
                "노드 목록 변경 요청이 시간 안에 확인되지 않았습니다. 다시 시도해 주세요.",
                "warning",
            )
            return
        added_node_ids = payload.get("added_node_ids") or ()
        if not isinstance(added_node_ids, (list, tuple)):
            return
        for node_id in added_node_ids:
            label = self._node_display_label(str(node_id))
            message = f"{label} 노드가 그룹에 참여했습니다."
            self.request_notification(message, "success")

    def _has_online_peer(self) -> bool:
        if self.registry is None or not hasattr(self.registry, "all"):
            return False
        for peer_id, conn in self.registry.all():
            if peer_id == getattr(getattr(self.ctx, "self_node", None), "node_id", None):
                continue
            if conn is not None and not getattr(conn, "closed", False):
                return True
        return False
