"""Tests for runtime/status_tray.py."""

from runtime.app_identity import APP_DISPLAY_NAME
from runtime.context import NodeInfo, RuntimeContext
from runtime.status_tray import StatusTray, build_tray_target_actions, build_tray_title
from runtime.status_view import build_status_view


class FakeConn:
    def __init__(self, closed=False):
        self.closed = closed


class FakeRegistry:
    def __init__(self, pairs):
        self._pairs = pairs

    def all(self):
        return list(self._pairs)


class FakeRouter:
    def __init__(self, state, target):
        self._state = state
        self._target = target

    def get_target_state(self):
        return self._state

    def get_requested_target(self):
        return self._target

    def get_active_target(self):
        if self._state == "active":
            return self._target
        return None

    def get_selected_target(self):
        return self._target


class FakeWindow:
    def __init__(self):
        self._visible = True
        self.hidden = 0
        self.shown = 0
        self.raised = 0
        self.activated = 0

    def isVisible(self):
        return self._visible

    def hide(self):
        self._visible = False
        self.hidden += 1

    def show(self):
        self._visible = True
        self.shown += 1

    def raise_(self):
        self.raised += 1

    def activateWindow(self):
        self.activated += 1


def _ctx():
    nodes = [
        NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000}),
        NodeInfo.from_dict({"name": "B", "ip": "127.0.0.1", "port": 5001}),
        NodeInfo.from_dict({"name": "C", "ip": "127.0.0.1", "port": 5002}),
    ]
    return RuntimeContext(self_node=nodes[0], nodes=nodes)


def test_build_tray_title_includes_core_runtime_fields():
    ctx = _ctx()
    view = build_status_view(
        ctx,
        FakeRegistry([("B", FakeConn())]),
        coordinator_resolver=lambda: ctx.get_node("B"),
        router=FakeRouter("active", "C"),
    )

    title = build_tray_title(view)

    assert f"{APP_DISPLAY_NAME} [A]" in title
    assert "B" in title


def test_build_tray_target_actions_reflect_selection_and_online_state():
    ctx = _ctx()
    view = build_status_view(
        ctx,
        FakeRegistry([("B", FakeConn())]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        router=FakeRouter("pending", "C"),
    )

    actions = {action.node_id: action for action in build_tray_target_actions(view)}

    assert actions["B"].enabled is True
    assert actions["B"].selected is False
    assert actions["C"].enabled is False
    assert actions["C"].selected is False


def test_toggle_window_notifies_when_hiding_to_tray(qapp):
    tray = StatusTray(controller=None, window=FakeWindow())
    notifications = []
    refresh_calls = []
    tray.show_notification = notifications.append
    tray.refresh = lambda: refresh_calls.append("refresh")

    tray.toggle_window()

    assert notifications == ["트레이에서 계속 실행 중입니다."]
    assert refresh_calls == ["refresh"]


def test_show_notification_updates_fallback_toast_only(qapp):
    tray = StatusTray(controller=None, window=FakeWindow())

    class FakeIcon:
        def __init__(self):
            self.used = False

    class FakeToast:
        def __init__(self):
            self.messages = []

        def show_message(self, message, *, title, timeout_ms):
            self.messages.append((title, message, timeout_ms))

    tray._icon = FakeIcon()
    tray._toast = FakeToast()

    tray.show_notification("테스트 알림", timeout_ms=1234)

    assert tray._icon.used is False
    assert tray._toast.messages == [(APP_DISPLAY_NAME, "테스트 알림", 1234)]
