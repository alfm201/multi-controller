"""Tests for runtime/status_window.py."""

from runtime.context import NodeInfo, RuntimeContext
from runtime.status_window import build_status_view


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


def test_build_status_view_includes_runtime_fields():
    ctx = _ctx()
    registry = FakeRegistry([("B", FakeConn()), ("C", FakeConn(closed=True))])
    router = FakeRouter("active", "B")
    sink = FakeSink("B")

    view = build_status_view(
        ctx,
        registry,
        coordinator_resolver=lambda: ctx.get_node("A"),
        router=router,
        sink=sink,
    )

    assert view.self_id == "A"
    assert view.coordinator_id == "A"
    assert view.online_peers == ("B",)
    assert view.connected_peer_count == 1
    assert view.total_peer_count == 2
    assert view.router_state == "active"
    assert view.selected_target == "B"
    assert view.authorized_controller == "B"
    assert view.config_path is None
    assert len(view.peers) == 2
    assert {peer.node_id for peer in view.peers} == {"B", "C"}


def test_build_status_view_marks_target_state_and_online_status():
    ctx = _ctx()
    registry = FakeRegistry([("B", FakeConn()), ("C", FakeConn(closed=True))])
    router = FakeRouter("pending", "C")

    view = build_status_view(
        ctx,
        registry,
        coordinator_resolver=lambda: ctx.get_node("A"),
        router=router,
    )

    targets = {target.node_id: target for target in view.targets}
    assert targets["B"].online is True
    assert targets["B"].selected is False
    assert targets["C"].online is False
    assert targets["C"].selected is True
    assert targets["C"].state == "pending"


def test_build_status_view_marks_peer_connection_roles_and_flags():
    ctx = _ctx()
    registry = FakeRegistry([("B", FakeConn()), ("C", FakeConn(closed=True))])
    sink = FakeSink("B")

    view = build_status_view(
        ctx,
        registry,
        coordinator_resolver=lambda: ctx.get_node("C"),
        sink=sink,
    )

    peers = {peer.node_id: peer for peer in view.peers}
    assert peers["B"].roles == ("controller", "target")
    assert peers["B"].online is True
    assert peers["B"].is_coordinator is False
    assert peers["B"].is_authorized_controller is True
    assert peers["C"].online is False
    assert peers["C"].is_coordinator is True
    assert peers["C"].is_authorized_controller is False
