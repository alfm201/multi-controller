"""Tests for routing/display_state.py."""

from routing.display_state import DisplayStateTracker
from runtime.context import NodeInfo, RuntimeContext
from runtime.layouts import AutoSwitchSettings, LayoutConfig, LayoutNode, replace_layout_monitors
from runtime.monitor_inventory import (
    MonitorBounds,
    MonitorInventoryItem,
    MonitorInventorySnapshot,
)


class FakeBounds:
    def __init__(self, left=0, top=0, width=1920, height=1080):
        self.left = left
        self.top = top
        self.width = width
        self.height = height


def _ctx(layout):
    nodes = [
        NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000}),
        NodeInfo.from_dict({"name": "B", "ip": "127.0.0.1", "port": 5001}),
    ]
    return RuntimeContext(self_node=nodes[0], nodes=nodes, layout=layout)


def _ctx_with_inventory(layout, *snapshots):
    ctx = _ctx(layout)
    for snapshot in snapshots:
        ctx.replace_monitor_inventory(snapshot)
    return ctx


def test_display_state_syncs_self_display_from_actual_pointer():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["1", "2"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-11T00:00:00",
    )
    ctx = _ctx_with_inventory(layout, snapshot)
    tracker = DisplayStateTracker(ctx, actual_pointer_provider=lambda: (1950, 540))

    display_id = tracker.sync_self_display_state(layout.get_node("A"))

    assert display_id == "2"
    assert tracker.state["A"] == "2"


def test_display_state_prefers_cached_display_for_current_node():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )
    ctx = _ctx(layout)
    tracker = DisplayStateTracker(ctx, actual_pointer_provider=lambda: (1950, 540))
    tracker.remember("A", "1")

    display_id = tracker.current_display_id(
        "A",
        layout.get_node("A"),
        {"kind": "mouse_move", "x_norm": 0.75, "y_norm": 0.5},
    )

    assert display_id == "1"


