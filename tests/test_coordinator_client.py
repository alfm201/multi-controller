"""Tests for coordinator/client.py coordinator failover and layout sync behavior."""

from coordinator.client import CoordinatorClient
from coordinator.protocol import (
    make_layout_update,
    make_monitor_inventory_state,
    make_node_list_state,
    make_remote_update_status,
)
from network.dispatcher import FrameDispatcher
from runtime.context import NodeInfo, RuntimeContext
from runtime.layouts import build_layout_config
from runtime.monitor_inventory import MonitorBounds, MonitorInventoryItem, MonitorInventorySnapshot


class FakeConn:
    def __init__(self):
        self.frames = []
        self.closed = False

    def send_frame(self, frame):
        self.frames.append(frame)
        return True


class FakeRegistry:
    def __init__(self, conns):
        self._conns = conns
        self._unbind_listeners = []

    def get(self, node_id):
        return self._conns.get(node_id)

    def add_unbind_listener(self, callback):
        self._unbind_listeners.append(callback)

    def emit_unbound(self, node_id):
        for callback in self._unbind_listeners:
            callback(node_id)


class FakeRouter:
    def __init__(self, state="inactive", target_id=None):
        self._state = state
        self._target_id = target_id
        self.clears = []
        self.activations = []
        self.pending_targets = []

    def get_target_state(self):
        return self._state

    def get_requested_target(self):
        return self._target_id

    def get_selected_target(self):
        return self._target_id

    def get_active_target(self):
        if self._state == "active":
            return self._target_id
        return None

    def clear_target(self, reason=None):
        self.clears.append(reason)
        self._state = "inactive"
        self._target_id = None

    def activate_target(self, target_id):
        self.activations.append(target_id)
        self._state = "active"
        self._target_id = target_id

    def set_pending_target(self, target_id):
        self.pending_targets.append(target_id)
        self._state = "pending"
        self._target_id = target_id


class FakeSink:
    def __init__(self):
        self.authorizations = []
        self._controller_id = None

    def set_authorized_controller(self, controller_id):
        self._controller_id = controller_id
        self.authorizations.append(controller_id)

    def get_authorized_controller(self):
        return self._controller_id


class FakeConfigReloader:
    def __init__(self, ctx):
        self.ctx = ctx
        self.calls = []
        self.node_calls = []

    def apply_layout(self, layout, persist=True, debounce_persist=False):
        self.calls.append((layout, persist, debounce_persist))
        self.ctx.replace_layout(layout)

    def apply_nodes_state(self, nodes, *, rename_map=None, persist=True, apply_runtime=True):
        self.node_calls.append((nodes, rename_map, persist, apply_runtime))
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


def test_coordinator_change_reclaims_pending_target():
    ctx = _ctx()
    b = FakeConn()
    c = FakeConn()
    registry = FakeRegistry({"B": b, "C": c})
    dispatcher = FrameDispatcher()
    router = FakeRouter(state="pending", target_id="C")
    sink = FakeSink()
    current = {"node": ctx.get_node("B")}
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
        router=router,
        sink=sink,
    )

    client._on_coordinator_changed("B")
    current["node"] = ctx.get_node("C")
    client._on_coordinator_changed("C")

    assert b.frames[-1]["kind"] == "ctrl.claim"
    assert c.frames[-1]["kind"] == "ctrl.claim"
    assert sink.authorizations == [None, None]


def test_coordinator_change_reheartbeats_active_target():
    ctx = _ctx()
    b = FakeConn()
    c = FakeConn()
    registry = FakeRegistry({"B": b, "C": c})
    dispatcher = FrameDispatcher()
    router = FakeRouter(state="active", target_id="C")
    current = {"node": ctx.get_node("B")}
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
        router=router,
        sink=FakeSink(),
    )

    client._on_coordinator_changed("B")
    current["node"] = ctx.get_node("C")
    client._on_coordinator_changed("C")

    assert b.frames[-1]["kind"] == "ctrl.heartbeat"
    assert c.frames[-1]["kind"] == "ctrl.heartbeat"


