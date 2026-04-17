"""Tests for coordinator/service.py lease and layout behavior."""

from coordinator.protocol import (
    make_auto_switch_update_request,
    make_claim,
    make_heartbeat,
    make_layout_edit_begin,
    make_layout_edit_end,
    make_local_input_override,
    make_layout_update_request,
    make_monitor_inventory_refresh_request,
    make_monitor_inventory_publish,
    make_node_list_update_request,
    make_node_note_update_request,
    make_remote_update_status,
    make_release,
)
from coordinator.service import CoordinatorService
from network.dispatcher import FrameDispatcher
from runtime.context import NodeInfo, RuntimeContext
from runtime.layouts import build_layout_config


class RecordingConn:
    def __init__(self):
        self.frames = []
        self.closed = False

    def send_frame(self, frame):
        self.frames.append(frame)
        return True


class LockCheckingConn(RecordingConn):
    def __init__(self, service_getter):
        super().__init__()
        self._service_getter = service_getter

    def send_frame(self, frame):
        service = self._service_getter()
        assert service is not None
        assert service._lock.locked() is False
        return super().send_frame(frame)


class FakeRegistry:
    def __init__(self, conns):
        self._conns = conns
        self._listeners = []

    def add_listener(self, listener):
        self._listeners.append(listener)

    def get(self, node_id):
        return self._conns.get(node_id)

    def all(self):
        return list(self._conns.items())

    def emit_bound(self, node_id):
        for listener in self._listeners:
            listener("bound", node_id)

    def emit_unbound(self, node_id):
        for listener in self._listeners:
            listener("unbound", node_id)


class FakeConfigReloader:
    def __init__(self, ctx):
        self.ctx = ctx
        self.calls = []

    def apply_nodes_state(self, nodes, *, rename_map=None, persist=True, apply_runtime=True):
        self.calls.append((nodes, rename_map, persist, apply_runtime))
        self.ctx.replace_nodes([NodeInfo.from_dict(node) for node in nodes])


def _ctx():
    nodes = [
        NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000}),
        NodeInfo.from_dict({"name": "B", "ip": "127.0.0.1", "port": 5001}),
        NodeInfo.from_dict({"name": "C", "ip": "127.0.0.1", "port": 5002}),
    ]
    ctx = RuntimeContext(self_node=nodes[0], nodes=nodes)
    ctx.replace_layout(build_layout_config({}, nodes))
    return ctx


def _ctx_with_self(self_id):
    ctx = _ctx()
    return RuntimeContext(self_node=ctx.get_node(self_id), nodes=ctx.nodes, layout=ctx.layout)


def test_claim_grants_and_updates_target():
    ctrl_conn = RecordingConn()
    tgt_conn = RecordingConn()
    registry = FakeRegistry({"B": ctrl_conn, "C": tgt_conn})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_claim("B", make_claim("C", "B"))

    assert ctrl_conn.frames[-1]["kind"] == "ctrl.grant"
    assert tgt_conn.frames[-1]["kind"] == "ctrl.lease_update"
    assert tgt_conn.frames[-1]["controller_id"] == "B"


def test_claim_denies_offline_target():
    ctrl_conn = RecordingConn()
    registry = FakeRegistry({"B": ctrl_conn})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_claim("B", make_claim("C", "B"))

    assert ctrl_conn.frames[-1]["kind"] == "ctrl.deny"
    assert ctrl_conn.frames[-1]["reason"] == "target_offline"


def test_release_clears_target_holder():
    ctrl_conn = RecordingConn()
    tgt_conn = RecordingConn()
    registry = FakeRegistry({"B": ctrl_conn, "C": tgt_conn})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_claim("B", make_claim("C", "B"))
    service._on_release("B", make_release("C", "B"))

    assert tgt_conn.frames[-1]["controller_id"] is None


