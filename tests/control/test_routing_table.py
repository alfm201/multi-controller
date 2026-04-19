"""Tests for control/routing/routing_table.py."""

from control.routing.routing_table import EdgeRoutingTable
from model.display.layouts import AutoSwitchSettings, LayoutConfig, LayoutNode, replace_layout_monitors


def _layout():
    return LayoutConfig(
        nodes=(
            LayoutNode("A", 0, 0),
            LayoutNode("B", 1, 0),
        ),
        auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
    )


def test_routing_table_exposes_adjacent_target_candidate():
    table = EdgeRoutingTable(_layout())

    slot = table.slot_for("A", "1", "right")

    assert slot is not None
    assert slot.pick_physical(0.5).node_id == "B"


def test_routing_table_keeps_logical_gap_without_physical_neighbor():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )
    table = EdgeRoutingTable(layout)

    center_slot = table.slot_for("A", "1", "right")
    outer_slot = table.slot_for("A", "1", "left")

    assert center_slot is not None
    assert center_slot.pick_physical(0.5) is None
    assert center_slot.pick_logical_display_id(0.5) == "2"
    assert outer_slot is not None
    assert outer_slot.pick_physical(0.5).display_id == "2"
