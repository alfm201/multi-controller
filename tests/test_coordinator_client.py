"""Tests for coordinator/client.py coordinator failover behavior."""

from coordinator.client import CoordinatorClient
from network.dispatcher import FrameDispatcher
from runtime.context import NodeInfo, RuntimeContext


class FakeConn:
    def __init__(self):
        self.frames = []
        self.closed = False

    def send_frame(self, frame):
        self.frames.append(frame)
        return True


class FakeRegistry:
    def __init__(self, conns):
        self._conns = conns

    def get(self, node_id):
        return self._conns.get(node_id)


class FakeRouter:
    def __init__(self, state="inactive", target_id=None):
        self._state = state
        self._target_id = target_id
        self.clears = []
        self.activations = []

    def get_target_state(self):
        return self._state

    def get_selected_target(self):
        return self._target_id

    def get_active_target(self):
        if self._state == "active":
            return self._target_id
        return None

    def clear_target(self, reason=None):
        self.clears.append(reason)
        self._state = "inactive"
        self._target_id = None

    def activate_target(self, target_id):
        self.activations.append(target_id)
        self._state = "active"
        self._target_id = target_id


class FakeSink:
    def __init__(self):
        self.authorizations = []

    def set_authorized_controller(self, controller_id):
        self.authorizations.append(controller_id)


def _ctx():
    nodes = [
        NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000}),
        NodeInfo.from_dict({"name": "B", "ip": "127.0.0.1", "port": 5001}),
        NodeInfo.from_dict({"name": "C", "ip": "127.0.0.1", "port": 5002}),
    ]
    return RuntimeContext(self_node=nodes[0], nodes=nodes)


def test_coordinator_change_reclaims_pending_target():
    b = FakeConn()
    c = FakeConn()
    registry = FakeRegistry({"B": b, "C": c})
    dispatcher = FrameDispatcher()
    router = FakeRouter(state="pending", target_id="C")
    sink = FakeSink()
    current = {"node": _ctx().get_node("B")}
    client = CoordinatorClient(
        _ctx(),
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
        router=router,
        sink=sink,
    )

    client._on_coordinator_changed("B")
    current["node"] = _ctx().get_node("C")
    client._on_coordinator_changed("C")

    assert b.frames[-1]["kind"] == "ctrl.claim"
    assert c.frames[-1]["kind"] == "ctrl.claim"
    assert sink.authorizations == [None, None]


def test_coordinator_change_reheartbeats_active_target():
    b = FakeConn()
    c = FakeConn()
    registry = FakeRegistry({"B": b, "C": c})
    dispatcher = FrameDispatcher()
    router = FakeRouter(state="active", target_id="C")
    current = {"node": _ctx().get_node("B")}
    client = CoordinatorClient(
        _ctx(),
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
        router=router,
        sink=FakeSink(),
    )

    client._on_coordinator_changed("B")
    current["node"] = _ctx().get_node("C")
    client._on_coordinator_changed("C")

    assert b.frames[-1]["kind"] == "ctrl.heartbeat"
    assert c.frames[-1]["kind"] == "ctrl.heartbeat"


def test_lease_update_only_from_current_coordinator_is_applied():
    registry = FakeRegistry({})
    dispatcher = FrameDispatcher()
    sink = FakeSink()
    current = {"node": _ctx().get_node("B")}
    client = CoordinatorClient(
        _ctx(),
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
        router=None,
        sink=sink,
    )

    client._on_lease_update("C", {"target_id": "A", "controller_id": "B", "coordinator_epoch": "B:1"})
    client._on_lease_update("B", {"target_id": "A", "controller_id": "B", "coordinator_epoch": "B:1"})

    assert sink.authorizations == ["B"]


def test_grant_from_stale_coordinator_is_ignored():
    b = FakeConn()
    registry = FakeRegistry({"B": b})
    dispatcher = FrameDispatcher()
    router = FakeRouter(state="pending", target_id="C")
    current = {"node": _ctx().get_node("B")}
    client = CoordinatorClient(
        _ctx(),
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
        router=router,
        sink=FakeSink(),
    )

    client._on_grant(
        "C",
        {
            "target_id": "C",
            "controller_id": "A",
            "coordinator_epoch": "C:1",
            "lease_ttl_ms": 3000,
        },
    )

    assert router.activations == []


def test_old_epoch_from_same_coordinator_is_ignored_after_new_epoch_seen():
    registry = FakeRegistry({})
    dispatcher = FrameDispatcher()
    sink = FakeSink()
    current = {"node": _ctx().get_node("B")}
    client = CoordinatorClient(
        _ctx(),
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
        router=None,
        sink=sink,
    )

    client._on_lease_update("B", {"target_id": "A", "controller_id": "B", "coordinator_epoch": "B:2"})
    client._on_lease_update("B", {"target_id": "A", "controller_id": "C", "coordinator_epoch": "B:1"})

    assert sink.authorizations == ["B"]


def test_newer_epoch_from_same_coordinator_replaces_old_authorization():
    registry = FakeRegistry({})
    dispatcher = FrameDispatcher()
    sink = FakeSink()
    current = {"node": _ctx().get_node("B")}
    client = CoordinatorClient(
        _ctx(),
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
        router=None,
        sink=sink,
    )

    client._on_lease_update("B", {"target_id": "A", "controller_id": "B", "coordinator_epoch": "B:1"})
    client._on_lease_update("B", {"target_id": "A", "controller_id": "C", "coordinator_epoch": "B:2"})

    assert sink.authorizations == ["B", None, "C"]