def test_lease_update_only_from_current_coordinator_is_applied():
    ctx = _ctx()
    registry = FakeRegistry({})
    dispatcher = FrameDispatcher()
    sink = FakeSink()
    current = {"node": ctx.get_node("B")}
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
        router=None,
        sink=sink,
    )

    client._on_lease_update("C", {"target_id": "A", "controller_id": "B", "coordinator_epoch": "B:1"})
    client._on_lease_update("B", {"target_id": "A", "controller_id": "B", "coordinator_epoch": "B:1"})

    assert sink.authorizations == ["B"]


def test_grant_from_stale_coordinator_is_ignored():
    ctx = _ctx()
    b = FakeConn()
    registry = FakeRegistry({"B": b})
    dispatcher = FrameDispatcher()
    router = FakeRouter(state="pending", target_id="C")
    current = {"node": ctx.get_node("B")}
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
        router=router,
        sink=FakeSink(),
    )

    client._on_grant(
        "C",
        {
            "target_id": "C",
            "controller_id": "A",
            "coordinator_epoch": "C:1",
            "lease_ttl_ms": 3000,
        },
    )

    assert router.activations == []


def test_old_epoch_from_same_coordinator_is_ignored_after_new_epoch_seen():
    ctx = _ctx()
    registry = FakeRegistry({})
    dispatcher = FrameDispatcher()
    sink = FakeSink()
    current = {"node": ctx.get_node("B")}
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
        router=None,
        sink=sink,
    )

    client._on_lease_update("B", {"target_id": "A", "controller_id": "B", "coordinator_epoch": "B:2"})
    client._on_lease_update("B", {"target_id": "A", "controller_id": "C", "coordinator_epoch": "B:1"})

    assert sink.authorizations == ["B"]


def test_newer_epoch_from_same_coordinator_replaces_old_authorization():
    ctx = _ctx()
    registry = FakeRegistry({})
    dispatcher = FrameDispatcher()
    sink = FakeSink()
    current = {"node": ctx.get_node("B")}
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
        router=None,
        sink=sink,
    )

    client._on_lease_update("B", {"target_id": "A", "controller_id": "B", "coordinator_epoch": "B:1"})
    client._on_lease_update("B", {"target_id": "A", "controller_id": "C", "coordinator_epoch": "B:2"})

    assert sink.authorizations == ["B", None, "C"]


def test_control_tick_sends_heartbeat_only_after_interval():
    ctx = _ctx()
    b = FakeConn()
    registry = FakeRegistry({"B": b})
    dispatcher = FrameDispatcher()
    router = FakeRouter(state="active", target_id="C")
    current = {"node": ctx.get_node("B")}
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
        router=router,
        sink=FakeSink(),
    )
    client._last_coordinator_id = "B"

    deadline = 0.0
    last_target = None
    for _ in range(3):
        deadline, last_target = client._control_tick(deadline, last_target)

    heartbeat_frames = [frame for frame in b.frames if frame["kind"] == "ctrl.heartbeat"]
    assert len(heartbeat_frames) == 1
    assert deadline == client.CONTROL_POLL_INTERVAL_SEC
    assert last_target == "C"


def test_control_tick_resets_heartbeat_deadline_when_target_changes():
    ctx = _ctx()
    b = FakeConn()
    registry = FakeRegistry({"B": b})
    dispatcher = FrameDispatcher()
    router = FakeRouter(state="active", target_id="C")
    current = {"node": ctx.get_node("B")}
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
        router=router,
        sink=FakeSink(),
    )
    client._last_coordinator_id = "B"

    deadline, last_target = client._control_tick(0.5, None)
    assert [frame for frame in b.frames if frame["kind"] == "ctrl.heartbeat"] == []

    router._target_id = "B"
    deadline, last_target = client._control_tick(deadline, last_target)

    heartbeat_frames = [frame for frame in b.frames if frame["kind"] == "ctrl.heartbeat"]
    assert heartbeat_frames == []
    assert deadline == client.CONTROL_POLL_INTERVAL_SEC
    assert last_target == "B"


def test_request_layout_edit_sends_begin_frame_to_coordinator():
    ctx = _ctx()
    b = FakeConn()
    registry = FakeRegistry({"B": b})
    dispatcher = FrameDispatcher()
    current = {"node": ctx.get_node("B")}
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
    )

    assert client.request_layout_edit() is True
    assert b.frames[-1]["kind"] == "ctrl.layout_edit_begin"
    assert client.is_layout_edit_pending() is True