def test_local_input_override_revokes_active_controller_and_notifies_both_sides():
    ctrl_conn = RecordingConn()
    tgt_conn = RecordingConn()
    registry = FakeRegistry({"B": ctrl_conn, "C": tgt_conn})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_claim("B", make_claim("C", "B"))
    service._on_local_input_override("C", make_local_input_override("C", "B"))

    assert tgt_conn.frames[-1]["kind"] == "ctrl.lease_update"
    assert tgt_conn.frames[-1]["controller_id"] is None
    assert ctrl_conn.frames[-1]["kind"] == "ctrl.deny"
    assert ctrl_conn.frames[-1]["reason"] == "local_activity"


def test_local_input_override_ignores_non_target_peer_spoof():
    ctrl_conn = RecordingConn()
    tgt_conn = RecordingConn()
    registry = FakeRegistry({"B": ctrl_conn, "C": tgt_conn})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_claim("B", make_claim("C", "B"))
    before_ctrl_frames = len(ctrl_conn.frames)
    before_tgt_frames = len(tgt_conn.frames)
    service._on_local_input_override("B", make_local_input_override("C", "B"))

    assert len(ctrl_conn.frames) == before_ctrl_frames
    assert len(tgt_conn.frames) == before_tgt_frames


def test_heartbeat_restores_missing_lease():
    ctrl_conn = RecordingConn()
    tgt_conn = RecordingConn()
    registry = FakeRegistry({"B": ctrl_conn, "C": tgt_conn})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_heartbeat("B", make_heartbeat("C", "B"))

    assert tgt_conn.frames[-1]["controller_id"] == "B"


def test_expire_once_clears_target_holder():
    ctrl_conn = RecordingConn()
    tgt_conn = RecordingConn()
    registry = FakeRegistry({"B": ctrl_conn, "C": tgt_conn})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_claim("B", make_claim("C", "B"))
    service._leases["C"]["expires_at"] = service._now() - 1

    expired = service._expire_once()

    assert expired == [("C", "B")]
    assert tgt_conn.frames[-1]["controller_id"] is None


def test_layout_edit_lock_grants_then_denies_other_editor():
    editor_b = RecordingConn()
    editor_c = RecordingConn()
    registry = FakeRegistry({"B": editor_b, "C": editor_c})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_layout_edit_begin("B", make_layout_edit_begin("B"))
    service._on_layout_edit_begin("C", make_layout_edit_begin("C"))

    assert any(frame["kind"] == "ctrl.layout_edit_grant" for frame in editor_b.frames)
    deny = next(frame for frame in editor_c.frames if frame["kind"] == "ctrl.layout_edit_deny")
    assert deny["reason"] == "held_by_other"
    assert deny["current_editor_id"] == "B"


def test_layout_update_broadcasts_to_other_nodes_in_real_time():
    editor_b = RecordingConn()
    peer_c = RecordingConn()
    registry = FakeRegistry({"B": editor_b, "C": peer_c})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_layout_edit_begin("B", make_layout_edit_begin("B"))
    service._on_layout_update(
        "B",
        make_layout_update_request(
            {
                "nodes": {
                    "A": {"x": 0, "y": 0, "width": 1, "height": 1},
                    "B": {"x": 2, "y": 0, "width": 1, "height": 1},
                    "C": {"x": 1, "y": 1, "width": 1, "height": 1},
                },
                "auto_switch": {"enabled": True, "edge_threshold": 0.02, "warp_margin": 0.04, "cooldown_ms": 250},
            },
            "B",
        ),
    )

    update = next(frame for frame in peer_c.frames if frame["kind"] == "ctrl.layout_update")
    assert update["layout"]["nodes"]["B"]["x"] == 2
    assert update["layout"]["auto_switch"]["enabled"] is True
    assert update["revision"] == 1
    assert update["persist"] is True


def test_layout_preview_update_broadcasts_without_persist_flag():
    editor_b = RecordingConn()
    peer_c = RecordingConn()
    registry = FakeRegistry({"B": editor_b, "C": peer_c})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_layout_edit_begin("B", make_layout_edit_begin("B"))
    service._on_layout_update(
        "B",
        make_layout_update_request(
            {
                "nodes": {
                    "A": {"x": 0, "y": 0, "width": 1, "height": 1},
                    "B": {"x": 2, "y": 0, "width": 1, "height": 1},
                    "C": {"x": 1, "y": 1, "width": 1, "height": 1},
                },
                "auto_switch": {"enabled": True, "edge_threshold": 0.02, "warp_margin": 0.04, "cooldown_ms": 250},
            },
            "B",
            persist=False,
        ),
    )

    update = next(frame for frame in peer_c.frames if frame["kind"] == "ctrl.layout_update")
    assert update["persist"] is False


