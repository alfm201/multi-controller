"""Tests for coordinator/protocol.py control message factories."""

from coordinator.protocol import (
    DEFAULT_LEASE_TTL_MS,
    make_claim,
    make_deny,
    make_grant,
    make_heartbeat,
    make_lease_update,
    make_release,
)


def test_claim_fields():
    frame = make_claim("tgt1", "ctrl1")
    assert frame["kind"] == "ctrl.claim"
    assert frame["target_id"] == "tgt1"
    assert frame["controller_id"] == "ctrl1"


def test_release_fields():
    frame = make_release("tgt2", "ctrl2")
    assert frame["kind"] == "ctrl.release"
    assert frame["target_id"] == "tgt2"
    assert frame["controller_id"] == "ctrl2"


def test_heartbeat_fields():
    frame = make_heartbeat("tgt3", "ctrl3")
    assert frame["kind"] == "ctrl.heartbeat"
    assert frame["target_id"] == "tgt3"
    assert frame["controller_id"] == "ctrl3"


def test_grant_fields_include_ttl():
    frame = make_grant("t", "c")
    assert frame["kind"] == "ctrl.grant"
    assert frame["target_id"] == "t"
    assert frame["controller_id"] == "c"
    assert frame["lease_ttl_ms"] == DEFAULT_LEASE_TTL_MS


def test_deny_fields():
    frame = make_deny("tgt4", "ctrl4", "held_by_other")
    assert frame["kind"] == "ctrl.deny"
    assert frame["target_id"] == "tgt4"
    assert frame["controller_id"] == "ctrl4"
    assert frame["reason"] == "held_by_other"


def test_lease_update_fields():
    frame = make_lease_update("tgt5", None, 1234)
    assert frame["kind"] == "ctrl.lease_update"
    assert frame["target_id"] == "tgt5"
    assert frame["controller_id"] is None
    assert frame["lease_ttl_ms"] == 1234


def test_ctrl_prefix_all():
    msgs = [
        make_claim("t", "c"),
        make_release("t", "c"),
        make_heartbeat("t", "c"),
        make_grant("t", "c"),
        make_deny("t", "c", "r"),
        make_lease_update("t", None),
    ]
    for msg in msgs:
        assert msg["kind"].startswith("ctrl."), msg["kind"]
