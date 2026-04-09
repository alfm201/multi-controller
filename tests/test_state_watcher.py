"""Tests for runtime/state_watcher.py."""

from runtime.context import NodeInfo, RuntimeContext
from runtime.state_watcher import (
    RuntimeState,
    collect_runtime_state,
    describe_state_changes,
)


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


def _ctx():
    nodes = [
        NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000}),
        NodeInfo.from_dict({"name": "B", "ip": "127.0.0.1", "port": 5001}),
        NodeInfo.from_dict({"name": "C", "ip": "127.0.0.1", "port": 5002}),
    ]
    return RuntimeContext(self_node=nodes[0], nodes=nodes)


def test_collect_runtime_state_reads_core_values():
    ctx = _ctx()
    registry = FakeRegistry([("B", FakeConn()), ("C", FakeConn(closed=True))])
    router = FakeRouter("pending", "B")
    sink = FakeSink("B")

    state = collect_runtime_state(
        ctx,
        registry,
        coordinator_resolver=lambda: ctx.get_node("A"),
        router=router,
        sink=sink,
    )

    assert state.coordinator_id == "A"
    assert state.online_peers == ("B",)
    assert state.router_state == "pending"
    assert state.selected_target == "B"
    assert state.authorized_controller == "B"


def test_describe_state_changes_reports_all_major_transitions():
    previous = RuntimeState(
        coordinator_id="A",
        online_peers=("B",),
        router_state="pending",
        selected_target="B",
        authorized_controller="B",
    )
    current = RuntimeState(
        coordinator_id="C",
        online_peers=("C",),
        router_state="active",
        selected_target="C",
        authorized_controller="C",
    )

    messages = describe_state_changes(previous, current)

    assert "[EVENT COORDINATOR] A -> C" in messages
    assert "[EVENT ONLINE] joined=['C'] left=['B'] now=['C']" in messages
    assert "[EVENT ROUTER] pending:B -> active:C" in messages
    assert "[EVENT LEASE] B -> C" in messages


def test_describe_state_changes_is_empty_without_previous_state():
    current = RuntimeState(
        coordinator_id="A",
        online_peers=("B",),
        router_state="inactive",
        selected_target=None,
        authorized_controller=None,
    )

    assert describe_state_changes(None, current) == []
