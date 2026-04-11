"""Tests for runtime/qt_app.py."""

from PySide6.QtCore import Qt

from runtime import qt_app as qt_app_module
from runtime.qt_app import QtRuntimeApp


class DummyTray:
    def __init__(self):
        self.stopped = 0

    def stop(self):
        self.stopped += 1


class DummyWindow:
    def __init__(self):
        self.closed = 0

    def force_close(self):
        self.closed += 1


class DummyApp:
    def __init__(self):
        self.quit_called = 0

    def quit(self):
        self.quit_called += 1


class DummyNotificationTray:
    def __init__(self):
        self.messages = []

    def show_notification(self, message):
        self.messages.append(message)


def test_perform_quit_stops_tray_and_closes_window():
    runtime_app = QtRuntimeApp(ctx=None, registry=None, coordinator_resolver=lambda: None)
    runtime_app._tray = DummyTray()
    runtime_app._window = DummyWindow()
    runtime_app._app = DummyApp()

    runtime_app._perform_quit()

    assert runtime_app._tray.stopped == 1
    assert runtime_app._window.closed == 1
    assert runtime_app._app.quit_called == 1


def test_request_quit_queues_bridge_on_qt_thread(monkeypatch):
    runtime_app = QtRuntimeApp(ctx=None, registry=None, coordinator_resolver=lambda: None)
    invoked = {}

    def fake_invoke(target, method_name, connection_type):
        invoked["target"] = target
        invoked["method_name"] = method_name
        invoked["connection_type"] = connection_type
        return True

    monkeypatch.setattr(qt_app_module.QMetaObject, "invokeMethod", fake_invoke)
    runtime_app._app = DummyApp()

    runtime_app.request_quit()

    assert invoked["target"] is runtime_app._quit_bridge
    assert invoked["method_name"] == "perform_quit"
    assert invoked["connection_type"] == Qt.QueuedConnection


def test_request_tray_notification_queues_bridge_on_qt_thread(monkeypatch):
    runtime_app = QtRuntimeApp(
        ctx=None,
        registry=None,
        coordinator_resolver=lambda: None,
        ui_mode="tray",
    )
    invoked = {}

    def fake_invoke(target, method_name, connection_type):
        invoked["target"] = target
        invoked["method_name"] = method_name
        invoked["connection_type"] = connection_type
        return True

    monkeypatch.setattr(qt_app_module.QMetaObject, "invokeMethod", fake_invoke)
    runtime_app._app = DummyApp()

    runtime_app.request_tray_notification("테스트 알림")

    assert invoked["target"] is runtime_app._notification_bridge
    assert invoked["method_name"] == "deliver_notifications"
    assert invoked["connection_type"] == Qt.QueuedConnection


def test_deliver_notifications_flushes_pending_messages_in_tray_mode():
    runtime_app = QtRuntimeApp(
        ctx=None,
        registry=None,
        coordinator_resolver=lambda: None,
        ui_mode="tray",
    )
    runtime_app._tray = DummyNotificationTray()
    runtime_app._pending_notifications = ["첫 번째", "두 번째"]

    runtime_app._deliver_notifications()

    assert runtime_app._tray.messages == ["두 번째"]
    assert runtime_app._pending_notifications == []


def test_deliver_notifications_discards_messages_outside_tray_mode():
    runtime_app = QtRuntimeApp(
        ctx=None,
        registry=None,
        coordinator_resolver=lambda: None,
        ui_mode="gui",
    )
    runtime_app._tray = DummyNotificationTray()
    runtime_app._pending_notifications = ["표시 안 함"]

    runtime_app._deliver_notifications()

    assert runtime_app._tray.messages == []
    assert runtime_app._pending_notifications == []


def test_deliver_notifications_flushes_when_window_is_hidden():
    runtime_app = QtRuntimeApp(
        ctx=None,
        registry=None,
        coordinator_resolver=lambda: None,
        ui_mode="gui",
    )
    runtime_app._tray = DummyNotificationTray()
    runtime_app._pending_notifications = ["숨김 상태 알림"]

    class HiddenWindow:
        def isVisible(self):
            return False

    runtime_app._window = HiddenWindow()

    runtime_app._deliver_notifications()

    assert runtime_app._tray.messages == ["숨김 상태 알림"]
    assert runtime_app._pending_notifications == []
