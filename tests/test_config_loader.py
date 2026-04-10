"""Tests for runtime/config_loader.py."""

import pytest

from runtime.config_loader import validate_config


def _minimal():
    return {"nodes": [{"name": "A", "ip": "127.0.0.1", "port": 5000}]}


def _two_nodes():
    return {
        "nodes": [
            {"name": "A", "ip": "127.0.0.1", "port": 5000},
            {"name": "B", "ip": "127.0.0.1", "port": 5001},
        ],
        "coordinator": {},
    }


def test_minimal_valid():
    validate_config(_minimal())


def test_with_coordinator_valid():
    validate_config(_two_nodes())


def test_roles_list_valid():
    cfg = _minimal()
    cfg["nodes"][0]["roles"] = ["controller", "target"]
    validate_config(cfg)


def test_missing_nodes_key():
    with pytest.raises(ValueError, match="nodes"):
        validate_config({})


def test_duplicate_names():
    cfg = {
        "nodes": [
            {"name": "A", "ip": "127.0.0.1", "port": 5000},
            {"name": "A", "ip": "127.0.0.1", "port": 5001},
        ]
    }
    with pytest.raises(ValueError, match="duplicated"):
        validate_config(cfg)


def test_roles_not_list():
    cfg = _minimal()
    cfg["nodes"][0]["roles"] = "controller"
    with pytest.raises(ValueError, match="roles"):
        validate_config(cfg)


def test_roles_unknown():
    cfg = _minimal()
    cfg["nodes"][0]["roles"] = ["invalid-role"]
    with pytest.raises(ValueError, match="unknown roles"):
        validate_config(cfg)


def test_coordinator_section_may_be_empty_object():
    cfg = _minimal()
    cfg["coordinator"] = {}
    validate_config(cfg)


def test_coordinator_not_dict():
    cfg = _minimal()
    cfg["coordinator"] = "bad"
    with pytest.raises(ValueError, match="coordinator"):
        validate_config(cfg)


def test_default_roles_not_list():
    cfg = _minimal()
    cfg["default_roles"] = "controller"
    with pytest.raises(ValueError, match="default_roles"):
        validate_config(cfg)


def test_port_must_be_positive_int():
    cfg = _minimal()
    cfg["nodes"][0]["port"] = 0
    with pytest.raises(ValueError, match="positive"):
        validate_config(cfg)


def test_layout_section_may_define_positions_and_auto_switch():
    cfg = _two_nodes()
    cfg["layout"] = {
        "nodes": {
            "A": {"x": 0, "y": 0},
            "B": {"x": 1, "y": 0, "width": 1, "height": 1},
        },
        "auto_switch": {
            "enabled": True,
            "edge_threshold": 0.03,
            "warp_margin": 0.05,
            "cooldown_ms": 300,
            "return_guard_ms": 450,
            "anchor_dead_zone": 0.1,
        },
    }

    validate_config(cfg)


def test_layout_rejects_unknown_node_id():
    cfg = _minimal()
    cfg["layout"] = {"nodes": {"B": {"x": 1, "y": 0}}}

    with pytest.raises(ValueError, match="unknown node"):
        validate_config(cfg)


def test_layout_auto_switch_threshold_range_checked():
    cfg = _minimal()
    cfg["layout"] = {"auto_switch": {"edge_threshold": 0.5}}

    with pytest.raises(ValueError, match="edge_threshold"):
        validate_config(cfg)


def test_layout_monitor_topology_allows_logical_and_physical_grids():
    cfg = _minimal()
    cfg["layout"] = {
        "nodes": {
            "A": {
                "monitors": {
                    "logical": [["1", "2", "3", "4", "5", "6"]],
                    "physical": [["1", "2", "3"], ["4", "5", "6"]],
                }
            }
        }
    }

    validate_config(cfg)


def test_layout_monitor_topology_requires_matching_ids():
    cfg = _minimal()
    cfg["layout"] = {
        "nodes": {
            "A": {
                "monitors": {
                    "logical": [["1", "2"]],
                    "physical": [["1", "3"]],
                }
            }
        }
    }

    with pytest.raises(ValueError, match="ids must match"):
        validate_config(cfg)
