"""Tests for control/routing/edge_detection.py."""

from control.routing.edge_detection import axis_ratio, detect_edge_approach, detect_edge_crossing, detect_edge_press


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


def test_detect_edge_crossing_detects_fast_right_exit():
    edge_press = detect_edge_crossing(
        (0, 0, 1919, 1079),
        {"x": 1916, "y": 540},
        {"x": 1925, "y": 540},
    )

    assert edge_press is not None
    assert edge_press.direction == "right"
    assert edge_press.cross_axis_ratio == 540 / 1079


def test_detect_edge_crossing_returns_none_when_segment_stays_inside():
    edge_press = detect_edge_crossing(
        (0, 0, 1919, 1079),
        {"x": 500, "y": 200},
        {"x": 1200, "y": 300},
    )

    assert edge_press is None


def test_detect_edge_crossing_prefers_deterministic_corner_tie_break():
    edge_press = detect_edge_crossing(
        (0, 0, 1919, 1079),
        {"x": 1918, "y": 1},
        {"x": 1925, "y": -6},
    )

    assert edge_press is not None
    assert edge_press.direction == "right"
    assert edge_press.cross_axis_ratio == 0.0


def test_detect_edge_approach_detects_slow_right_entry_before_exit():
    edge_press = detect_edge_approach(
        (0, 0, 1919, 1079),
        {"x": 1914, "y": 540},
        {"x": 1918, "y": 540},
        2,
    )

    assert edge_press is not None
    assert edge_press.direction == "right"
    assert edge_press.cross_axis_ratio == 540 / 1079


def test_detect_edge_approach_returns_none_for_large_jump_outside_band():
    edge_press = detect_edge_approach(
        (0, 0, 1919, 1079),
        {"x": 1800, "y": 540},
        {"x": 1916, "y": 540},
        2,
    )

    assert edge_press is None


def test_axis_ratio_clamps_value_into_unit_interval():
    assert axis_ratio(-5, 0, 10) == 0.0
    assert axis_ratio(15, 0, 10) == 1.0
