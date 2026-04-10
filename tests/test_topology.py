"""Tests for routing/topology.py."""

from routing.topology import should_connect


def test_all_peers_connect_regardless_of_roles():
    assert should_connect(("controller",), ("target",)) is True
    assert should_connect(("target",), ("controller",)) is True
    assert should_connect(("controller",), ("controller",)) is True
    assert should_connect(("target",), ("target",)) is True
    assert should_connect((), ()) is True
