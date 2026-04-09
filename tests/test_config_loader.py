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
        "coordinator": {"candidates": ["A"]},
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


def test_coordinator_candidate_not_in_nodes():
    cfg = _minimal()
    cfg["coordinator"] = {"candidates": ["Z"]}
    with pytest.raises(ValueError, match="defined in nodes"):
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
