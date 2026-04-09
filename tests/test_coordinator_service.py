"""Tests for coordinator/service.py lease behavior."""

from coordinator.protocol import make_claim, make_heartbeat, make_release
from coordinator.service import CoordinatorService
from network.dispatcher import FrameDispatcher
from runtime.context import NodeInfo, RuntimeContext


class RecordingConn:
    def __init__(self):
        self.frames = []
        self.closed = False

    def send_frame(self, frame):
        self.frames.append(frame)
        return True


class FakeRegistry:
    def __init__(self, conns):
        self._conns = conns
        self._listeners = []

    def add_listener(self, listener):
        self._listeners.append(listener)

    def get(self, node_id):
        return self._conns.get(node_id)

    def emit_bound(self, node_id):
        for listener in self._listeners:
            listener("bound", node_id)


def _ctx():
    nodes = [
        NodeInfo.from_dict({"name": "COORD", "ip": "127.0.0.1", "port": 5000, "roles": ["coordinator"]}),
        NodeInfo.from_dict({"name": "CTRL", "ip": "127.0.0.1", "port": 5001, "roles": ["controller"]}),
        NodeInfo.from_dict({"name": "TGT", "ip": "127.0.0.1", "port": 5002, "roles": ["target"]}),
    ]
    return RuntimeContext(
        self_node=nodes[0],
        nodes=nodes,
        coordinator_candidates=["COORD"],
    )


def test_claim_grants_and_updates_target():
    ctrl_conn = RecordingConn()
    tgt_conn = RecordingConn()
    registry = FakeRegistry({"CTRL": ctrl_conn, "TGT": tgt_conn})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_claim("CTRL", make_claim("TGT", "CTRL"))

    assert ctrl_conn.frames[-1]["kind"] == "ctrl.grant"
    assert tgt_conn.frames[-1]["kind"] == "ctrl.lease_update"
    assert tgt_conn.frames[-1]["controller_id"] == "CTRL"


def test_release_clears_target_holder():
    ctrl_conn = RecordingConn()
    tgt_conn = RecordingConn()
    registry = FakeRegistry({"CTRL": ctrl_conn, "TGT": tgt_conn})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_claim("CTRL", make_claim("TGT", "CTRL"))
    service._on_release("CTRL", make_release("TGT", "CTRL"))

    assert tgt_conn.frames[-1]["kind"] == "ctrl.lease_update"
    assert tgt_conn.frames[-1]["controller_id"] is None


def test_heartbeat_restores_missing_lease():
    ctrl_conn = RecordingConn()
    tgt_conn = RecordingConn()
    registry = FakeRegistry({"CTRL": ctrl_conn, "TGT": tgt_conn})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_heartbeat("CTRL", make_heartbeat("TGT", "CTRL"))

    assert tgt_conn.frames[-1]["controller_id"] == "CTRL"


def test_expire_once_clears_target_holder():
    ctrl_conn = RecordingConn()
    tgt_conn = RecordingConn()
    registry = FakeRegistry({"CTRL": ctrl_conn, "TGT": tgt_conn})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_claim("CTRL", make_claim("TGT", "CTRL"))
    service._leases["TGT"]["expires_at"] = service._now() - 1

    expired = service._expire_once()

    assert expired == [("TGT", "CTRL")]
    assert tgt_conn.frames[-1]["controller_id"] is None
