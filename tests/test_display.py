"""Tests for runtime/display.py."""

from runtime.display import denormalize_position, enrich_pointer_event, normalize_position, resolve_pointer_position


def test_normalize_position_uses_screen_bounds():
    norm_x, norm_y = normalize_position(960, 540, 1920, 1080)
    assert round(norm_x, 3) == round(960 / 1919, 3)
    assert round(norm_y, 3) == round(540 / 1079, 3)


def test_denormalize_position_restores_absolute_coordinates():
    x, y = denormalize_position(0.5, 0.5, 1920, 1080)
    assert x == round(0.5 * 1919)
    assert y == round(0.5 * 1079)


def test_enrich_pointer_event_adds_normalized_coordinates():
    event = enrich_pointer_event({"kind": "mouse_move", "x": 100, "y": 200}, 1000, 500)
    assert event["x"] == 100
    assert event["y"] == 200
    assert 0.0 <= event["x_norm"] <= 1.0
    assert 0.0 <= event["y_norm"] <= 1.0


def test_resolve_pointer_position_prefers_normalized_coordinates():
    event = {"kind": "mouse_move", "x": 1, "y": 2, "x_norm": 0.5, "y_norm": 0.25}
    assert resolve_pointer_position(event, 200, 100) == (100, 25)


def test_resolve_pointer_position_falls_back_to_raw_coordinates():
    event = {"kind": "mouse_move", "x": 12, "y": 34}
    assert resolve_pointer_position(event, 200, 100) == (12, 34)
