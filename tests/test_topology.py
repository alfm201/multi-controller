"""Tests for routing/topology.py — should_connect truth table."""

import pytest

from routing.topology import should_connect


# --------------------------------------------------------------------------- #
# edges that MUST exist
# --------------------------------------------------------------------------- #

def test_controller_to_target():
    assert should_connect(("controller",), ("target",)) is True


def test_target_to_controller():
    """Symmetric: target side computes same result."""
    assert should_connect(("target",), ("controller",)) is True


def test_controller_to_coordinator():
    assert should_connect(("controller",), ("coordinator",)) is True


def test_coordinator_to_controller():
    assert should_connect(("coordinator",), ("controller",)) is True


# --------------------------------------------------------------------------- #
# edges that must NOT exist
# --------------------------------------------------------------------------- #

def test_target_to_coordinator_no_edge():
    assert should_connect(("target",), ("coordinator",)) is False


def test_coordinator_to_target_no_edge():
    assert should_connect(("coordinator",), ("target",)) is False


def test_controller_to_controller_no_edge():
    assert should_connect(("controller",), ("controller",)) is False


def test_coordinator_to_coordinator_no_edge():
    assert should_connect(("coordinator",), ("coordinator",)) is False


def test_target_to_target_no_edge():
    assert should_connect(("target",), ("target",)) is False


# --------------------------------------------------------------------------- #
# multi-role nodes
# --------------------------------------------------------------------------- #

def test_controller_target_to_controller_target():
    """Default role pair: both nodes are controller+target — should connect."""
    assert should_connect(("controller", "target"), ("controller", "target")) is True


def test_controller_target_to_coordinator():
    """controller+target node needs to reach coordinator."""
    assert should_connect(("controller", "target"), ("coordinator",)) is True


def test_coordinator_to_controller_target():
    assert should_connect(("coordinator",), ("controller", "target")) is True


def test_target_only_to_coordinator_only_no_edge():
    assert should_connect(("target",), ("coordinator",)) is False