def test_request_layout_edit_keeps_pending_when_send_fails_for_retry():
    ctx = _ctx()
    registry = FakeRegistry({})
    dispatcher = FrameDispatcher()
    current = {"node": ctx.get_node("B")}
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
    )

    assert client.request_layout_edit() is False
    assert client.is_layout_edit_pending() is True


def test_layout_edit_deny_tracks_current_editor():
    ctx = _ctx()
    registry = FakeRegistry({"B": FakeConn()})
    dispatcher = FrameDispatcher()
    current = {"node": ctx.get_node("B")}
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
    )
    client.request_layout_edit()

    client._on_layout_edit_deny(
        "B",
        {
            "editor_id": "A",
            "reason": "held_by_other",
            "current_editor_id": "C",
            "coordinator_epoch": "B:1",
        },
    )

    assert client.get_layout_editor() == "C"
    assert client.get_layout_edit_denial() == "held_by_other"
    assert client.is_layout_edit_pending() is False


def test_layout_edit_grant_marks_local_editor():
    ctx = _ctx()
    registry = FakeRegistry({"B": FakeConn()})
    dispatcher = FrameDispatcher()
    current = {"node": ctx.get_node("B")}
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
    )
    client.request_layout_edit()

    client._on_layout_edit_grant(
        "B",
        {
            "editor_id": "A",
            "coordinator_epoch": "B:1",
        },
    )

    assert client.is_layout_editor() is True
    assert client.get_layout_edit_denial() is None


def test_layout_state_tracks_remote_editor():
    ctx = _ctx()
    registry = FakeRegistry({})
    dispatcher = FrameDispatcher()
    current = {"node": ctx.get_node("B")}
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
    )

    client._on_layout_state(
        "B",
        {
            "editor_id": "C",
            "coordinator_epoch": "B:1",
        },
    )

    assert client.get_layout_editor() == "C"


def test_layout_state_clears_pending_when_other_editor_is_active():
    ctx = _ctx()
    registry = FakeRegistry({"B": FakeConn()})
    dispatcher = FrameDispatcher()
    current = {"node": ctx.get_node("B")}
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
    )
    client.request_layout_edit()

    client._on_layout_state(
        "B",
        {
            "editor_id": "C",
            "coordinator_epoch": "B:1",
        },
    )

    assert client.get_layout_editor() == "C"
    assert client.is_layout_edit_pending() is False


def test_control_tick_retries_pending_layout_edit_request():
    ctx = _ctx()
    b = FakeConn()
    registry = FakeRegistry({"B": b})
    dispatcher = FrameDispatcher()
    current = {"node": ctx.get_node("B")}
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
    )
    client._last_coordinator_id = "B"
    client.request_layout_edit()
    initial_count = len(b.frames)
    client._layout_edit_requested_at -= client.LAYOUT_EDIT_RETRY_INTERVAL_SEC

    client._control_tick(0.0, None)

    begin_frames = [frame for frame in b.frames if frame["kind"] == "ctrl.layout_edit_begin"]
    assert len(begin_frames) == initial_count + 1


def test_bootstrap_layout_update_applies_even_when_sender_is_not_current_coordinator():
    ctx = _ctx()
    registry = FakeRegistry({})
    dispatcher = FrameDispatcher()
    current = {"node": ctx.get_node("A")}
    reloader = FakeConfigReloader(ctx)
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
        config_reloader=reloader,
    )

    client._on_layout_update(
        "B",
        make_layout_update(
            layout={
                "nodes": {
                    "A": {"x": 0, "y": 0, "width": 1, "height": 1},
                    "B": {"x": 3, "y": 0, "width": 1, "height": 1},
                    "C": {"x": 0, "y": 1, "width": 1, "height": 1},
                },
                "auto_switch": {
                    "enabled": True,
                    "cooldown_ms": 250,
                    "return_guard_ms": 350,
                },
            },
            editor_id="B",
            coordinator_epoch="B:5",
            revision=7,
            bootstrap=True,
        ),
    )

    assert len(reloader.calls) == 1
    assert ctx.layout.get_node("B").x == 3