def test_node_note_update_is_broadcast_to_all_nodes():
    peer_b = RecordingConn()
    peer_c = RecordingConn()
    registry = FakeRegistry({"B": peer_b, "C": peer_c})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_node_note_update_request("B", make_node_note_update_request("C", "회의실", "B"))

    assert peer_b.frames[-1]["kind"] == "ctrl.node_note_update_state"
    assert peer_b.frames[-1]["node_id"] == "C"
    assert peer_b.frames[-1]["note"] == "회의실"
    assert peer_c.frames[-1]["kind"] == "ctrl.node_note_update_state"
    assert service.ctx.get_node("C").note == "회의실"


def test_remote_update_status_is_forwarded_to_requester():
    peer_b = RecordingConn()
    peer_c = RecordingConn()
    registry = FakeRegistry({"B": peer_b, "C": peer_c})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_remote_update_status(
        "C",
        make_remote_update_status(
            "C",
            "B",
            "starting",
            "downloaded",
            "A:1",
            event_id="evt-1",
            session_id="session-1",
            current_version="0.3.17",
            latest_version="0.3.18",
        ),
    )

    assert peer_b.frames[-1]["kind"] == "ctrl.remote_update_status"
    assert peer_b.frames[-1]["target_id"] == "C"
    assert peer_b.frames[-1]["status"] == "starting"
    assert peer_b.frames[-1]["event_id"] == "evt-1"
    assert peer_b.frames[-1]["session_id"] == "session-1"
    assert peer_b.frames[-1]["current_version"] == "0.3.17"
    assert peer_b.frames[-1]["latest_version"] == "0.3.18"


def test_node_list_update_is_broadcast_to_all_nodes():
    peer_b = RecordingConn()
    peer_c = RecordingConn()
    ctx = _ctx()
    registry = FakeRegistry({"B": peer_b, "C": peer_c})
    dispatcher = FrameDispatcher()
    reloader = FakeConfigReloader(ctx)
    service = CoordinatorService(ctx, registry, dispatcher, config_reloader=reloader)

    service._on_node_list_update_request(
        "B",
        make_node_list_update_request(
            [
                {"name": "A", "ip": "127.0.0.1", "port": 5000},
                {"name": "B", "ip": "127.0.0.1", "port": 5001, "note": "회의실"},
                {"name": "D", "ip": "127.0.0.1", "port": 5003},
            ],
            "B",
        ),
    )

    assert reloader.calls
    assert peer_b.frames[-1]["kind"] == "ctrl.node_list_state"
    assert peer_c.frames[-1]["kind"] == "ctrl.node_list_state"
    assert peer_c.frames[-1]["nodes"][-1]["name"] == "D"


def test_auto_switch_update_request_broadcasts_shared_layout_change():
    peer_b = RecordingConn()
    peer_c = RecordingConn()
    registry = FakeRegistry({"B": peer_b, "C": peer_c})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_auto_switch_update_request(
        "B",
        make_auto_switch_update_request(False, "B"),
    )

    update = next(frame for frame in peer_c.frames if frame["kind"] == "ctrl.layout_update")
    assert update["layout"]["auto_switch"]["enabled"] is False
    assert update["persist"] is True
    assert update["revision"] == 1
    assert update["change_kind"] == "auto_switch_toggle"
    assert update["requester_id"] == "B"
    assert service.ctx.layout.auto_switch.enabled is False


