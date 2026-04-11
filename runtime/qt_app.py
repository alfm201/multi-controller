"""Qt application runner for the runtime GUI and tray."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QMetaObject, Qt, Slot
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
    def __init__(self, runtime_app):
        super().__init__()
        self._runtime_app = runtime_app

    @Slot()
    def deliver_notifications(self) -> None:
        self._runtime_app._deliver_notifications()


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
        self._notification_bridge = _NotificationBridge(self)
        self._notification_lock = threading.Lock()
        self._pending_notifications: list[str] = []

    def run(self, on_close) -> int:
        apply_app_user_model_id(APP_ID)
        app = QApplication.instance() or QApplication([])
        apply_gui_theme(app)
        app.setApplicationName(APP_DISPLAY_NAME)
        app.setApplicationDisplayName(APP_DISPLAY_NAME)
        app.setWindowIcon(build_app_icon())
        app.setQuitOnLastWindowClosed(False)
        self._app = app
        self._window = StatusWindow(
            self.ctx,
            self.registry,
            self.coordinator_resolver,
            router=self.router,
            sink=self.sink,
            coord_client=self.coord_client,
            config_reloader=self.config_reloader,
            monitor_inventory_manager=self.monitor_inventory_manager,
        )
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
        if self._tray is None and self._app is None and QApplication.instance() is None:
            return
        with self._notification_lock:
            self._pending_notifications.append(message)
        QMetaObject.invokeMethod(
            self._notification_bridge,
            "deliver_notifications",
            Qt.QueuedConnection,
        )

    def _deliver_notifications(self) -> None:
        tray = self._tray
        should_show = (
            tray is not None
            and (self.ui_mode == "tray" or (self._window is not None and not self._window.isVisible()))
        )
        if not should_show:
            with self._notification_lock:
                self._pending_notifications.clear()
            return
        with self._notification_lock:
            notifications = list(self._pending_notifications)
            self._pending_notifications.clear()
        if notifications:
            tray.show_notification(notifications[-1])
