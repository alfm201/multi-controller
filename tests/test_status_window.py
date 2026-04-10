"""Tests for runtime/status_window.py."""

from runtime.context import build_runtime_context
from runtime.status_window import StatusWindow


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

    def get_selected_target(self):
        return self._target


class FakeSink:
    def __init__(self, controller_id):
        self._controller_id = controller_id

    def get_authorized_controller(self):
        return self._controller_id


class FakeVar:
    def __init__(self, value=None):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class FakeRoot:
    def __init__(self):
        self.after_calls = []
        self.destroyed = False

    def after(self, delay, callback):
        self.after_calls.append((delay, callback))

    def destroy(self):
        self.destroyed = True


class FakeLayoutEditor:
    def __init__(self):
        self.refresh_calls = []
        self.closed = 0

    def refresh(self, view):
        self.refresh_calls.append(view)

    def close(self):
        self.closed += 1


class FakeCoordClient:
    def __init__(self):
        self.requested = []
        self.cleared = 0
        self.ended = 0

    def request_target(self, node_id):
        self.requested.append(node_id)

    def clear_target(self):
        self.cleared += 1

    def is_layout_editor(self):
        return True

    def end_layout_edit(self):
        self.ended += 1


class FakeConfigReloader:
    def __init__(self):
        self.calls = 0

    def reload(self):
        self.calls += 1


class FakeInventoryManager:
    def __init__(self):
        self.calls = 0

    def refresh(self):
        self.calls += 1
        return type("Snapshot", (), {"monitors": (1, 2)})()


def _layout_ctx():
    config = {
        "nodes": [
            {"name": "A", "ip": "127.0.0.1", "port": 5000},
            {"name": "B", "ip": "127.0.0.1", "port": 5001},
        ],
    }
    return build_runtime_context(config, override_name="A", config_path="config.json")


def test_refresh_updates_summary_and_delegates_to_layout_editor():
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([("B", FakeConn())]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        router=FakeRouter("active", "B"),
        sink=FakeSink("B"),
    )
    window._root = FakeRoot()
    window._vars = {
        "headline": FakeVar(),
        "summary": FakeVar(),
        "hint": FakeVar(),
        "self_id": FakeVar(),
        "coordinator": FakeVar(),
        "router": FakeVar(),
        "lease": FakeVar(),
        "config_path": FakeVar(),
        "message": FakeVar(),
    }
    window._render_summary_cards = lambda cards: None
    window._render_targets = lambda targets: None
    window._render_peers = lambda peers: None
    window._render_selected_detail = lambda view: None
    window._render_advanced_runtime = lambda: None
    window._render_advanced_peers = lambda peers: None
    window._layout_editor = FakeLayoutEditor()

    window._refresh()

    assert window._vars["headline"].get() == "B PC가 현재 제어 대상입니다."
    assert window._vars["summary"].get() == "연결된 PC 1 / 1"
    assert window._vars["hint"].get() == "입력이 선택된 PC로 전달되고 있습니다."
    assert window._layout_editor.refresh_calls[0].self_id == "A"
    assert window._root.after_calls[0][0] == window.refresh_ms


def test_reload_clear_target_and_detection_update_runtime_message():
    ctx = _layout_ctx()
    coord_client = FakeCoordClient()
    reloader = FakeConfigReloader()
    inventory_manager = FakeInventoryManager()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: None,
        coord_client=coord_client,
        config_reloader=reloader,
        monitor_inventory_manager=inventory_manager,
    )
    window._vars = {"message": FakeVar()}

    window._reload_config()
    window._clear_target()
    window._request_target("B")
    window._refresh_local_monitor_inventory()

    assert reloader.calls == 1
    assert coord_client.cleared == 1
    assert coord_client.requested == ["B"]
    assert inventory_manager.calls == 1
    assert window._vars["message"].get() == "로컬 모니터를 다시 감지했습니다. 모니터 2개"


def test_handle_close_closes_editor_and_ends_layout_edit():
    ctx = _layout_ctx()
    coord_client = FakeCoordClient()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: None,
        coord_client=coord_client,
    )
    window._root = FakeRoot()
    window._layout_editor = FakeLayoutEditor()
    closed = []
    window._on_close = lambda: closed.append(True)

    window._handle_close()

    assert window._layout_editor.closed == 1
    assert coord_client.ended == 1
    assert closed == [True]
    assert window._root is None
