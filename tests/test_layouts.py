"""Tests for runtime/layouts.py."""

from runtime.layouts import (
    LayoutConfig,
    LayoutNode,
    build_anchor_event,
    build_layout_config,
    detect_display_edge,
    find_adjacent_display,
    find_adjacent_display_in_node,
    find_adjacent_node,
    find_overlapping_nodes,
    layout_bounds,
    monitor_topology_to_rows,
    normalized_display_rect,
    replace_auto_switch_settings,
    replace_layout_monitors,
    replace_layout_node,
    serialize_layout_config,
)


class FakeNode:
    def __init__(self, node_id):
        self.node_id = node_id


def test_build_layout_config_uses_defaults_in_node_order():
    layout = build_layout_config({}, [FakeNode("A"), FakeNode("B"), FakeNode("C")])

    assert [(node.node_id, node.x, node.y) for node in layout.nodes] == [
        ("A", 0, 0),
        ("B", 1, 0),
        ("C", 2, 0),
    ]
    assert layout.auto_switch.enabled is True
    assert layout.get_node("A").monitors().display_ids() == ("1",)


def test_build_layout_config_supports_separate_logical_and_physical_monitor_maps():
    layout = build_layout_config(
        {
            "layout": {
                "nodes": {
                    "A": {
                        "x": 0,
                        "y": 0,
                        "monitors": {
                            "logical": [["1", "2", "3", "4", "5", "6"]],
                            "physical": [["1", "2", "3"], ["4", "5", "6"]],
                        },
                    }
                }
            }
        },
        [FakeNode("A")],
    )

    node = layout.get_node("A")
    assert (node.width, node.height) == (3, 2)
    assert monitor_topology_to_rows(node.monitors(), logical=True) == [["1", "2", "3", "4", "5", "6"]]
    assert monitor_topology_to_rows(node.monitors(), logical=False) == [["1", "2", "3"], ["4", "5", "6"]]


def test_serialize_layout_config_round_trips_basic_fields():
    layout = LayoutConfig(
        nodes=(
            LayoutNode("A", 0, 0),
            LayoutNode("B", 1, 1, width=2, height=1),
        )
    )

    data = serialize_layout_config(layout)

    assert data["nodes"]["A"] == {"x": 0, "y": 0, "width": 1, "height": 1}
    assert data["nodes"]["B"] == {"x": 1, "y": 1, "width": 2, "height": 1}


def test_serialize_layout_config_includes_custom_monitor_maps():
    layout = replace_layout_monitors(
        LayoutConfig(nodes=(LayoutNode("A", 0, 0),)),
        "A",
        logical_rows=[["1", "2"], ["3", "4"]],
        physical_rows=[["1", "2"], ["3", "4"]],
    )

    data = serialize_layout_config(layout)

    assert data["nodes"]["A"]["width"] == 2
    assert data["nodes"]["A"]["height"] == 2
    assert data["nodes"]["A"]["monitors"]["logical"] == [["1", "2"], ["3", "4"]]
    assert data["nodes"]["A"]["monitors"]["physical"] == [["1", "2"], ["3", "4"]]


def test_replace_helpers_update_layout_without_mutating_other_nodes():
    layout = LayoutConfig(nodes=(LayoutNode("A", 0, 0), LayoutNode("B", 1, 0)))

    moved = replace_layout_node(layout, "B", x=3, y=2)
    updated = replace_auto_switch_settings(
        moved,
        enabled=False,
        cooldown_ms=500,
        return_guard_ms=600,
    )

    assert moved.get_node("A").x == 0
    assert moved.get_node("B").x == 3
    assert moved.get_node("B").y == 2
    assert updated.auto_switch.enabled is False
    assert updated.auto_switch.cooldown_ms == 500
    assert updated.auto_switch.return_guard_ms == 600


def test_replace_layout_monitors_updates_node_size():
    layout = LayoutConfig(nodes=(LayoutNode("A", 0, 0), LayoutNode("B", 1, 0)))

    updated = replace_layout_monitors(
        layout,
        "B",
        logical_rows=[["1", "2", "3", "4"]],
        physical_rows=[["1", "2"], ["3", "4"]],
    )

    assert updated.get_node("B").width == 2
    assert updated.get_node("B").height == 2
    assert updated.get_node("A").width == 1


