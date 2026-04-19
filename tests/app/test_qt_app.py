"""Tests for app/ui/qt_app.py."""

from types import SimpleNamespace

from PySide6.QtCore import Qt

from app.ui import qt_app as qt_app_module
from app.ui.qt_app import QtRuntimeApp


class DummyTray:
    def __init__(self):
        self.stopped = 0

    def stop(self):
        self.stopped += 1


class DummyWindow:
    def __init__(self):
        self.closed = 0
        self.should_handle = True
        self.remote_updates = []
        self.remote_update_statuses = []

    def force_close(self):
        self.closed += 1

    def should_handle_global_layout_wheel(self, x, y, dx, dy):
        return self.should_handle

    def handle_remote_update_command(self, payload):
        self.remote_updates.append(payload)

    def handle_remote_update_status(self, payload):
        self.remote_update_statuses.append(payload)


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


class DummyNode:
    def __init__(self, name, ip, note=""):
        self.name = name
        self.ip = ip
        self.note = note


class DummyContext:
    def __init__(self):
        self.self_node = type("SelfNode", (), {"node_id": "A"})()

    def get_node(self, node_id):
        if node_id == "B":
            return DummyNode("B", "127.0.0.1", "회의실")
        if node_id == "A":
            return DummyNode("A", "127.0.0.1", "")
        return None


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


def test_schedule_deferred_startup_runs_callback_only_once(monkeypatch):
    calls = []
    single_shot_calls = []
    runtime_app = QtRuntimeApp(
        ctx=None,
        registry=None,
        coordinator_resolver=lambda: None,
        deferred_startup_callback=lambda: calls.append("started"),
    )
    runtime_app._app = SimpleNamespace()

    def fake_single_shot(delay_ms, callback):
        single_shot_calls.append(delay_ms)
        callback()

    monkeypatch.setattr(qt_app_module.QTimer, "singleShot", fake_single_shot)

    runtime_app._schedule_deferred_startup()
    runtime_app._schedule_deferred_startup()

    assert single_shot_calls == [0]
    assert calls == ["started"]


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


def test_request_notification_routes_tray_without_history_echo(monkeypatch):
    runtime_app = QtRuntimeApp(
        ctx=None,
        registry=None,
        coordinator_resolver=lambda: None,
        ui_mode="gui",
    )
    calls = []

    monkeypatch.setattr(
        runtime_app,
        "request_status_message",
        lambda message, tone="neutral": calls.append(("status", message, tone)),
    )

    def fake_request_tray_notification(message, *, record_history=True):
        calls.append(("tray", message, record_history))

    monkeypatch.setattr(runtime_app, "request_tray_notification", fake_request_tray_notification)

    runtime_app.request_notification("joined", "success")

    assert calls == [
        ("status", "joined", "success"),
        ("tray", "joined", False),
    ]


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


def test_deliver_remote_update_forwards_payload_to_window():
    runtime_app = QtRuntimeApp(
        ctx=None,
        registry=None,
        coordinator_resolver=lambda: None,
        ui_mode="gui",
    )
    runtime_app._window = DummyWindow()

    runtime_app._deliver_remote_update({"target_id": "B"})

    assert runtime_app._window.remote_updates == [{"target_id": "B"}]


def test_deliver_remote_update_status_forwards_payload_to_window():
    runtime_app = QtRuntimeApp(
        ctx=None,
        registry=None,
        coordinator_resolver=lambda: None,
        ui_mode="gui",
    )
    runtime_app._window = DummyWindow()

    runtime_app._deliver_remote_update_status({"target_id": "B", "status": "completed"})

    assert runtime_app._window.remote_update_statuses == [{"target_id": "B", "status": "completed"}]


