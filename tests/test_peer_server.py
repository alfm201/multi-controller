"""Tests for network/peer_server.py."""

import json

from network.peer_server import PeerServer


class FakeSelfNode:
    node_id = "A"
    port = 5000


class FakeContext:
    def __init__(self):
        self.self_node = FakeSelfNode()

    def get_node(self, node_id):
        return FakeSelfNode() if node_id == "A" else None


class FakeRegistry:
    def bind(self, *_args, **_kwargs):
        return True

    def notify_bound_ready(self, *_args, **_kwargs):
        return True

    def unbind(self, *_args, **_kwargs):
        return True


class FakeDispatcher:
    def dispatch(self, *_args, **_kwargs):
        return None


class FakeSocket:
    def __init__(self):
        self.sent = []
        self.closed = False
        self.timeout = None

    def settimeout(self, value):
        self.timeout = value

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True


def test_peer_server_sends_reject_frame_for_unknown_node(monkeypatch):
    server = PeerServer(FakeContext(), FakeRegistry(), FakeDispatcher())
    sock = FakeSocket()

    monkeypatch.setattr(
        "network.peer_server.send_hello",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "network.peer_server.recv_hello",
        lambda _sock: type("Hello", (), {"node_id": "B", "app_version": None, "compatibility_version": None, "bootstrap": False})(),
    )

    server._handshake_inbound(sock, ("192.168.0.2", 12345))

    reject_payload = json.loads(sock.sent[0].decode("utf-8").strip())
    assert reject_payload["kind"] == "ctrl.peer_reject"
    assert reject_payload["reason"] == "unknown_node"
    assert sock.closed is True
