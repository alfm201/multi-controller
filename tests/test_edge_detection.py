"""Tests for routing/edge_detection.py."""

from routing.edge_detection import axis_ratio, detect_edge_press


def test_detect_edge_press_returns_none_for_interior_pointer():
    assert detect_edge_press((0, 0, 1919, 1079), {"x": 960, "y": 540}) is None


def test_detect_edge_press_prefers_nearest_edge():
    edge_press = detect_edge_press((0, 0, 1919, 1079), {"x": 1925, "y": 200})

    assert edge_press is not None
    assert edge_press.direction == "right"
    assert 0.0 <= edge_press.cross_axis_ratio <= 1.0


def test_detect_edge_press_handles_corner_press():
    edge_press = detect_edge_press((0, 0, 1919, 1079), {"x": -4, "y": -2})

    assert edge_press is not None
    assert edge_press.direction in {"left", "up"}


def test_axis_ratio_clamps_value_into_unit_interval():
    assert axis_ratio(-5, 0, 10) == 0.0
    assert axis_ratio(15, 0, 10) == 1.0