def test_display_state_reconciles_stale_self_cache_with_actual_pointer():
    display1 = r"\\.\DISPLAY1"
    display2 = r"\\.\DISPLAY2"
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[[display2, display1]],
        physical_rows=[[display2, display1]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem(display2, display2, MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem(display1, display1, MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-15T00:00:00",
    )
    ctx = _ctx_with_inventory(layout, snapshot)
    tracker = DisplayStateTracker(ctx, actual_pointer_provider=lambda: (0, 540))
    tracker.remember("A", display1)

    display_id = tracker.current_display_id(
        "A",
        layout.get_node("A"),
        {"kind": "mouse_move", "x": 0, "y": 540, "x_norm": 0.0, "y_norm": 0.5},
    )

    assert display_id == display2
    assert tracker.state["A"] == display2


def test_display_state_uses_actual_self_pointer_over_stale_event_coordinates():
    display1 = r"\\.\DISPLAY1"
    display2 = r"\\.\DISPLAY2"
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[[display2, display1]],
        physical_rows=[[display2, display1]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem(display2, display2, MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem(display1, display1, MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-15T00:00:00",
    )
    ctx = _ctx_with_inventory(layout, snapshot)
    tracker = DisplayStateTracker(ctx, actual_pointer_provider=lambda: (0, 540))
    tracker.remember("A", display1)

    coerced = tracker.coerce_self_event(
        layout.get_node("A"),
        {"kind": "mouse_move", "x": 1920, "y": 540, "x_norm": 0.50013, "y_norm": 0.5},
        FakeBounds(width=3840),
    )
    display_id = tracker.current_display_id(
        "A",
        layout.get_node("A"),
        coerced,
    )

    assert coerced["x"] == 0
    assert coerced["y"] == 540
    assert display_id == display2
    assert tracker.state["A"] == display2


def test_display_state_prefers_explicit_routing_display_hint_over_actual_pointer():
    display1 = r"\\.\DISPLAY1"
    display2 = r"\\.\DISPLAY2"
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[[display1, display2]],
        physical_rows=[[display1, display2]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem(display1, display1, MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem(display2, display2, MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-17T00:00:00",
    )
    ctx = _ctx_with_inventory(layout, snapshot)
    tracker = DisplayStateTracker(ctx, actual_pointer_provider=lambda: (1920, 540))

    display_id = tracker.current_display_id(
        "A",
        layout.get_node("A"),
        {
            "kind": "mouse_move",
            "x": 1920,
            "y": 540,
            "x_norm": 1920 / 3839,
            "y_norm": 540 / 1079,
            "__routing_display_id__": display1,
        },
    )

    assert display_id == display1
    assert tracker.state["A"] == display1


def test_display_state_builds_block_anchor_on_same_display_edge():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["1", "2"]],
    )
    ctx = _ctx(layout)
    tracker = DisplayStateTracker(ctx)

    event = tracker.build_edge_anchor_event(
        layout.get_node("A"),
        "1",
        "left",
        0.5,
        FakeBounds(width=3840),
        source_event={"x": 0, "y": 540},
        blocked=True,
    )

    assert event["x"] == 1
    assert event["y"] == 540


def test_display_state_builds_block_anchor_one_pixel_inside_right_edge():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["1", "2"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-11T00:00:00",
    )
    ctx = _ctx_with_inventory(layout, snapshot)
    tracker = DisplayStateTracker(ctx)

    event = tracker.build_edge_anchor_event(
        layout.get_node("A"),
        "1",
        "right",
        0.5,
        FakeBounds(width=1920),
        source_event={"x": 1919, "y": 540},
        blocked=True,
    )

    assert event["x"] == 1918
    assert event["y"] == 540


def test_display_state_builds_block_hold_rect_one_pixel_inside_bottom_and_right_edges():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["1", "2"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-11T00:00:00",
    )
    ctx = _ctx_with_inventory(layout, snapshot)
    tracker = DisplayStateTracker(ctx)

    right_rect = tracker.build_edge_hold_rect(layout.get_node("A"), "1", "right", FakeBounds(width=1920))
    down_rect = tracker.build_edge_hold_rect(layout.get_node("A"), "1", "down", FakeBounds(width=1920, height=1080))

    assert right_rect == (1918, 0, 1918, 1079)
    assert down_rect == (0, 1078, 1919, 1078)


def test_display_state_uses_remote_inventory_bounds_for_display_rect():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0), LayoutNode("B", 1, 0)),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "B",
        logical_rows=[["1"]],
        physical_rows=[["1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="B",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(2500, 100, 2560, 1440), logical_order=0, dpi_scale=1.25),
        ),
        captured_at="2026-04-11T00:00:00",
    )
    ctx = _ctx_with_inventory(layout, snapshot)
    tracker = DisplayStateTracker(ctx)

    rect = tracker.display_pixel_rect(layout.get_node("B"), "1", FakeBounds(width=1920, height=1080))

    assert rect == (2500, 100, 5059, 1539)


def test_display_state_pointer_speed_scale_respects_dpi_adjusted_display_sizes():
    layout = LayoutConfig(
        nodes=(LayoutNode("A", 0, 0), LayoutNode("B", 1, 0)),
        auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
    )
    source_snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 3840, 2160), logical_order=0, dpi_scale=1.5),
        ),
        captured_at="2026-04-11T00:00:00",
    )
    target_snapshot = MonitorInventorySnapshot(
        node_id="B",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0, dpi_scale=1.0),
        ),
        captured_at="2026-04-11T00:00:00",
    )
    ctx = _ctx_with_inventory(layout, source_snapshot, target_snapshot)
    tracker = DisplayStateTracker(ctx)

    scale_x, scale_y = tracker.pointer_speed_scale(
        source_node=layout.get_node("A"),
        source_display_id="1",
        source_bounds=FakeBounds(width=3840, height=2160),
        target_node=layout.get_node("B"),
        target_display_id="1",
        target_bounds=FakeBounds(width=1920, height=1080),
    )

    assert round(scale_x, 3) == 0.75
    assert round(scale_y, 3) == 0.75
