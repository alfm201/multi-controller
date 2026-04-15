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
        self.should_handle = True

    def force_close(self):
        self.closed += 1

    def should_handle_global_layout_wheel(self, x, y, dx, dy):
        return self.should_handle


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


class DummyController:
    def __init__(self):
        self.messages = []

    def set_message(self, message, tone):
        self.messages.append((message, tone))


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


def test_deliver_notifications_shows_message_in_tray_mode():
    runtime_app = QtRuntimeApp(
        ctx=None,
        registry=None,
        coordinator_resolver=lambda: None,
        ui_mode="tray",
    )
    runtime_app._tray = DummyNotificationTray()

    runtime_app._deliver_notification("message")

    assert runtime_app._tray.messages == ["message"]


def test_deliver_notifications_discards_messages_outside_tray_mode():
    runtime_app = QtRuntimeApp(
        ctx=None,
        registry=None,
        coordinator_resolver=lambda: None,
        ui_mode="gui",
    )
    runtime_app._tray = DummyNotificationTray()

    runtime_app._deliver_notification("message")

    assert runtime_app._tray.messages == []


def test_deliver_notifications_flushes_when_window_is_hidden():
    runtime_app = QtRuntimeApp(
        ctx=None,
        registry=None,
        coordinator_resolver=lambda: None,
        ui_mode="gui",
    )
    runtime_app._tray = DummyNotificationTray()

    class HiddenWindow:
        def isVisible(self):
            return False

    runtime_app._window = HiddenWindow()

    runtime_app._deliver_notification("hidden")

    assert runtime_app._tray.messages == ["hidden"]


def test_deliver_status_message_updates_window_controller():
    runtime_app = QtRuntimeApp(
        ctx=None,
        registry=None,
        coordinator_resolver=lambda: None,
        ui_mode="gui",
    )

    class Window:
        def __init__(self):
            self.controller = DummyController()

    runtime_app._window = Window()

    runtime_app._deliver_status_message("status", "accent")

    assert runtime_app._window.controller.messages == [("status", "accent")]


def test_request_global_layout_wheel_returns_false_when_window_does_not_need_it():
    runtime_app = QtRuntimeApp(
        ctx=None,
        registry=None,
        coordinator_resolver=lambda: None,
        ui_mode="gui",
    )
    runtime_app._window = DummyWindow()
    runtime_app._window.should_handle = False

    assert runtime_app.request_global_layout_wheel(10, 20, 0, 1) is False
