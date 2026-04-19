"""Tests for control/coordination/protocol.py control message factories."""

from control.coordination.protocol import (
    DEFAULT_LEASE_TTL_MS,
    make_claim,
    make_deny,
    make_grant,
    make_heartbeat,
    make_local_input_override,
    make_monitor_inventory_publish,
    make_monitor_inventory_state,
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


def test_local_input_override_fields():
    frame = make_local_input_override("tgt-local", "ctrl-local")
    assert frame["kind"] == "ctrl.local_input_override"
    assert frame["target_id"] == "tgt-local"
    assert frame["controller_id"] == "ctrl-local"


def test_grant_fields_include_ttl():
    frame = make_grant("t", "c", "epoch-1")
    assert frame["kind"] == "ctrl.grant"
    assert frame["target_id"] == "t"
    assert frame["controller_id"] == "c"
    assert frame["coordinator_epoch"] == "epoch-1"
    assert frame["lease_ttl_ms"] == DEFAULT_LEASE_TTL_MS


def test_deny_fields():
    frame = make_deny("tgt4", "ctrl4", "held_by_other", "epoch-1")
    assert frame["kind"] == "ctrl.deny"
    assert frame["target_id"] == "tgt4"
    assert frame["controller_id"] == "ctrl4"
    assert frame["reason"] == "held_by_other"
    assert frame["coordinator_epoch"] == "epoch-1"


def test_lease_update_fields():
    frame = make_lease_update("tgt5", None, "epoch-1", 1234)
    assert frame["kind"] == "ctrl.lease_update"
    assert frame["target_id"] == "tgt5"
    assert frame["controller_id"] is None
    assert frame["coordinator_epoch"] == "epoch-1"
    assert frame["lease_ttl_ms"] == 1234


def test_ctrl_prefix_all():
    msgs = [
        make_claim("t", "c"),
        make_release("t", "c"),
        make_local_input_override("t", "c"),
        make_heartbeat("t", "c"),
        make_grant("t", "c", "epoch-1"),
        make_deny("t", "c", "r", "epoch-1"),
        make_lease_update("t", None, "epoch-1"),
        make_monitor_inventory_publish({"node_id": "A", "monitors": []}),
        make_monitor_inventory_state({"node_id": "A", "monitors": []}, "epoch-1"),
    ]
    for msg in msgs:
        assert msg["kind"].startswith("ctrl."), msg["kind"]


def test_monitor_inventory_frames_include_snapshot():
    publish = make_monitor_inventory_publish({"node_id": "A", "monitors": []})
    state = make_monitor_inventory_state({"node_id": "A", "monitors": []}, "epoch-1")

    assert publish["kind"] == "ctrl.monitor_inventory_publish"
    assert publish["snapshot"]["node_id"] == "A"
    assert state["kind"] == "ctrl.monitor_inventory_state"
    assert state["coordinator_epoch"] == "epoch-1"
