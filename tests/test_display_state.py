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

    assert event["x"] == 0
    assert event["y"] == 540
