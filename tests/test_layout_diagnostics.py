"""Tests for runtime/layout_diagnostics.py."""

from runtime.context import build_runtime_context
from runtime.layout_diagnostics import build_layout_diagnostics, format_layout_diagnostics


def test_build_layout_diagnostics_includes_nodes_monitors_and_adjacency():
    config = {
        "nodes": [
            {"name": "A", "ip": "127.0.0.1", "port": 5000},
            {"name": "B", "ip": "127.0.0.1", "port": 5001},
        ],
        "layout": {
            "nodes": {
                "A": {"x": 0, "y": 0, "width": 1, "height": 1},
                "B": {
                    "x": 1,
                    "y": 0,
                    "width": 3,
                    "height": 2,
                    "monitors": {
                        "logical": [["1", "2", "3", "4", "5", "6"]],
                        "physical": [["1", "2", "3"], ["4", "5", "6"]],
                    },
                },
            },
            "auto_switch": {
                "enabled": True,
                "cooldown_ms": 320,
                "return_guard_ms": 410,
            },
        },
    }

    ctx = build_runtime_context(config, override_name="A", config_path="config.json")

    diagnostics = build_layout_diagnostics(ctx)

    assert diagnostics["self_node"] == "A"
    assert diagnostics["layout"]["bounds"] == {"left": 0, "top": 0, "width": 4, "height": 2}
    assert diagnostics["layout"]["auto_switch"]["return_guard_ms"] == 410
    assert diagnostics["layout"]["overlaps"] == []

    nodes = {node["node_id"]: node for node in diagnostics["layout"]["nodes"]}
    assert nodes["A"]["node_adjacency"]["right"] == "B"
    assert nodes["B"]["node_adjacency"]["left"] == "A"
    assert nodes["B"]["logical_monitors"] == [["1", "2", "3", "4", "5", "6"]]
    assert nodes["B"]["physical_monitors"] == [["1", "2", "3"], ["4", "5", "6"]]
    assert nodes["B"]["display_adjacency"]["1"]["left"] == {"node_id": "A", "display_id": "1"}
    assert nodes["B"]["display_adjacency"]["4"]["up"] == {"node_id": "B", "display_id": "1"}
    assert nodes["A"]["edge_routes"]["1"]["right"].startswith("target-switch")
    assert nodes["B"]["edge_routes"]["1"]["left"].startswith("target-switch")


def test_format_layout_diagnostics_returns_json_text():
    config = {
        "nodes": [
            {"name": "A", "ip": "127.0.0.1", "port": 5000},
            {"name": "B", "ip": "127.0.0.1", "port": 5001},
        ]
    }

    ctx = build_runtime_context(config, override_name="A", config_path="config.json")

    text = format_layout_diagnostics(build_layout_diagnostics(ctx))

    assert '"self_node": "A"' in text
    assert '"layout"' in text
