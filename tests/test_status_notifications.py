from runtime.context import build_runtime_context
from runtime.qt_app import QtRuntimeApp
from runtime.status_window import StatusWindow


class FakeRegistry:
    def __init__(self, pairs=None):
        self._pairs = list(pairs or [])

    def all(self):
        return list(self._pairs)


class FakeCoordClient:
    def is_layout_editor(self):
        return False

    def is_layout_edit_pending(self):
        return False

    def get_layout_editor(self):
        return None

    def get_layout_edit_denial(self):
        return None


def _ctx():
    return build_runtime_context(
        {
            "nodes": [
                {"name": "A", "ip": "127.0.0.1", "port": 5000},
                {"name": "B", "ip": "127.0.0.1", "port": 5001},
            ]
        },
        override_name="A",
        config_path="config/config.json",
    )


def test_status_window_recent_history_preserves_multiline_text(qtbot):
    window = StatusWindow(
        _ctx(),
        FakeRegistry(),
        coordinator_resolver=lambda: None,
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window.show()

    window.controller.record_message("line1\r\nline2", "warning")
    window._set_message_history_expanded(True, animate=False)
    qtbot.waitUntil(lambda: not window._message_history_render_in_progress)

    item = window._message_history_list.item(0)
    label = window._message_history_list.itemWidget(item)

    assert item.text().endswith("line1\nline2")
    assert label is not None
    assert label.text().endswith("line1\nline2")
    assert item.sizeHint().height() > label.fontMetrics().lineSpacing()


def test_monitor_alert_state_is_not_rendered_as_banner_or_history(qtbot):
    window = StatusWindow(
        _ctx(),
        FakeRegistry(),
        coordinator_resolver=lambda: None,
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    initial_count = len(window.controller.message_history)

    window.controller._current_view = type("View", (), {"monitor_alert": "line1\r\nline2", "monitor_alert_tone": "warning"})()
    window._refresh_banner_from_state()
    window.controller._current_view = type("View", (), {"monitor_alert": "line1\nline2", "monitor_alert_tone": "warning"})()
    window._refresh_banner_from_state()

    assert len(window.controller.message_history) == initial_count
    assert window._last_passive_banner_payload is None
    assert window._banner_label.text() == "새로운 알림이 없습니다."


def test_update_banner_tray_notification_is_recorded_in_history(qtbot):
    window = StatusWindow(
        _ctx(),
        FakeRegistry(),
        coordinator_resolver=lambda: None,
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    tray_messages = []

    class Tray:
        def available(self):
            return False

        def show_notification(self, message, timeout_ms=2500):
            tray_messages.append((message, timeout_ms))

    window.attach_tray(Tray())
    initial_count = len(window.controller.message_history)

    window._render_update_banner(
        {
            "visible": True,
            "stage": "update_available",
            "target_kind": "self",
            "title": "새 업데이트 v9.9.9이 준비되었습니다!",
            "detail": "설치 버튼을 눌러 새 버전 준비를 시작할 수 있습니다.",
            "tag_name": "v9.9.9",
        }
    )

    assert tray_messages == [("v9.9.9 업데이트가 준비되었습니다.", 3500)]
    assert len(window.controller.message_history) == initial_count + 1
    assert window.controller.message_history[0]["message"] == "v9.9.9 업데이트가 준비되었습니다."
    assert window.controller.message_history[0]["tone"] == "accent"


def test_qt_app_combined_notification_records_history_once_and_shows_tray():
    runtime_app = QtRuntimeApp(ctx=_ctx(), registry=FakeRegistry(), coordinator_resolver=lambda: None, ui_mode="tray")

    class DummyController:
        def __init__(self):
            self.events = []

        def publish_message(self, message, tone, *, show_banner, record_history):
            self.events.append((message, tone, show_banner, record_history))

    class DummyWindow:
        def __init__(self):
            self.controller = DummyController()

        def isVisible(self):
            return False

    class DummyTray:
        def __init__(self):
            self.messages = []

        def show_notification(self, message):
            self.messages.append(message)

    runtime_app._window = DummyWindow()
    runtime_app._tray = DummyTray()

    runtime_app._deliver_notification_event(
        {
            "message": "same-message",
            "tone": "accent",
            "show_banner": True,
            "record_history": True,
            "show_tray": True,
        }
    )

    assert runtime_app._window.controller.events == [("same-message", "accent", True, True)]
    assert runtime_app._tray.messages == ["same-message"]


def test_qt_app_tray_notification_records_history_without_banner():
    runtime_app = QtRuntimeApp(ctx=_ctx(), registry=FakeRegistry(), coordinator_resolver=lambda: None, ui_mode="tray")

    class DummyController:
        def __init__(self):
            self.events = []

        def publish_message(self, message, tone, *, show_banner, record_history):
            self.events.append((message, tone, show_banner, record_history))

    class DummyWindow:
        def __init__(self):
            self.controller = DummyController()

        def isVisible(self):
            return False

    class DummyTray:
        def __init__(self):
            self.messages = []

        def show_notification(self, message):
            self.messages.append(message)

    runtime_app._window = DummyWindow()
    runtime_app._tray = DummyTray()

    runtime_app._deliver_notification("line1\r\nline2")

    assert runtime_app._window.controller.events == [("line1\r\nline2", "neutral", False, True)]
    assert runtime_app._tray.messages == ["line1\r\nline2"]
