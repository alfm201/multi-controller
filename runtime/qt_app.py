"""Qt application runner for the runtime GUI and tray."""

from __future__ import annotations

from PySide6.QtCore import QObject, QMetaObject, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication

from runtime.app_icon import build_app_icon
from runtime.app_identity import APP_DISPLAY_NAME, APP_ID
from runtime.gui_style import apply_gui_theme
from runtime.status_tray import StatusTray
from runtime.status_window import StatusWindow
from runtime.window_chrome import apply_app_user_model_id, apply_window_chrome


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
        self._app = None
        self._window = None
        self._tray = None
        self._quit_bridge = _QuitBridge(self)
        self._notification_bridge = None
        self._status_bridge = None
        self._global_wheel_bridge = None
        self._remote_update_bridge = None

    def _ensure_bridges(self) -> None:
        if self._notification_bridge is None:
            self._notification_bridge = _NotificationBridge(self)
        if self._status_bridge is None:
            self._status_bridge = _StatusBridge(self)
        if self._global_wheel_bridge is None:
            self._global_wheel_bridge = _GlobalWheelBridge(self)
        if self._remote_update_bridge is None:
            self._remote_update_bridge = _RemoteUpdateBridge(self)

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
        self._status_bridge.moveToThread(app.thread())
        self._global_wheel_bridge.moveToThread(app.thread())
        self._remote_update_bridge.moveToThread(app.thread())
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
        try:
            return app.exec()
        finally:
            if self._tray is not None:
                self._tray.stop()
            if self._window is not None:
                self._window.force_close()
            on_close()

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

    def request_tray_notification(self, message: str) -> None:
        if not message:
            return
        self._ensure_bridges()
        if self._tray is None and self._app is None and QApplication.instance() is None:
            return
        self._notification_bridge.notificationRequested.emit(message)

    def request_status_message(self, message: str, tone: str = "neutral") -> None:
        if not message:
            return
        self._ensure_bridges()
        if self._window is None and self._app is None and QApplication.instance() is None:
            return
        self._status_bridge.statusRequested.emit(message, tone)

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

    def _deliver_notification(self, message: str) -> None:
        tray = self._tray
        should_show = (
            tray is not None
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
