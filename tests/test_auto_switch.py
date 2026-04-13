"""Tests for routing/auto_switch.py."""

from __future__ import annotations

from capture.input_capture import MoveProcessingResult
from routing.auto_switch import AutoTargetSwitcher, detect_edge_direction
from runtime.context import NodeInfo, RuntimeContext
from runtime.layouts import AutoSwitchSettings, LayoutConfig, LayoutNode, replace_layout_monitors
from runtime.monitor_inventory import (
    MonitorBounds,
    MonitorInventoryItem,
    MonitorInventorySnapshot,
)


class FakeRouter:
    def __init__(self, selected_target=None, active_target=None, last_remote_anchor_event=None):
        self._selected_target = selected_target
        self._active_target = active_target
        self.handoffs = []
        self._last_remote_anchor_event = last_remote_anchor_event

    def get_selected_target(self):
        return self._selected_target

    def get_active_target(self):
        return self._active_target

    def prepare_pointer_handoff(self, anchor_event):
        self.handoffs.append(anchor_event)

    def get_last_remote_anchor_event(self):
        if self._last_remote_anchor_event is None:
            return None
        return dict(self._last_remote_anchor_event)


class FakeBounds:
    def __init__(self, left=0, top=0, width=1920, height=1080):
        self.left = left
        self.top = top
        self.width = width
        self.height = height


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, delta):
        self.value += delta


def _ctx(layout):
    nodes = [
        NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000}),
        NodeInfo.from_dict({"name": "B", "ip": "127.0.0.1", "port": 5001}),
        NodeInfo.from_dict({"name": "C", "ip": "127.0.0.1", "port": 5002}),
    ]
    return RuntimeContext(self_node=nodes[0], nodes=nodes, layout=layout)


def _ctx_with_inventory(layout, *snapshots):
    ctx = _ctx(layout)
    for snapshot in snapshots:
        ctx.replace_monitor_inventory(snapshot)
    return ctx


def _layout(enabled=True):
    return LayoutConfig(
        nodes=(
            LayoutNode("A", 0, 0),
            LayoutNode("B", 1, 0),
            LayoutNode("C", 2, 0),
        ),
        auto_switch=AutoSwitchSettings(
            enabled=enabled,
            cooldown_ms=250,
            return_guard_ms=400,
        ),
    )


def test_detect_edge_direction_prefers_nearest_edge():
    assert detect_edge_direction({"x_norm": 0.99, "y_norm": 0.4}, 0.05) == ("right", 0.4)
    assert detect_edge_direction({"x_norm": 0.2, "y_norm": 0.01}, 0.05) == ("up", 0.2)
    assert detect_edge_direction({"x_norm": 0.5, "y_norm": 0.5}, 0.05) == (None, None)


def test_auto_switch_requests_adjacent_target_and_blocks_local_move():
    requests = []
    clears = []
    moves = []
    router = FakeRouter(selected_target=None)
    switcher = AutoTargetSwitcher(
        _ctx(_layout(enabled=True)),
        router,
        request_target=requests.append,
        clear_target=lambda: clears.append("clear"),
        pointer_mover=lambda x, y: moves.append((x, y)),
        screen_bounds_provider=lambda: FakeBounds(),
        now_fn=FakeClock(),
    )

    event = {"kind": "mouse_move", "x": 1919, "y": 600, "x_norm": 0.999, "y_norm": 0.55}
    result = switcher.process(event)

    assert result == MoveProcessingResult(None, True)
    assert requests == ["B"]
    assert clears == []
    assert router.handoffs[-1]["x"] == 0
    assert moves == []


