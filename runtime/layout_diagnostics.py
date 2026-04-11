"""레이아웃과 모니터 토폴로지 상태를 덤프하는 진단 유틸리티."""

import json

from runtime.layouts import (
    find_adjacent_display,
    find_adjacent_node,
    find_overlapping_nodes,
    layout_bounds,
    monitor_topology_to_rows,
)


_DIRECTIONS = ("left", "right", "up", "down")


def build_layout_diagnostics(ctx):
    """현재 RuntimeContext 기준의 레이아웃 진단 정보를 수집한다."""
    layout = ctx.layout
    if layout is None:
        return {
            "self_node": ctx.self_node.node_id,
            "config_path": None if ctx.config_path is None else str(ctx.config_path),
            "layout": None,
        }

    min_x, min_y, max_x, max_y = layout_bounds(layout)
    nodes = []
    for node in layout.nodes:
        node_adjacency = {}
        for direction in _DIRECTIONS:
            neighbor = find_adjacent_node(layout, node.node_id, direction, 0.5)
            node_adjacency[direction] = None if neighbor is None else neighbor.node_id

        display_adjacency = {}
        for display in node.monitors().physical:
            neighbors = {}
            for direction in _DIRECTIONS:
                adjacent = find_adjacent_display(
                    layout,
                    node.node_id,
                    display.display_id,
                    direction,
                    0.5,
                )
                neighbors[direction] = (
                    None
                    if adjacent is None
                    else {
                        "node_id": adjacent.node_id,
                        "display_id": adjacent.display_id,
                    }
                )
            display_adjacency[display.display_id] = neighbors

        nodes.append(
            {
                "node_id": node.node_id,
                "x": node.x,
                "y": node.y,
                "width": node.width,
                "height": node.height,
                "logical_monitors": monitor_topology_to_rows(node.monitors(), logical=True),
                "physical_monitors": monitor_topology_to_rows(node.monitors(), logical=False),
                "node_adjacency": node_adjacency,
                "display_adjacency": display_adjacency,
            }
        )

    auto_switch = layout.auto_switch
    return {
        "self_node": ctx.self_node.node_id,
        "config_path": None if ctx.config_path is None else str(ctx.config_path),
        "layout": {
            "bounds": {
                "left": min_x,
                "top": min_y,
                "width": max_x - min_x,
                "height": max_y - min_y,
            },
            "auto_switch": {
                "enabled": auto_switch.enabled,
                "cooldown_ms": auto_switch.cooldown_ms,
                "return_guard_ms": auto_switch.return_guard_ms,
            },
            "overlaps": [
                {"left": left, "right": right}
                for left, right in find_overlapping_nodes(layout)
            ],
            "nodes": nodes,
        },
    }


def format_layout_diagnostics(diagnostics):
    """레이아웃 진단 정보를 사람이 읽기 쉬운 JSON 문자열로 만든다."""
    return json.dumps(diagnostics, ensure_ascii=False, indent=2)