def test_layout_bounds_cover_all_nodes():
    layout = LayoutConfig(
        nodes=(
            LayoutNode("A", -1, 0),
            LayoutNode("B", 1, 2, width=2, height=3),
        )
    )

    assert layout_bounds(layout) == (-1, 0, 3, 5)


def test_find_adjacent_node_uses_direction_and_cross_axis_ratio():
    layout = LayoutConfig(
        nodes=(
            LayoutNode("A", 0, 0),
            LayoutNode("B", 1, 0),
            LayoutNode("C", 1, 1),
            LayoutNode("D", 0, 1),
        )
    )

    assert find_adjacent_node(layout, "A", "right", 0.2).node_id == "B"
    assert find_adjacent_node(layout, "A", "down", 0.3).node_id == "D"
    assert find_adjacent_node(layout, "B", "down", 0.7).node_id == "C"
    assert find_adjacent_node(layout, "C", "left", 0.4).node_id == "D"


def test_detect_display_edge_uses_logical_monitor_regions():
    layout = build_layout_config(
        {
            "layout": {
                "nodes": {
                    "A": {
                        "monitors": {
                            "logical": [["1", "2", "3", "4", "5", "6"]],
                            "physical": [["1", "2", "3"], ["4", "5", "6"]],
                        }
                    }
                }
            }
        },
        [FakeNode("A")],
    )
    node = layout.get_node("A")

    display, direction, cross_ratio = detect_display_edge(node, x_norm=0.52, y_norm=0.01, threshold=0.1)

    assert display.display_id == "4"
    assert direction == "up"
    assert round(cross_ratio, 2) == 0.12


def test_find_adjacent_display_uses_physical_monitor_topology():
    layout = build_layout_config(
        {
            "layout": {
                "nodes": {
                    "A": {
                        "x": 0,
                        "y": 0,
                        "monitors": {
                            "logical": [["1", "2", "3", "4", "5", "6"]],
                            "physical": [["1", "2", "3"], ["4", "5", "6"]],
                        },
                    },
                    "B": {
                        "x": 3,
                        "y": 0,
                    },
                }
            }
        },
        [FakeNode("A"), FakeNode("B")],
    )

    internal = find_adjacent_display(layout, "A", "4", "up", 0.5)
    external = find_adjacent_display(layout, "A", "3", "right", 0.5)

    assert internal.node_id == "A"
    assert internal.display_id == "1"
    assert external.node_id == "B"
    assert external.display_id == "1"


def test_find_adjacent_display_in_node_distinguishes_logical_and_physical_neighbors():
    layout = build_layout_config(
        {
            "layout": {
                "nodes": {
                    "A": {
                        "monitors": {
                            "logical": [["1", "2"]],
                            "physical": [["2", "1"]],
                        }
                    }
                }
            }
        },
        [FakeNode("A")],
    )
    node = layout.get_node("A")

    assert find_adjacent_display_in_node(node, "2", "left", 0.5, logical=True) == "1"
    assert find_adjacent_display_in_node(node, "2", "left", 0.5, logical=False) is None
    assert find_adjacent_display_in_node(node, "2", "right", 0.5, logical=False) == "1"


def test_normalized_display_rect_and_anchor_event_follow_destination_display():
    layout = build_layout_config(
        {
            "layout": {
                "nodes": {
                    "A": {
                        "monitors": {
                            "logical": [["1", "2", "3", "4", "5", "6"]],
                            "physical": [["1", "2", "3"], ["4", "5", "6"]],
                        }
                    }
                }
            }
        },
        [FakeNode("A")],
    )
    node = layout.get_node("A")

    rect = normalized_display_rect(node, "4", logical=True)
    anchor = build_anchor_event(node, "4", "down", 0.25, 0.1)

    assert rect == (0.5, 0.0, 2 / 3, 1.0)
    assert round(anchor["x_norm"], 3) == round(0.5 + ((2 / 3 - 0.5) * 0.25), 3)
    assert round(anchor["y_norm"], 3) == 0.0


def test_find_overlapping_nodes_reports_collisions():
    layout = LayoutConfig(
        nodes=(
            LayoutNode("A", 0, 0, width=2, height=1),
            LayoutNode("B", 1, 0),
            LayoutNode("C", 3, 0),
        )
    )

    assert find_overlapping_nodes(layout) == [("A", "B")]
