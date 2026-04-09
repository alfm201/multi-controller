"""Tests for runtime/context.py — NodeInfo and RuntimeContext."""

import pytest

from runtime.context import NodeInfo, RuntimeContext


# --------------------------------------------------------------------------- #
# NodeInfo.from_dict
# --------------------------------------------------------------------------- #

def test_from_dict_basic():
    ni = NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000})
    assert ni.name == "A"
    assert ni.ip == "127.0.0.1"
    assert ni.port == 5000


def test_from_dict_port_coerced_to_int():
    ni = NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": "5000"})
    assert isinstance(ni.port, int)


def test_from_dict_roles_present():
    ni = NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000, "roles": ["controller"]})
    assert ni.has_role("controller")
    assert not ni.has_role("target")


def test_from_dict_roles_absent_defaults():
    ni = NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000})
    # default: both controller and target
    assert ni.has_role("controller")
    assert ni.has_role("target")


def test_from_dict_roles_null_defaults():
    ni = NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000, "roles": None})
    assert ni.has_role("controller")
    assert ni.has_role("target")


def test_from_dict_default_roles_applied():
    """When node has no roles, default_roles parameter is used as fallback."""
    ni = NodeInfo.from_dict(
        {"name": "A", "ip": "127.0.0.1", "port": 5000},
        default_roles=["target"],
    )
    assert ni.has_role("target")
    assert not ni.has_role("controller")


def test_from_dict_node_roles_override_default():
    """Explicit node roles take priority over default_roles."""
    ni = NodeInfo.from_dict(
        {"name": "A", "ip": "127.0.0.1", "port": 5000, "roles": ["controller"]},
        default_roles=["target"],
    )
    assert ni.has_role("controller")
    assert not ni.has_role("target")


def test_node_id_equals_name():
    ni = NodeInfo.from_dict({"name": "X", "ip": "1.2.3.4", "port": 9})
    assert ni.node_id == "X"


def test_label():
    ni = NodeInfo.from_dict({"name": "A", "ip": "192.168.1.1", "port": 5000})
    assert ni.label() == "A(192.168.1.1:5000)"


# --------------------------------------------------------------------------- #
# RuntimeContext.peers
# --------------------------------------------------------------------------- #

def _make_nodes(*names):
    return [
        NodeInfo.from_dict({"name": n, "ip": "127.0.0.1", "port": 5000 + i})
        for i, n in enumerate(names)
    ]


def test_peers_excludes_self():
    nodes = _make_nodes("A", "B", "C")
    ctx = RuntimeContext(self_node=nodes[0], nodes=nodes)
    peer_ids = [p.node_id for p in ctx.peers]
    assert "A" not in peer_ids
    assert "B" in peer_ids
    assert "C" in peer_ids


def test_peers_single_node():
    nodes = _make_nodes("A")
    ctx = RuntimeContext(self_node=nodes[0], nodes=nodes)
    assert ctx.peers == []


def test_get_node_found():
    nodes = _make_nodes("A", "B")
    ctx = RuntimeContext(self_node=nodes[0], nodes=nodes)
    assert ctx.get_node("B") is nodes[1]


def test_get_node_not_found():
    nodes = _make_nodes("A")
    ctx = RuntimeContext(self_node=nodes[0], nodes=nodes)
    assert ctx.get_node("Z") is None
