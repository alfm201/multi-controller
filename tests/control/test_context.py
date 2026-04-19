"""Tests for control/state/context.py — NodeInfo and RuntimeContext."""

from control.state.context import NodeInfo, RuntimeContext, build_runtime_context


# --------------------------------------------------------------------------- #
# NodeInfo.from_dict
# --------------------------------------------------------------------------- #

def test_from_dict_basic():
    ni = NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000})
    assert ni.name == "A"
    assert ni.node_id == "A"
    assert ni.ip == "127.0.0.1"
    assert ni.port == 5000
    assert ni.priority == 0


def test_from_dict_port_coerced_to_int():
    ni = NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": "5000"})
    assert isinstance(ni.port, int)


def test_from_dict_legacy_roles_are_ignored():
    ni = NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000, "roles": ["controller"]})
    assert ni.has_role("controller")
    assert ni.has_role("target")


def test_from_dict_roles_absent_defaults():
    ni = NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000})
    # default: both controller and target
    assert ni.has_role("controller")
    assert ni.has_role("target")


def test_from_dict_roles_null_defaults():
    ni = NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000, "roles": None})
    assert ni.has_role("controller")
    assert ni.has_role("target")


def test_from_dict_default_roles_are_ignored():
    ni = NodeInfo.from_dict(
        {"name": "A", "ip": "127.0.0.1", "port": 5000},
        default_roles=["target"],
    )
    assert ni.has_role("controller")
    assert ni.has_role("target")


def test_from_dict_node_roles_do_not_override_dual_capability():
    ni = NodeInfo.from_dict(
        {"name": "A", "ip": "127.0.0.1", "port": 5000, "roles": ["controller"]},
        default_roles=["target"],
    )
    assert ni.has_role("controller")
    assert ni.has_role("target")


def test_node_id_defaults_to_name():
    ni = NodeInfo.from_dict({"name": "X", "ip": "1.2.3.4", "port": 9})
    assert ni.node_id == "X"


def test_node_id_uses_explicit_field():
    ni = NodeInfo.from_dict({"node_id": "node-x", "name": "회의실 PC", "ip": "1.2.3.4", "port": 9})
    assert ni.node_id == "node-x"
    assert ni.name == "회의실 PC"


def test_priority_uses_explicit_field():
    ni = NodeInfo.from_dict({"node_id": "node-x", "name": "회의실 PC", "ip": "1.2.3.4", "port": 9, "priority": 7})
    assert ni.priority == 7


def test_priority_defaults_to_last_when_null():
    ni = NodeInfo.from_dict({"node_id": "node-x", "name": "회의실 PC", "ip": "1.2.3.4", "port": 9, "priority": None})
    assert ni.priority == 0


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


def test_replace_nodes_refreshes_self_node():
    nodes = _make_nodes("A", "B")
    ctx = RuntimeContext(self_node=nodes[0], nodes=nodes)

    updated_self = NodeInfo.from_dict({"name": "A", "ip": "192.168.0.10", "port": 5000})
    updated_peer = NodeInfo.from_dict({"name": "B", "ip": "127.0.0.2", "port": 5001})

    ctx.replace_nodes([updated_self, updated_peer])

    assert ctx.self_node is updated_self
    assert ctx.self_node.ip == "192.168.0.10"


def test_build_runtime_context_includes_layout_defaults():
    config = {
        "nodes": [
            {"name": "A", "ip": "127.0.0.1", "port": 5000},
            {"name": "B", "ip": "127.0.0.1", "port": 5001},
        ]
    }

    ctx = build_runtime_context(config, override_name="A", config_path="config.json")

    assert ctx.layout is not None
    assert ctx.layout.get_node("A").x == 0
    assert ctx.layout.get_node("B").x == 1


def test_build_runtime_context_uses_explicit_node_ids():
    config = {
        "nodes": [
            {"node_id": "node-a", "name": "A", "ip": "127.0.0.1", "port": 5000},
            {"node_id": "node-b", "name": "B", "ip": "127.0.0.1", "port": 5001},
        ],
        "layout": {
            "nodes": {
                "node-a": {"x": 0, "y": 0},
                "node-b": {"x": 1, "y": 0},
            }
        },
    }

    ctx = build_runtime_context(config, override_name="node-a", config_path="config.json")

    assert ctx.self_node.node_id == "node-a"
    assert ctx.self_node.name == "A"
    assert ctx.layout.get_node("node-b").x == 1
