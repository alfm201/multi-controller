"""Tests for routing/topology.py."""

from routing.topology import should_connect


def test_controller_to_target():
    assert should_connect(("controller",), ("target",)) is True


def test_controller_to_coordinator():
    assert should_connect(("controller",), ("coordinator",)) is True


def test_target_to_coordinator():
    assert should_connect(("target",), ("coordinator",)) is True


def test_controller_to_controller_no_edge():
    assert should_connect(("controller",), ("controller",)) is False


def test_coordinator_to_coordinator_no_edge():
    assert should_connect(("coordinator",), ("coordinator",)) is False


def test_target_to_target_no_edge():
    assert should_connect(("target",), ("target",)) is False


def test_controller_target_to_controller_target():
    assert should_connect(("controller", "target"), ("controller", "target")) is True


def test_target_only_to_coordinator_only():
    assert should_connect(("target",), ("coordinator",)) is True
