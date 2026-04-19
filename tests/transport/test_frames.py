"""Tests for transport/peer/frames.py — wire format encoding/decoding."""

import json

from transport.peer.frames import (
    decode_frame,
    encode_frame,
    make_bye,
    make_hello,
    make_ping,
    make_pong,
)


def test_encode_ends_with_newline():
    raw = encode_frame({"kind": "ping"})
    assert raw.endswith(b"\n")


def test_encode_is_valid_json():
    raw = encode_frame({"kind": "ping", "x": 1})
    obj = json.loads(raw.decode("utf-8").strip())
    assert obj == {"kind": "ping", "x": 1}


def test_decode_bytes():
    raw = b'{"kind":"pong"}\n'
    obj = decode_frame(raw)
    assert obj["kind"] == "pong"


def test_decode_str():
    obj = decode_frame('{"kind":"hello","node_id":"A"}')
    assert obj["node_id"] == "A"


def test_round_trip():
    original = {"kind": "key_down", "key": "a", "ts": 1.0}
    assert decode_frame(encode_frame(original)) == original


def test_round_trip_unicode():
    original = {"kind": "system", "message": "안녕"}
    assert decode_frame(encode_frame(original)) == original


def test_make_hello():
    h = make_hello("NodeA")
    assert h["kind"] == "hello"
    assert h["node_id"] == "NodeA"


def test_make_hello_includes_version_metadata():
    h = make_hello(
        "NodeA",
        app_version="0.3.18",
        compatibility_version="0.3.18",
    )

    assert h["app_version"] == "0.3.18"
    assert h["compatibility_version"] == "0.3.18"


def test_make_bye():
    b = make_bye()
    assert b["kind"] == "bye"


def test_make_ping():
    assert make_ping()["kind"] == "ping"


def test_make_pong():
    assert make_pong()["kind"] == "pong"