def test_layout_update_applies_runtime_layout_and_persists():
    ctx = _ctx()
    registry = FakeRegistry({})
    dispatcher = FrameDispatcher()
    current = {"node": ctx.get_node("B")}
    reloader = FakeConfigReloader(ctx)
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
        config_reloader=reloader,
    )

    client._on_layout_update(
        "B",
        make_layout_update(
            layout={
                "nodes": {
                    "A": {"x": 0, "y": 0, "width": 1, "height": 1},
                    "B": {"x": 2, "y": 0, "width": 1, "height": 1},
                    "C": {"x": 0, "y": 1, "width": 1, "height": 1},
                },
                "auto_switch": {
                    "enabled": True,
                    "edge_threshold": 0.02,
                    "warp_margin": 0.04,
                    "cooldown_ms": 250,
                },
            },
            editor_id="A",
            coordinator_epoch="B:1",
            revision=1,
        ),
    )

    assert len(reloader.calls) == 1
    layout, persist, debounce_persist = reloader.calls[0]
    assert persist is True
    assert debounce_persist is False
    assert layout.get_node("B").x == 2
    assert ctx.layout.auto_switch.enabled is True
    assert client.get_layout_editor() == "A"


def test_layout_preview_update_applies_without_persisting():
    ctx = _ctx()
    registry = FakeRegistry({})
    dispatcher = FrameDispatcher()
    current = {"node": ctx.get_node("B")}
    reloader = FakeConfigReloader(ctx)
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
        config_reloader=reloader,
    )

    client._on_layout_update(
        "B",
        make_layout_update(
            layout={
                "nodes": {
                    "A": {"x": 0, "y": 0, "width": 1, "height": 1},
                    "B": {"x": 2, "y": 0, "width": 1, "height": 1},
                    "C": {"x": 0, "y": 1, "width": 1, "height": 1},
                },
                "auto_switch": {
                    "enabled": True,
                    "edge_threshold": 0.02,
                    "warp_margin": 0.04,
                    "cooldown_ms": 250,
                },
            },
            editor_id="A",
            coordinator_epoch="B:1",
            revision=1,
            persist=False,
        ),
    )

    assert len(reloader.calls) == 1
    layout, persist, debounce_persist = reloader.calls[0]
    assert persist is False
    assert debounce_persist is False
    assert layout.get_node("B").x == 2


def test_publish_layout_sends_request_only_when_local_editor():
    ctx = _ctx()
    b = FakeConn()
    registry = FakeRegistry({"B": b})
    dispatcher = FrameDispatcher()
    current = {"node": ctx.get_node("B")}
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
    )

    assert client.publish_layout(ctx.layout) is False
    client.request_layout_edit()
    client._on_layout_edit_grant(
        "B",
        {
            "editor_id": "A",
            "coordinator_epoch": "B:1",
        },
    )

    assert client.publish_layout(ctx.layout, persist=False) is True
    assert b.frames[-1]["kind"] == "ctrl.layout_update_request"
    assert b.frames[-1]["persist"] is False


def test_coordinator_change_reissues_layout_edit_when_requested():
    ctx = _ctx()
    b = FakeConn()
    c = FakeConn()
    registry = FakeRegistry({"B": b, "C": c})
    dispatcher = FrameDispatcher()
    current = {"node": ctx.get_node("B")}
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
    )
    client.request_layout_edit()

    client._on_coordinator_changed("B")
    current["node"] = ctx.get_node("C")
    client._on_coordinator_changed("C")

    begin_frames_b = [frame for frame in b.frames if frame["kind"] == "ctrl.layout_edit_begin"]
    begin_frames_c = [frame for frame in c.frames if frame["kind"] == "ctrl.layout_edit_begin"]
    assert len(begin_frames_b) >= 1
    assert len(begin_frames_c) >= 1


def test_publish_monitor_inventory_sends_snapshot_to_coordinator():
    ctx = _ctx()
    b = FakeConn()
    registry = FakeRegistry({"B": b})
    dispatcher = FrameDispatcher()
    current = {"node": ctx.get_node("B")}
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "Display 1", MonitorBounds(0, 0, 100, 100)),
        ),
        captured_at="10:00:00",
    )

    client.publish_monitor_inventory(snapshot)

    assert b.frames[-1]["kind"] == "ctrl.monitor_inventory_publish"
    assert b.frames[-1]["snapshot"]["node_id"] == "A"
    assert ctx.get_monitor_inventory("A").captured_at == "10:00:00"


