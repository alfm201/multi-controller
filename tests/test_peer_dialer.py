"""Tests for network/peer_dialer.py."""

from network.peer_dialer import PeerDialer
from network.peer_reject import REJECT_REASON_UNKNOWN_NODE, make_peer_reject


class FakeNode:
    def __init__(self, node_id: str):
        self.node_id = node_id
        self.roles = ("controller", "target")


class FakeContext:
    def __init__(self):
        self.self_node = FakeNode("A")
        self.peers = [FakeNode("B")]

    def get_node(self, node_id):
        if node_id == "A":
            return self.self_node
        if node_id == "B":
            return self.peers[0]
        return None


class FakeDispatcher:
    def __init__(self):
        self.handlers = {}

    def register_control_handler(self, kind, handler):
        self.handlers[kind] = handler


class FakeConn:
    def __init__(self):
        self.close_calls = 0
        self.closed = False

    def close(self):
        self.close_calls += 1
        self.closed = True


class FakeRegistry:
    def __init__(self, conn):
        self._conn = conn

    def get(self, node_id):
        if node_id == "B" and not self._conn.closed:
            return self._conn
        return None

    def has(self, _node_id):
        return False


def test_peer_dialer_handles_peer_reject_and_applies_retry_hold():
    now = {"value": 100.0}
    dispatcher = FakeDispatcher()
    conn = FakeConn()
    notices = []
    dialer = PeerDialer(
        FakeContext(),
        FakeRegistry(conn),
        dispatcher,
        reject_callback=lambda peer_id, reject: notices.append((peer_id, reject.reason, reject.detail)),
        now_fn=lambda: now["value"],
    )

    dispatcher.handlers["ctrl.peer_reject"](
        "B",
        make_peer_reject(
            REJECT_REASON_UNKNOWN_NODE,
            detail="상대 노드 목록에 현재 PC 정보가 없습니다.",
            retry_after_sec=60,
        ),
    )

    assert conn.close_calls == 1
    assert notices == [("B", REJECT_REASON_UNKNOWN_NODE, "상대 노드 목록에 현재 PC 정보가 없습니다.")]
    assert dialer._reject_wait_sec("B") == 60.0
