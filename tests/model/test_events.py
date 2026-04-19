"""Tests for model/events.py — pure factory functions."""

import time

from model.events import (
    make_key_down_event,
    make_key_up_event,
    make_mouse_move_event,
    make_mouse_button_event,
    make_mouse_wheel_event,
    make_system_event,
)


def test_key_down_kind():
    ev = make_key_down_event("a")
    assert ev["kind"] == "key_down"


def test_key_up_kind():
    ev = make_key_up_event("a")
    assert ev["kind"] == "key_up"


def test_key_down_has_key():
    ev = make_key_down_event("z")
    assert ev["key"] == "z"


def test_key_down_char_extraction():
    """pynput Key objects with .char attribute should be unwrapped."""
    class FakeKey:
        char = "x"
    ev = make_key_down_event(FakeKey())
    assert ev["key"] == "x"


def test_key_down_special_key_stringified():
    """pynput special keys (no .char) fall back to str()."""
    class FakeSpecial:
        def __str__(self):
            return "Key.esc"
    ev = make_key_down_event(FakeSpecial())
    assert ev["key"] == "Key.esc"


def test_key_down_timestamp_present():
    ev = make_key_down_event("a")
    assert "ts" in ev
    assert isinstance(ev["ts"], float)


def test_timestamps_monotonic():
    t0 = time.time()
    ev = make_key_down_event("a")
    t1 = time.time()
    assert t0 <= ev["ts"] <= t1


def test_mouse_move_kind():
    ev = make_mouse_move_event(10, 20)
    assert ev["kind"] == "mouse_move"


def test_mouse_move_coords():
    ev = make_mouse_move_event(100, 200)
    assert ev["x"] == 100
    assert ev["y"] == 200


def test_mouse_button_kind():
    ev = make_mouse_button_event(0, 0, "left", True)
    assert ev["kind"] == "mouse_button"


def test_mouse_button_pressed_true():
    ev = make_mouse_button_event(0, 0, "left", True)
    assert ev["pressed"] is True


def test_mouse_button_pressed_false():
    ev = make_mouse_button_event(0, 0, "left", False)
    assert ev["pressed"] is False


def test_mouse_button_stringifies_button():
    class FakeBtn:
        def __str__(self):
            return "Button.left"
    ev = make_mouse_button_event(0, 0, FakeBtn(), True)
    assert ev["button"] == "Button.left"


def test_mouse_wheel_kind():
    ev = make_mouse_wheel_event(0, 0, 0, -3)
    assert ev["kind"] == "mouse_wheel"


def test_mouse_wheel_fields():
    ev = make_mouse_wheel_event(5, 6, 1, -2)
    assert ev["dx"] == 1
    assert ev["dy"] == -2


def test_system_event_kind():
    ev = make_system_event("shutdown")
    assert ev["kind"] == "system"


def test_system_event_message():
    ev = make_system_event("hello")
    assert ev["message"] == "hello"
