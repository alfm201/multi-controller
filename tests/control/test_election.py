"""Tests for control/coordination/election.py."""

from control.coordination.election import CoordinatorElection, is_self_coordinator, online_node_ids, pick_coordinator
from control.state.context import NodeInfo, RuntimeContext


class FakeConn:
    def __init__(self, closed=False):
        self.closed = closed


class FakeRegistry:
    def __init__(self, pairs):
        self._pairs = pairs
        self._listeners = []

    def all(self):
        return list(self._pairs)

    def add_listener(self, listener):
        self._listeners.append(listener)

    def emit(self, event, node_id):
        for listener in self._listeners:
            listener(event, node_id)


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


def test_pick_coordinator_prefers_lower_priority_before_node_id():
    nodes = [
        NodeInfo.from_dict({"name": "B", "ip": "127.0.0.1", "port": 5001, "priority": 50}),
        NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000, "priority": 100}),
        NodeInfo.from_dict({"name": "C", "ip": "127.0.0.1", "port": 5002, "priority": 100}),
    ]
    ctx = RuntimeContext(self_node=nodes[0], nodes=nodes)
    registry = FakeRegistry([("A", FakeConn()), ("C", FakeConn())])

    assert pick_coordinator(ctx, registry).node_id == "B"


def test_pick_coordinator_treats_zero_or_missing_priority_as_last():
    nodes = [
        NodeInfo.from_dict({"name": "B", "ip": "127.0.0.1", "port": 5001, "priority": 0}),
        NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000}),
        NodeInfo.from_dict({"name": "C", "ip": "127.0.0.1", "port": 5002, "priority": 5}),
    ]
    ctx = RuntimeContext(self_node=nodes[0], nodes=nodes)
    registry = FakeRegistry([("A", FakeConn()), ("C", FakeConn())])

    assert pick_coordinator(ctx, registry).node_id == "C"


def test_self_wins_when_it_is_smallest_online():
    nodes = [
        NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000}),
        NodeInfo.from_dict({"name": "B", "ip": "127.0.0.1", "port": 5001}),
    ]
    ctx = RuntimeContext(self_node=nodes[0], nodes=nodes)
    registry = FakeRegistry([])
    assert is_self_coordinator(ctx, registry) is True


def test_coordinator_election_waits_for_health_and_hold_down(monkeypatch):
    now = {"value": 100.0}
    monkeypatch.setattr("control.coordination.election.time.monotonic", lambda: now["value"])
    nodes = [
        NodeInfo.from_dict({"name": "B", "ip": "127.0.0.1", "port": 5001, "priority": 100}),
        NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000, "priority": 10}),
    ]
    ctx = RuntimeContext(self_node=nodes[0], nodes=nodes)
    registry = FakeRegistry([])
    election = CoordinatorElection(ctx, registry, health_grace_sec=1.0, hold_down_sec=3.0)

    assert election.pick().node_id == "B"

    registry._pairs = [("A", FakeConn())]
    registry.emit("bound", "A")
    assert election.pick().node_id == "B"

    now["value"] += 1.1
    assert election.pick().node_id == "B"

    now["value"] += 2.9
    assert election.pick().node_id == "A"


def test_coordinator_election_fails_over_immediately_when_current_goes_offline(monkeypatch):
    now = {"value": 200.0}
    monkeypatch.setattr("control.coordination.election.time.monotonic", lambda: now["value"])
    nodes = [
        NodeInfo.from_dict({"name": "B", "ip": "127.0.0.1", "port": 5001, "priority": 100}),
        NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000, "priority": 10}),
    ]
    ctx = RuntimeContext(self_node=nodes[0], nodes=nodes)
    registry = FakeRegistry([("A", FakeConn())])
    election = CoordinatorElection(ctx, registry, health_grace_sec=0.0, hold_down_sec=10.0)

    assert election.pick().node_id == "A"

    registry._pairs = []
    registry.emit("unbound", "A")
    assert election.pick().node_id == "B"


def test_coordinator_election_without_current_coordinator_prefers_currently_healthy_candidate(monkeypatch):
    now = {"value": 300.0}
    monkeypatch.setattr("control.coordination.election.time.monotonic", lambda: now["value"])
    nodes = [
        NodeInfo.from_dict({"name": "B", "ip": "127.0.0.1", "port": 5001, "priority": 0}),
        NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000, "priority": 10}),
    ]
    ctx = RuntimeContext(self_node=nodes[0], nodes=nodes)
    registry = FakeRegistry([("A", FakeConn())])
    election = CoordinatorElection(ctx, registry, health_grace_sec=5.0, hold_down_sec=10.0)

    assert election.pick().node_id == "B"
