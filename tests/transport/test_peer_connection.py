"""Tests for transport/peer/peer_connection.py."""

from transport.peer import peer_connection as peer_connection_module
from transport.peer.peer_connection import PeerConnection


class SlowSendSocket:
    def __init__(self):
        self.shutdown_calls = []
        self.closed = False

    def setsockopt(self, *_args, **_kwargs):
        return None

    def send(self, _payload):
        raise BlockingIOError

    def shutdown(self, how):
        self.shutdown_calls.append(how)

    def close(self):
        self.closed = True


def test_send_frame_times_out_and_closes_connection(monkeypatch):
    clock = {"now": 0.0}

    def fake_monotonic():
        clock["now"] += 0.4
        return clock["now"]

    monkeypatch.setattr(peer_connection_module.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(
        peer_connection_module.select,
        "select",
        lambda _r, w, _e, _timeout: ([], w, []),
    )

    closed = []
    sock = SlowSendSocket()
    conn = PeerConnection(
        sock=sock,
        peer_node_id="B",
        on_frame=lambda *_args: None,
        on_close=lambda node_id, _conn: closed.append(node_id),
    )

    assert conn.send_frame({"kind": "ctrl.test"}) is False
    assert conn.closed is True
    assert sock.closed is True
    assert closed == ["B"]