def test_request_auto_switch_enabled_sends_shared_layout_toggle_to_coordinator():
    ctx = _ctx()
    b = FakeConn()
    registry = FakeRegistry({"B": b})
    dispatcher = FrameDispatcher()
    current = {"node": ctx.get_node("B")}
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
    )

    assert client.request_auto_switch_enabled(False) is True
    assert b.frames[-1]["kind"] == "ctrl.auto_switch_update_request"
    assert b.frames[-1]["enabled"] is False
    assert b.frames[-1]["requester_id"] == "A"


def test_local_input_override_sends_once_per_controller_until_lease_changes():
    ctx = _ctx()
    b = FakeConn()
    registry = FakeRegistry({"B": b})
    dispatcher = FrameDispatcher()
    current = {"node": ctx.get_node("B")}
    sink = FakeSink()
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
        sink=sink,
    )

    sink.set_authorized_controller("C")

    assert client.notify_local_input_override() is True
    assert client.notify_local_input_override() is True
    override_frames = [frame for frame in b.frames if frame["kind"] == "ctrl.local_input_override"]
    assert len(override_frames) == 1

    client._on_lease_update(
        "B",
        {"target_id": "A", "controller_id": None, "coordinator_epoch": "B:1"},
    )
    sink.set_authorized_controller("C")
    assert client.notify_local_input_override() is True
    override_frames = [frame for frame in b.frames if frame["kind"] == "ctrl.local_input_override"]
    assert len(override_frames) == 2


def test_monitor_inventory_state_updates_context():
    ctx = _ctx()
    registry = FakeRegistry({})
    dispatcher = FrameDispatcher()
    current = {"node": ctx.get_node("B")}
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
    )

    client._on_monitor_inventory_state(
        "B",
        make_monitor_inventory_state(
            {
                "node_id": "C",
                "captured_at": "10:10:10",
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
            },
            "B:1",
        ),
    )

    assert ctx.get_monitor_inventory("C").captured_at == "10:10:10"


def test_node_list_state_updates_runtime_context():
    ctx = _ctx()
    registry = FakeRegistry({})
    dispatcher = FrameDispatcher()
    current = {"node": ctx.get_node("B")}
    reloader = FakeConfigReloader(ctx)
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
        config_reloader=reloader,
    )

    client._on_node_list_state(
        "B",
        make_node_list_state(
            [
                {"name": "A", "ip": "127.0.0.1", "port": 5000},
                {"name": "B", "ip": "127.0.0.1", "port": 5001, "note": "회의실"},
                {"name": "D", "ip": "127.0.0.1", "port": 5003},
            ],
            "B:1",
        ),
    )

    assert reloader.node_calls
    assert ctx.get_node("B").note == "회의실"
    assert ctx.get_node("D") is not None


def test_peer_unbound_clears_selected_target():
    ctx = _ctx()
    registry = FakeRegistry({"B": FakeConn()})
    dispatcher = FrameDispatcher()
    router = FakeRouter(state="active", target_id="B")
    CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: ctx.get_node("A"),
        router=router,
        sink=FakeSink(),
    )

    registry.emit_unbound("B")

    assert router.get_selected_target() is None
    assert router.clears[-1] == "target-offline"


def test_remote_update_status_handler_receives_forwarded_status():
    ctx = _ctx()
    registry = FakeRegistry({})
    dispatcher = FrameDispatcher()
    current = {"node": ctx.get_node("B")}
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
    )
    received = []
    client.set_remote_update_status_handler(received.append)

    client._on_remote_update_status(
        "B",
        make_remote_update_status("C", "A", "completed", "", "B:1"),
    )

    assert received == [
        {
            "target_id": "C",
            "requester_id": "A",
            "status": "completed",
            "detail": "",
            "coordinator_epoch": "B:1",
        }
    ]


