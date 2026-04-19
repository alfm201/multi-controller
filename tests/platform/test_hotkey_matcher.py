"""Tests for platform/capture/hotkey.py::HotkeyMatcher — pure string-driven logic."""

from platform.capture.hotkey import HotkeyMatcher


def _mk(callback=None):
    calls = []
    cb = callback or (lambda: calls.append("fired"))
    m = HotkeyMatcher(
        modifier_groups=[
            ("Key.ctrl", "Key.ctrl_l", "Key.ctrl_r"),
            ("Key.shift", "Key.shift_l", "Key.shift_r"),
        ],
        trigger="Key.tab",
        callback=cb,
        name="cycle",
    )
    return m, calls


def test_no_match_without_modifiers():
    m, calls = _mk()
    assert m.on_press("Key.tab") is False
    assert calls == []


def test_match_with_ctrl_shift_then_tab():
    m, calls = _mk()
    m.on_press("Key.ctrl_l")
    m.on_press("Key.shift_l")
    assert m.on_press("Key.tab") is True
    assert calls == ["fired"]


def test_match_with_alt_modifier_key_names():
    """Different L/R/base variants all count as the same modifier."""
    m, calls = _mk()
    m.on_press("Key.ctrl_r")
    m.on_press("Key.shift")
    assert m.on_press("Key.tab") is True
    assert calls == ["fired"]


def test_no_match_when_only_one_modifier():
    m, calls = _mk()
    m.on_press("Key.ctrl_l")
    assert m.on_press("Key.tab") is False
    assert calls == []


def test_no_match_when_modifier_released_before_trigger():
    m, calls = _mk()
    m.on_press("Key.ctrl_l")
    m.on_press("Key.shift_l")
    m.on_release("Key.ctrl_l")
    assert m.on_press("Key.tab") is False
    assert calls == []


def test_trigger_release_after_match_is_consumed():
    m, _ = _mk()
    m.on_press("Key.ctrl_l")
    m.on_press("Key.shift_l")
    m.on_press("Key.tab")
    assert m.on_release("Key.tab") is True


def test_trigger_release_without_prior_match_is_not_consumed():
    m, _ = _mk()
    m.on_press("Key.tab")
    assert m.on_release("Key.tab") is False


def test_modifier_release_is_never_consumed():
    m, _ = _mk()
    m.on_press("Key.ctrl_l")
    m.on_press("Key.shift_l")
    m.on_press("Key.tab")
    assert m.on_release("Key.ctrl_l") is False
    assert m.on_release("Key.shift_l") is False


def test_double_trigger_requires_fresh_press():
    m, calls = _mk()
    m.on_press("Key.ctrl_l")
    m.on_press("Key.shift_l")
    assert m.on_press("Key.tab") is True
    m.on_release("Key.tab")
    assert m.on_press("Key.tab") is True
    assert calls == ["fired", "fired"]


def test_unrelated_keys_passthrough():
    m, calls = _mk()
    assert m.on_press("a") is False
    assert m.on_press("Key.enter") is False
    assert calls == []


def test_none_key_ignored_press():
    m, _ = _mk()
    assert m.on_press(None) is False
    assert m.on_press("") is False


def test_none_key_ignored_release():
    m, _ = _mk()
    assert m.on_release(None) is False
    assert m.on_release("") is False


def test_callback_exception_does_not_propagate():
    def boom():
        raise RuntimeError("kaboom")

    m = HotkeyMatcher(
        modifier_groups=[("Key.ctrl",)],
        trigger="Key.f1",
        callback=boom,
    )
    m.on_press("Key.ctrl")
    # should still report match and not raise
    assert m.on_press("Key.f1") is True


def test_match_consumed_flag_resets_between_presses():
    m, _ = _mk()
    m.on_press("Key.ctrl_l")
    m.on_press("Key.shift_l")
    m.on_press("Key.tab")
    m.on_release("Key.tab")
    # Second release with no match in between must not be consumed
    assert m.on_release("Key.tab") is False
