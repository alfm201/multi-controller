"""Tests for coordinator/protocol.py — control message factories."""

from coordinator.protocol import (
    make_claim,
    make_deny,
    make_grant,
    make_heartbeat,
    make_release,
)


def test_claim_kind():
    assert make_claim("tgt1", "ctrl1")["kind"] == "ctrl.claim"


def test_claim_fields():
    m = make_claim("tgt1", "ctrl1")
    assert m["target_id"] == "tgt1"
    assert m["controller_id"] == "ctrl1"


def test_release_kind():
    assert make_release("tgt1", "ctrl1")["kind"] == "ctrl.release"


def test_release_fields():
    m = make_release("tgt2", "ctrl2")
    assert m["target_id"] == "tgt2"
    assert m["controller_id"] == "ctrl2"


def test_heartbeat_kind():
    assert make_heartbeat("tgt1", "ctrl1")["kind"] == "ctrl.heartbeat"


def test_heartbeat_fields():
    m = make_heartbeat("tgt3", "ctrl3")
    assert m["target_id"] == "tgt3"
    assert m["controller_id"] == "ctrl3"


def test_grant_kind():
    assert make_grant("tgt1", "ctrl1")["kind"] == "ctrl.grant"


def test_grant_fields():
    m = make_grant("t", "c")
    assert m["target_id"] == "t"
    assert m["controller_id"] == "c"


def test_deny_kind():
    assert make_deny("tgt1", "ctrl1", "held")["kind"] == "ctrl.deny"


def test_deny_fields():
    m = make_deny("tgt4", "ctrl4", "held_by_other")
    assert m["target_id"] == "tgt4"
    assert m["controller_id"] == "ctrl4"
    assert m["reason"] == "held_by_other"


def test_ctrl_prefix_all():
    """All control messages must start with 'ctrl.'"""
    msgs = [
        make_claim("t", "c"),
        make_release("t", "c"),
        make_heartbeat("t", "c"),
        make_grant("t", "c"),
        make_deny("t", "c", "r"),
    ]
    for m in msgs:
        assert m["kind"].startswith("ctrl."), m["kind"]