def test_auto_switch_change_handler_receives_remote_toggle_from_other_node():
    ctx = _ctx()
    registry = FakeRegistry({})
    dispatcher = FrameDispatcher()
    current = {"node": ctx.get_node("B")}
    reloader = FakeConfigReloader(ctx)
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
        config_reloader=reloader,
    )
    received = []
    client.set_auto_switch_change_handler(received.append)

    client._on_layout_update(
        "B",
        make_layout_update(
            layout={
                "nodes": {
                    "A": {"x": 0, "y": 0, "width": 1, "height": 1},
                    "B": {"x": 1, "y": 0, "width": 1, "height": 1},
                    "C": {"x": 0, "y": 1, "width": 1, "height": 1},
                },
                "auto_switch": {
                    "enabled": False,
                    "edge_threshold": 0.02,
                    "warp_margin": 0.04,
                    "cooldown_ms": 250,
                },
            },
            editor_id="",
            coordinator_epoch="B:2",
            revision=2,
            change_kind="auto_switch_toggle",
            requester_id="C",
        ),
    )

    assert received == [
        {
            "enabled": False,
            "requester_id": "C",
            "coordinator_epoch": "B:2",
        }
    ]


def test_auto_switch_change_handler_ignores_self_originated_toggle():
    ctx = _ctx()
    registry = FakeRegistry({})
    dispatcher = FrameDispatcher()
    current = {"node": ctx.get_node("B")}
    reloader = FakeConfigReloader(ctx)
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
        config_reloader=reloader,
    )
    received = []
    client.set_auto_switch_change_handler(received.append)

    client._on_layout_update(
        "B",
        make_layout_update(
            layout={
                "nodes": {
                    "A": {"x": 0, "y": 0, "width": 1, "height": 1},
                    "B": {"x": 1, "y": 0, "width": 1, "height": 1},
                    "C": {"x": 0, "y": 1, "width": 1, "height": 1},
                },
                "auto_switch": {
                    "enabled": False,
                    "edge_threshold": 0.02,
                    "warp_margin": 0.04,
                    "cooldown_ms": 250,
                },
            },
            editor_id="",
            coordinator_epoch="B:2",
            revision=2,
            change_kind="auto_switch_toggle",
            requester_id="A",
        ),
    )

    assert received == []


def test_node_list_change_listener_receives_added_nodes():
    ctx = _ctx()
    registry = FakeRegistry({})
    dispatcher = FrameDispatcher()
    current = {"node": ctx.get_node("B")}
    reloader = FakeConfigReloader(ctx)
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
        config_reloader=reloader,
    )
    received = []
    client.add_node_list_change_listener(received.append)

    client._on_node_list_state(
        "B",
        make_node_list_state(
            [
                {"name": "A", "ip": "127.0.0.1", "port": 5000},
                {"name": "B", "ip": "127.0.0.1", "port": 5001},
                {"name": "C", "ip": "127.0.0.1", "port": 5002},
                {"name": "D", "ip": "127.0.0.1", "port": 5003, "note": "new"},
            ],
            "B:1",
            rename_map={},
        ),
    )

    assert received == [{"added_node_ids": ("D",), "coordinator_epoch": "B:1"}]


def test_request_target_notifies_failure_when_claim_send_fails():
    ctx = _ctx()
    registry = FakeRegistry({})
    dispatcher = FrameDispatcher()
    router = FakeRouter()
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: ctx.get_node("B"),
        router=router,
        sink=FakeSink(),
    )
    events = []
    client.add_target_result_listener(
        lambda status, target_id, reason, source: events.append((status, target_id, reason, source))
    )

    started = client.request_target("C", source="ui")

    assert started is False
    assert router.clears[-1] == "claim-send-failed"
    assert events == [("failed", "C", "coordinator_unreachable", "ui")]


def test_grant_notifies_active_result_with_source():
    ctx = _ctx()
    registry = FakeRegistry({"B": FakeConn()})
    dispatcher = FrameDispatcher()
    router = FakeRouter(state="pending", target_id="C")
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: ctx.get_node("B"),
        router=router,
        sink=FakeSink(),
    )
    client._requested_target_id = "C"
    client._requested_target_source = "hotkey"
    events = []
    client.add_target_result_listener(
        lambda status, target_id, reason, source: events.append((status, target_id, reason, source))
    )

    client._on_grant(
        "B",
        {
            "target_id": "C",
            "controller_id": "A",
            "coordinator_epoch": "B:1",
            "lease_ttl_ms": 3000,
        },
    )

    assert events == [("active", "C", None, "hotkey")]
