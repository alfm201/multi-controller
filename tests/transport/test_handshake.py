"""Tests for transport/peer/handshake.py."""

from __future__ import annotations

import json

from transport.peer.handshake import recv_hello, send_hello


class FakeSocket:
    def __init__(self, *chunks: bytes):
        self._chunks = list(chunks)
        self.sent = b""

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def recv(self, _size: int) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


def test_send_hello_includes_version_metadata():
    sock = FakeSocket()

    send_hello(
        sock,
        "A",
        app_version="0.3.18",
        compatibility_version="0.3.18",
    )

    payload = json.loads(sock.sent.decode("utf-8").strip())
    assert payload["node_id"] == "A"
    assert payload["app_version"] == "0.3.18"
    assert payload["compatibility_version"] == "0.3.18"


def test_recv_hello_accepts_legacy_payload_without_version():
    sock = FakeSocket(b'{"kind":"hello","node_id":"B"}\n')

    hello = recv_hello(sock)

    assert hello.node_id == "B"
    assert hello.app_version is None
    assert hello.compatibility_version is None


def test_recv_hello_reads_version_metadata():
    sock = FakeSocket(
        b'{"kind":"hello","node_id":"B","app_version":"0.3.17","compatibility_version":"0.3.17"}\n'
    )

    hello = recv_hello(sock)

    assert hello.node_id == "B"
    assert hello.app_version == "0.3.17"
    assert hello.compatibility_version == "0.3.17"
