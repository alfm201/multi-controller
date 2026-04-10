"""Tests for runtime/display.py."""

from runtime.display import (
    ScreenBounds,
    denormalize_position,
    enable_best_effort_dpi_awareness,
    get_dpi_awareness_mode,
    enrich_pointer_event,
    normalize_position,
    resolve_pointer_position,
)


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


def test_normalize_position_supports_virtual_desktop_origin():
    bounds = ScreenBounds(left=-1920, top=0, width=3840, height=1080)
    norm_x, norm_y = normalize_position(-960, 540, bounds)
    assert round(norm_x, 3) == round(960 / 3839, 3)
    assert round(norm_y, 3) == round(540 / 1079, 3)


def test_denormalize_position_restores_virtual_desktop_coordinates():
    bounds = ScreenBounds(left=-1920, top=0, width=3840, height=1080)
    x, y = denormalize_position(0.25, 0.5, bounds)
    assert x == -1920 + round(0.25 * 3839)
    assert y == round(0.5 * 1079)


def test_resolve_pointer_position_uses_virtual_bounds_tuple():
    event = {"kind": "mouse_move", "x": 1, "y": 2, "x_norm": 0.25, "y_norm": 0.5}
    assert resolve_pointer_position(event, (-1920, 0, 3840, 1080)) == (
        -1920 + round(0.25 * 3839),
        round(0.5 * 1079),
    )


class _FakeUser32:
    def __init__(self, dpi_context_result=None, dpi_aware_result=None):
        self._dpi_context_result = dpi_context_result
        self._dpi_aware_result = dpi_aware_result

    def SetProcessDpiAwarenessContext(self, value):
        return self._dpi_context_result

    def SetProcessDPIAware(self):
        return self._dpi_aware_result


class _FakeShcore:
    def __init__(self, result=None):
        self._result = result

    def SetProcessDpiAwareness(self, value):
        return self._result


def test_enable_best_effort_dpi_awareness_prefers_per_monitor_v2():
    user32 = _FakeUser32(dpi_context_result=True, dpi_aware_result=False)
    shcore = _FakeShcore(result=5)
    assert enable_best_effort_dpi_awareness(user32=user32, shcore=shcore) is True


def test_enable_best_effort_dpi_awareness_falls_back_to_shcore():
    user32 = _FakeUser32(dpi_context_result=False, dpi_aware_result=False)
    shcore = _FakeShcore(result=0)
    assert enable_best_effort_dpi_awareness(user32=user32, shcore=shcore) is True


def test_enable_best_effort_dpi_awareness_falls_back_to_legacy_user32():
    user32 = _FakeUser32(dpi_context_result=False, dpi_aware_result=True)
    shcore = _FakeShcore(result=5)
    assert enable_best_effort_dpi_awareness(user32=user32, shcore=shcore) is True


def test_get_dpi_awareness_mode_returns_last_selected_mode():
    user32 = _FakeUser32(dpi_context_result=False, dpi_aware_result=True)
    shcore = _FakeShcore(result=5)
    enable_best_effort_dpi_awareness(user32=user32, shcore=shcore)
    assert get_dpi_awareness_mode() in {"system", "per-monitor", "per-monitor-v2"}
