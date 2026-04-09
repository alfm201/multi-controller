"""Tests for runtime/config_loader.py — validate_config."""

import pytest

from runtime.config_loader import validate_config

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _minimal():
    return {
        "nodes": [
            {"name": "A", "ip": "127.0.0.1", "port": 5000},
        ]
    }


def _two_nodes():
    return {
        "nodes": [
            {"name": "A", "ip": "127.0.0.1", "port": 5000},
            {"name": "B", "ip": "127.0.0.1", "port": 5001},
        ],
        "coordinator": {"candidates": ["A"]},
    }


# --------------------------------------------------------------------------- #
# valid configs
# --------------------------------------------------------------------------- #

def test_minimal_valid():
    validate_config(_minimal())  # must not raise


def test_with_coordinator_valid():
    validate_config(_two_nodes())


def test_roles_list_valid():
    cfg = _minimal()
    cfg["nodes"][0]["roles"] = ["controller", "target"]
    validate_config(cfg)


def test_no_coordinator_section_valid():
    validate_config({"nodes": [{"name": "X", "ip": "1.2.3.4", "port": 9000}]})


# --------------------------------------------------------------------------- #
# invalid: nodes
# --------------------------------------------------------------------------- #

def test_missing_nodes_key():
    with pytest.raises(ValueError, match="nodes"):
        validate_config({})


def test_empty_nodes():
    with pytest.raises(ValueError, match="nodes"):
        validate_config({"nodes": []})


def test_nodes_not_list():
    with pytest.raises(ValueError, match="nodes"):
        validate_config({"nodes": "A"})


def test_node_missing_name():
    with pytest.raises(ValueError, match="name"):
        validate_config({"nodes": [{"ip": "1.1.1.1", "port": 1}]})


def test_node_missing_ip():
    with pytest.raises(ValueError, match="ip"):
        validate_config({"nodes": [{"name": "A", "port": 1}]})


def test_node_missing_port():
    with pytest.raises(ValueError, match="port"):
        validate_config({"nodes": [{"name": "A", "ip": "1.1.1.1"}]})


def test_duplicate_names():
    cfg = {
        "nodes": [
            {"name": "A", "ip": "127.0.0.1", "port": 5000},
            {"name": "A", "ip": "127.0.0.1", "port": 5001},
        ]
    }
    with pytest.raises(ValueError, match="중복"):
        validate_config(cfg)


def test_roles_not_list():
    cfg = _minimal()
    cfg["nodes"][0]["roles"] = "controller"
    with pytest.raises(ValueError, match="roles"):
        validate_config(cfg)


# --------------------------------------------------------------------------- #
# invalid: coordinator
# --------------------------------------------------------------------------- #

def test_coordinator_candidate_not_in_nodes():
    cfg = _minimal()
    cfg["coordinator"] = {"candidates": ["Z"]}
    with pytest.raises(ValueError, match="nodes"):
        validate_config(cfg)


def test_coordinator_not_dict():
    cfg = _minimal()
    cfg["coordinator"] = "A"
    with pytest.raises(ValueError, match="coordinator"):
        validate_config(cfg)


def test_coordinator_candidates_not_list():
    cfg = _minimal()
    cfg["coordinator"] = {"candidates": "A"}
    with pytest.raises(ValueError, match="candidates"):
        validate_config(cfg)
