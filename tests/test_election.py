"""Tests for coordinator/election.py."""

from coordinator.election import is_self_coordinator, online_node_ids, pick_coordinator
from runtime.context import NodeInfo, RuntimeContext


class FakeConn:
    def __init__(self, closed=False):
        self.closed = closed


class FakeRegistry:
    def __init__(self, pairs):
        self._pairs = pairs

    def all(self):
        return list(self._pairs)


def _ctx():
    nodes = [
        NodeInfo.from_dict({"name": "B", "ip": "127.0.0.1", "port": 5001}),
        NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000}),
        NodeInfo.from_dict({"name": "C", "ip": "127.0.0.1", "port": 5002}),
    ]
    return RuntimeContext(self_node=nodes[0], nodes=nodes)


def test_online_node_ids_include_self_and_live_peers():
    ctx = _ctx()
    registry = FakeRegistry([("A", FakeConn()), ("C", FakeConn(closed=True))])
    assert online_node_ids(ctx, registry) == ["A", "B"]


def test_pick_coordinator_selects_smallest_online_node_id():
    ctx = _ctx()
    registry = FakeRegistry([("A", FakeConn()), ("C", FakeConn())])
    assert pick_coordinator(ctx, registry).node_id == "A"


def test_self_wins_when_it_is_smallest_online():
    nodes = [
        NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000}),
        NodeInfo.from_dict({"name": "B", "ip": "127.0.0.1", "port": 5001}),
    ]
    ctx = RuntimeContext(self_node=nodes[0], nodes=nodes)
    registry = FakeRegistry([])
    assert is_self_coordinator(ctx, registry) is True
