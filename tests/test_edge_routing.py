"""Tests for routing/edge_routing.py."""

from routing.edge_routing import EdgeRoutingResolver, resolve_edge_route
from runtime.layouts import AutoSwitchSettings, LayoutConfig, LayoutNode, replace_layout_monitors


def _layout():
    return LayoutConfig(
        nodes=(
            LayoutNode("A", 0, 0),
            LayoutNode("B", 1, 0),
        ),
        auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
    )


def test_edge_routing_returns_target_switch_for_online_adjacent_node():
    route = resolve_edge_route(
        layout=_layout(),
        self_node_id="A",
        current_node_id="A",
        current_display_id="1",
        direction="right",
        cross_axis_ratio=0.5,
        is_target_online=lambda node_id: True,
    )

    assert route.kind == "target-switch"
    assert route.destination.node_id == "B"


def test_edge_routing_blocks_offline_adjacent_target_from_self():
    route = resolve_edge_route(
        layout=_layout(),
        self_node_id="A",
        current_node_id="A",
        current_display_id="1",
        direction="right",
        cross_axis_ratio=0.5,
        is_target_online=lambda node_id: False,
    )

    assert route.kind == "block"
    assert route.reason == "offline-target"


def test_edge_routing_warps_between_self_monitors_when_physical_neighbor_exists():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )

    route = resolve_edge_route(
        layout=layout,
        self_node_id="A",
        current_node_id="A",
        current_display_id="1",
        direction="left",
        cross_axis_ratio=0.5,
        is_target_online=lambda node_id: True,
    )

    assert route.kind == "self-warp"
    assert route.destination.display_id == "2"


def test_edge_routing_blocks_center_crossing_when_only_logical_neighbor_exists():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )

    route = resolve_edge_route(
        layout=layout,
        self_node_id="A",
        current_node_id="A",
        current_display_id="1",
        direction="right",
        cross_axis_ratio=0.5,
        is_target_online=lambda node_id: True,
    )

    assert route.kind == "block"
    assert route.reason == "self-logical-gap"


def test_edge_routing_resolver_refreshes_when_layout_object_changes():
    resolver = EdgeRoutingResolver()
    first_layout = LayoutConfig(
        nodes=(LayoutNode("A", 0, 0), LayoutNode("B", 1, 0)),
        auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
    )
    second_layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )

    first = resolver.resolve(
        layout=first_layout,
        self_node_id="A",
        current_node_id="A",
        current_display_id="1",
        direction="right",
        cross_axis_ratio=0.5,
        is_target_online=lambda node_id: True,
    )
    second = resolver.resolve(
        layout=second_layout,
        self_node_id="A",
        current_node_id="A",
        current_display_id="1",
        direction="right",
        cross_axis_ratio=0.5,
        is_target_online=lambda node_id: True,
    )

    assert first.kind == "target-switch"
    assert second.kind == "block"