def test_layout_update_rejects_overlapping_nodes():
    editor_b = RecordingConn()
    peer_c = RecordingConn()
    registry = FakeRegistry({"B": editor_b, "C": peer_c})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_layout_edit_begin("B", make_layout_edit_begin("B"))
    before = len(peer_c.frames)
    service._on_layout_update(
        "B",
        make_layout_update_request(
            {
                "nodes": {
                    "A": {"x": 0, "y": 0, "width": 1, "height": 1},
                    "B": {"x": 1, "y": 0, "width": 1, "height": 1},
                    "C": {"x": 1, "y": 0, "width": 1, "height": 1},
                },
                "auto_switch": {"enabled": False, "edge_threshold": 0.02, "warp_margin": 0.04, "cooldown_ms": 250},
            },
            "B",
        ),
    )

    assert len(peer_c.frames) == before


def test_layout_editor_disconnect_releases_lock():
    editor_b = RecordingConn()
    peer_c = RecordingConn()
    registry = FakeRegistry({"B": editor_b, "C": peer_c})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_layout_edit_begin("B", make_layout_edit_begin("B"))
    registry.emit_unbound("B")

    assert service._layout_editor_id is None
    assert peer_c.frames[-1]["kind"] == "ctrl.layout_state"
    assert peer_c.frames[-1]["editor_id"] is None


def test_layout_edit_end_clears_lock():
    editor_b = RecordingConn()
    peer_c = RecordingConn()
    registry = FakeRegistry({"B": editor_b, "C": peer_c})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_layout_edit_begin("B", make_layout_edit_begin("B"))
    service._on_layout_edit_end("B", make_layout_edit_end("B"))

    assert service._layout_editor_id is None
    assert peer_c.frames[-1]["kind"] == "ctrl.layout_state"
    assert peer_c.frames[-1]["editor_id"] is None


def test_monitor_inventory_refresh_request_forwards_to_target_and_acknowledges_requester():
    requester = RecordingConn()
    target = RecordingConn()
    registry = FakeRegistry({"B": requester, "C": target})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_monitor_inventory_refresh_request(
        "B",
        make_monitor_inventory_refresh_request("C", "B"),
    )

    assert target.frames[-1]["kind"] == "ctrl.monitor_inventory_refresh_request"
    assert target.frames[-1]["node_id"] == "C"
    assert requester.frames[-1]["kind"] == "ctrl.monitor_inventory_refresh_status"
    assert requester.frames[-1]["status"] == "requested"


def test_monitor_inventory_refresh_request_reports_offline_target():
    requester = RecordingConn()
    registry = FakeRegistry({"B": requester})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_monitor_inventory_refresh_request(
        "B",
        make_monitor_inventory_refresh_request("C", "B"),
    )

    assert requester.frames[-1]["kind"] == "ctrl.monitor_inventory_refresh_status"
    assert requester.frames[-1]["status"] == "offline"


def test_unbound_target_clears_lease_and_denies_controller():
    ctrl_conn = RecordingConn()
    tgt_conn = RecordingConn()
    registry = FakeRegistry({"B": ctrl_conn, "C": tgt_conn})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_claim("B", make_claim("C", "B"))
    registry.emit_unbound("C")

    assert "C" not in service._leases
    assert ctrl_conn.frames[-1]["kind"] == "ctrl.deny"
    assert ctrl_conn.frames[-1]["reason"] == "target_offline"


def test_unbound_controller_does_not_revoke_active_lease_immediately():
    ctrl_conn = RecordingConn()
    tgt_conn = RecordingConn()
    registry = FakeRegistry({"B": ctrl_conn, "C": tgt_conn})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_claim("B", make_claim("C", "B"))
    registry.emit_unbound("B")

    assert service._leases["C"]["controller_id"] == "B"
    assert tgt_conn.frames[-1]["controller_id"] == "B"


def test_heartbeat_denies_when_target_went_offline():
    ctrl_conn = RecordingConn()
    registry = FakeRegistry({"B": ctrl_conn})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)
    service._leases["C"] = {
        "controller_id": "B",
        "expires_at": service._lease_expiry(),
    }

    service._on_heartbeat("B", make_heartbeat("C", "B"))

    assert "C" not in service._leases
    assert ctrl_conn.frames[-1]["kind"] == "ctrl.deny"
    assert ctrl_conn.frames[-1]["reason"] == "target_offline"