def test_auto_switch_keeps_self_internal_routing_when_remote_switching_disabled():
    display1 = r"\\.\DISPLAY1"
    display2 = r"\\.\DISPLAY2"
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=False, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[[display2, display1]],
        physical_rows=[[display1, display2]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem(display2, display2, MonitorBounds(-1920, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem(display1, display1, MonitorBounds(0, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-11T00:00:00",
    )
    moves = []
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        actual_pointer_provider=lambda: (1919, 396),
        screen_bounds_provider=lambda: FakeBounds(left=-1920, width=3840),
        now_fn=FakeClock(),
    )
    switcher.refresh_self_clip()

    result = switcher.process(
        {"kind": "mouse_move", "x": 1919, "y": 396, "x_norm": 0.999, "y_norm": 0.36}
    )

    assert result == MoveProcessingResult(None, True)
    assert moves == [(-1920, 396)]
    assert switcher._display_state_by_node["A"] == display2


def test_auto_switch_skips_remote_target_switch_when_disabled():
    requests = []
    router = FakeRouter(selected_target=None)
    switcher = AutoTargetSwitcher(
        _ctx(_layout(enabled=False)),
        router,
        request_target=requests.append,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: None,
        screen_bounds_provider=lambda: FakeBounds(),
        now_fn=FakeClock(),
    )

    event = {"kind": "mouse_move", "x": 1919, "y": 600, "x_norm": 0.999, "y_norm": 0.55}
    result = switcher.process(event)

    assert result == event
    assert requests == []


def test_auto_switch_can_return_to_self_and_clear_target():
    requests = []
    clears = []
    moves = []
    router = FakeRouter(selected_target="B", active_target="B")
    switcher = AutoTargetSwitcher(
        _ctx(_layout(enabled=True)),
        router,
        request_target=requests.append,
        clear_target=lambda: clears.append("clear"),
        pointer_mover=lambda x, y: moves.append((x, y)),
        screen_bounds_provider=lambda: FakeBounds(),
        now_fn=FakeClock(),
    )
    switcher._display_state_by_node["B"] = "1"

    event = {"kind": "mouse_move", "x": 0, "y": 450, "x_norm": 0.0, "y_norm": 0.4}
    result = switcher.process(event)

    assert result == MoveProcessingResult(None, True)
    assert requests == []
    assert clears == ["clear"]
    assert moves == [(0, 450), (1919, 450)]


def test_auto_switch_self_internal_warp_updates_cached_display():
    display1 = r"\\.\DISPLAY1"
    display2 = r"\\.\DISPLAY2"
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[[display2, display1]],
        physical_rows=[[display1, display2]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem(display2, display2, MonitorBounds(-1920, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem(display1, display1, MonitorBounds(0, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-11T00:00:00",
    )
    moves = []
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        actual_pointer_provider=lambda: (1919, 396),
        screen_bounds_provider=lambda: FakeBounds(left=-1920, width=3840),
        now_fn=FakeClock(),
    )
    switcher.refresh_self_clip()

    result = switcher.process(
        {"kind": "mouse_move", "x": 1919, "y": 396, "x_norm": 0.999, "y_norm": 0.36}
    )

    assert result == MoveProcessingResult(None, True)
    assert moves == [(-1920, 396)]
    assert switcher._display_state_by_node["A"] == display2


def test_auto_switch_blocks_center_crossing_when_physical_neighbor_is_missing():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-11T00:00:00",
    )
    moves = []
    positions = iter(((1919, 540), (1920, 540)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    switcher.refresh_self_clip()
    result = switcher.process(
        {"kind": "mouse_move", "x": 1920, "y": 540, "x_norm": 0.50013, "y_norm": 0.5}
    )

    assert result == MoveProcessingResult(None, True)
    assert moves == [(1918, 540)]
    assert switcher._display_state_by_node["A"] == "1"


def test_auto_switch_outer_edge_warps_when_physical_neighbor_exists():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-11T00:00:00",
    )
    moves = []
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        actual_pointer_provider=lambda: (0, 540),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    switcher.refresh_self_clip()
    result = switcher.process(
        {"kind": "mouse_move", "x": 0, "y": 540, "x_norm": 0.0, "y_norm": 0.5}
    )

    assert result == MoveProcessingResult(None, True)
    assert moves == [(3839, 540)]
    assert switcher._display_state_by_node["A"] == "2"


def test_auto_switch_remote_internal_warp_forwards_anchor_and_blocks_local_move():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0), LayoutNode("B", 1, 0)),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "B",
        logical_rows=[["1", "2"]],
        physical_rows=[["1", "2"]],
    )
    moves = []
    router = FakeRouter(selected_target="B", active_target="B")
    switcher = AutoTargetSwitcher(
        _ctx(layout),
        router,
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )
    switcher._display_state_by_node["B"] = "1"

    result = switcher.process(
        {"kind": "mouse_move", "x": 1919, "y": 480, "x_norm": 0.5, "y_norm": 0.45}
    )

    assert isinstance(result, MoveProcessingResult)
    assert result.block_local is True
    assert result.event is not None
    assert result.event["kind"] == "mouse_move"
    assert moves == [(1919, 480), (1920, 480)]
    assert switcher._display_state_by_node["B"] == "2"


def test_auto_switch_respects_cooldown_window():
    clock = FakeClock()
    requests = []
    switcher = AutoTargetSwitcher(
        _ctx(_layout(enabled=True)),
        FakeRouter(selected_target=None),
        request_target=requests.append,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: None,
        screen_bounds_provider=lambda: FakeBounds(),
        now_fn=clock,
    )

    event = {"kind": "mouse_move", "x": 1919, "y": 600, "x_norm": 0.999, "y_norm": 0.55}
    assert switcher.process(event) == MoveProcessingResult(None, True)
    assert switcher.process(event) == event
    clock.advance(0.3)
    assert switcher.process(event) == MoveProcessingResult(None, True)
    assert requests == ["B", "B"]


def test_auto_switch_pending_target_keeps_current_node_as_self():
    requests = []
    moves = []
    router = FakeRouter(selected_target="B", active_target=None)
    switcher = AutoTargetSwitcher(
        _ctx(_layout(enabled=True)),
        router,
        request_target=requests.append,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        screen_bounds_provider=lambda: FakeBounds(),
        now_fn=FakeClock(),
    )

    event = {"kind": "mouse_move", "x": 1919, "y": 540, "x_norm": 0.999, "y_norm": 0.5}
    result = switcher.process(event)

    assert result == MoveProcessingResult(None, True)
    assert requests == ["B"]
    assert moves == []


def test_active_target_uses_virtual_remote_pointer_and_blocks_local_move():
    layout = _layout(enabled=True)
    router = FakeRouter(
        selected_target="B",
        active_target="B",
        last_remote_anchor_event={"kind": "mouse_move", "x": 960, "y": 540, "x_norm": 0.5, "y_norm": 0.5},
    )
    moves = []
    clock = FakeClock()
    pointer_positions = [(100, 100)]
    switcher = AutoTargetSwitcher(
        _ctx(layout),
        router,
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        actual_pointer_provider=lambda: pointer_positions[-1],
        screen_bounds_provider=lambda: FakeBounds(),
        now_fn=clock,
    )
    switcher._display_state_by_node["B"] = "1"
    switcher.on_router_state_change("active", "B")

    result = switcher.process(
        {"kind": "mouse_move", "x": 130, "y": 120, "x_norm": 130 / 1919, "y_norm": 120 / 1079, "ts": 1.0}
    )

    assert isinstance(result, MoveProcessingResult)
    assert result.block_local is True
    assert result.event is not None
    assert result.event["kind"] == "mouse_move"
    assert result.event["x"] == 990
    assert result.event["y"] == 560
    assert moves == [(100, 100)]
