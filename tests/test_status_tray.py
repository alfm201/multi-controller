"""Tests for runtime/status_tray.py."""

from runtime.context import NodeInfo, RuntimeContext
from runtime.status_view import build_status_view
from runtime.status_tray import build_tray_target_actions, build_tray_title


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

    assert "multi-controller [A]" in title
    assert "coord=B" in title
    assert "target=C" in title
    assert "state=active" in title


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
    assert actions["C"].selected is True
    assert "pending" in actions["C"].label