class FakeClockCoordinatorService(CoordinatorService):
    def __init__(self, ctx, registry, dispatcher):
        self.fake_now = 0.0
        super().__init__(ctx, registry, dispatcher)

    def _now(self):
        return self.fake_now


def test_repeated_heartbeats_keep_lease_alive_until_they_stop():
    ctrl_conn = RecordingConn()
    tgt_conn = RecordingConn()
    registry = FakeRegistry({"B": ctrl_conn, "C": tgt_conn})
    dispatcher = FrameDispatcher()
    service = FakeClockCoordinatorService(_ctx(), registry, dispatcher)

    service._on_claim("B", make_claim("C", "B"))

    for _ in range(20):
        service.fake_now += 0.75
        service._on_heartbeat("B", make_heartbeat("C", "B"))
        assert service._expire_once() == []
        assert service._leases["C"]["controller_id"] == "B"

    service.fake_now = service._leases["C"]["expires_at"] + 0.01
    expired = service._expire_once()

    assert expired == [("C", "B")]
    assert tgt_conn.frames[-1]["controller_id"] is None


def test_monitor_inventory_publish_broadcasts_state():
    peer_b = RecordingConn()
    peer_c = RecordingConn()
    registry = FakeRegistry({"B": peer_b, "C": peer_c})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)

    service._on_monitor_inventory_publish(
        "B",
        make_monitor_inventory_publish(
            {
                "node_id": "B",
                "captured_at": "10:00:00",
                "monitors": [
                    {
                        "monitor_id": "1",
                        "display_name": "Display 1",
                        "bounds": {"left": 0, "top": 0, "width": 100, "height": 100},
                        "is_primary": True,
                        "dpi_scale": 1.0,
                        "logical_order": 0,
                    }
                ],
            }
        ),
    )

    assert peer_b.frames[-1]["kind"] == "ctrl.monitor_inventory_state"
    assert peer_c.frames[-1]["kind"] == "ctrl.monitor_inventory_state"
    assert service.ctx.get_monitor_inventory("B").captured_at == "10:00:00"


def test_bound_event_bootstraps_layout_from_previous_coordinator_when_new_lower_node_joins():
    joining_a = RecordingConn()
    peer_c = RecordingConn()
    registry = FakeRegistry({"A": joining_a, "C": peer_c})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx_with_self("B"), registry, dispatcher)
    service._layout_revision = 4

    registry.emit_bound("A")

    bootstrap_frames = [
        frame
        for frame in joining_a.frames
        if frame["kind"] == "ctrl.layout_update" and frame.get("bootstrap") is True
    ]
    assert len(bootstrap_frames) == 1
    assert bootstrap_frames[0]["revision"] == 4


def test_claim_does_not_send_frames_while_internal_lock_is_held():
    holder = {"service": None}
    ctrl_conn = LockCheckingConn(lambda: holder["service"])
    tgt_conn = LockCheckingConn(lambda: holder["service"])
    registry = FakeRegistry({"B": ctrl_conn, "C": tgt_conn})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx(), registry, dispatcher)
    holder["service"] = service

    service._on_claim("B", make_claim("C", "B"))

    assert ctrl_conn.frames[-1]["kind"] == "ctrl.grant"
    assert tgt_conn.frames[-1]["kind"] == "ctrl.lease_update"


def test_bound_event_bootstrap_sends_frames_outside_internal_lock():
    holder = {"service": None}
    joining_a = LockCheckingConn(lambda: holder["service"])
    peer_c = LockCheckingConn(lambda: holder["service"])
    registry = FakeRegistry({"A": joining_a, "C": peer_c})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(_ctx_with_self("B"), registry, dispatcher)
    holder["service"] = service
    service._layout_revision = 4

    registry.emit_bound("A")

    bootstrap_frames = [
        frame
        for frame in joining_a.frames
        if frame["kind"] == "ctrl.layout_update" and frame.get("bootstrap") is True
    ]
    assert len(bootstrap_frames) == 1