def test_deliver_pending_remote_update_outcomes_keeps_unsent_outcome(monkeypatch, tmp_path):
    runtime_app = QtRuntimeApp(
        ctx=None,
        registry=None,
        coordinator_resolver=lambda: None,
        ui_mode="gui",
    )

    class CoordClient:
        def __init__(self):
            self.calls = []

        def report_remote_update_status(self, **payload):
            self.calls.append(payload)
            return False

    outcome_path = tmp_path / "remote-update-1.json"
    outcome_path.write_text("{}", encoding="utf-8")
    runtime_app.coord_client = CoordClient()
    monkeypatch.setattr(
        qt_app_module,
        "read_remote_update_outcomes",
        lambda: [
            (
                outcome_path,
                {
                    "requester_id": "A",
                    "target_id": "B",
                    "status": "completed",
                    "detail": "",
                    "event_id": "evt-1",
                    "session_id": "session-1",
                    "current_version": "0.3.17",
                    "latest_version": "0.3.18",
                },
            )
        ],
    )

    runtime_app._deliver_pending_remote_update_outcomes()

    assert runtime_app.coord_client.calls == [
        {
            "target_id": "B",
            "requester_id": "A",
            "status": "completed",
            "reason": "",
            "detail": "",
            "request_id": "",
            "event_id": "evt-1",
            "session_id": "session-1",
            "current_version": "0.3.17",
            "latest_version": "0.3.18",
        }
    ]
    assert outcome_path.exists() is True


def test_deliver_pending_remote_update_outcomes_removes_sent_outcome(monkeypatch, tmp_path):
    runtime_app = QtRuntimeApp(
        ctx=None,
        registry=None,
        coordinator_resolver=lambda: None,
        ui_mode="gui",
    )

    class CoordClient:
        def __init__(self):
            self.calls = []

        def report_remote_update_status(self, **payload):
            self.calls.append(payload)
            return True

    outcome_path = tmp_path / "remote-update-1.json"
    outcome_path.write_text("{}", encoding="utf-8")
    runtime_app.coord_client = CoordClient()
    monkeypatch.setattr(
        qt_app_module,
        "read_remote_update_outcomes",
        lambda: []
        if not outcome_path.exists()
        else [
            (
                outcome_path,
                {
                    "requester_id": "A",
                    "target_id": "B",
                    "status": "starting",
                    "detail": "",
                    "event_id": "evt-2",
                    "session_id": "session-1",
                    "current_version": "0.3.17",
                    "latest_version": "0.3.18",
                },
            )
        ],
    )

    runtime_app._deliver_pending_remote_update_outcomes()

    assert runtime_app.coord_client.calls == [
        {
            "target_id": "B",
            "requester_id": "A",
            "status": "installing",
            "reason": "",
            "detail": "",
            "request_id": "",
            "event_id": "evt-2",
            "session_id": "session-1",
            "current_version": "0.3.17",
            "latest_version": "0.3.18",
        }
    ]
    assert outcome_path.exists() is False


def test_handle_remote_auto_switch_change_requests_banner_and_toast(monkeypatch):
    runtime_app = QtRuntimeApp(
        ctx=DummyContext(),
        registry=None,
        coordinator_resolver=lambda: None,
        ui_mode="gui",
    )
    status_messages = []
    notifications = []
    monkeypatch.setattr(
        runtime_app,
        "request_status_message",
        lambda message, tone="neutral": status_messages.append((message, tone)),
    )
    monkeypatch.setattr(runtime_app, "request_tray_notification", notifications.append)

    runtime_app._handle_remote_auto_switch_change({"requester_id": "B", "enabled": True})

    assert status_messages == [("B(127.0.0.1) 노드가 자동 경계 전환을 켰습니다.", "accent")]
    assert notifications == ["B(127.0.0.1) 노드가 자동 경계 전환을 켰습니다."]
def test_handle_node_list_change_announces_joined_nodes(monkeypatch):
    runtime_app = QtRuntimeApp(
        ctx=DummyContext(),
        registry=None,
        coordinator_resolver=lambda: None,
        ui_mode="gui",
    )
    status_messages = []
    notifications = []
    monkeypatch.setattr(
        runtime_app,
        "request_status_message",
        lambda message, tone="neutral": status_messages.append((message, tone)),
    )
    monkeypatch.setattr(runtime_app, "request_tray_notification", notifications.append)

    runtime_app._handle_node_list_change({"added_node_ids": ("B",)})

    assert status_messages == [("B(127.0.0.1) 노드가 그룹에 참여했습니다.", "success")]
    assert notifications == ["B(127.0.0.1) 노드가 그룹에 참여했습니다."]
