"""Tests for capture/input_capture.py synthetic suppression behavior."""

import queue

from capture.hotkey import HotkeyMatcher
from capture.input_capture import InputCapture
from runtime.display import ScreenBounds
from runtime.synthetic_input import SyntheticInputGuard


def _drain(q):
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


def test_synthetic_key_press_is_not_enqueued():
    q = queue.Queue()
    guard = SyntheticInputGuard()
    capture = InputCapture(q, synthetic_guard=guard)
    capture.running = True

    guard.record_key("a", down=True)
    capture.on_key_press("a")

    assert _drain(q) == []


def test_synthetic_key_release_is_not_enqueued():
    q = queue.Queue()
    guard = SyntheticInputGuard()
    capture = InputCapture(q, synthetic_guard=guard)
    capture.running = True

    guard.record_key("Key.ctrl_l", down=False)
    capture.on_key_release("Key.ctrl_l")

    assert _drain(q) == []


def test_synthetic_mouse_move_is_not_enqueued():
    q = queue.Queue()
    guard = SyntheticInputGuard()
    capture = InputCapture(q, synthetic_guard=guard)
    capture.running = True

    guard.record_mouse_move(100, 200)
    capture.on_move(101, 198)

    assert _drain(q) == []


def test_move_processor_can_consume_mouse_move_before_queueing():
    q = queue.Queue()
    seen = []
    capture = InputCapture(
        q,
        synthetic_guard=SyntheticInputGuard(),
        move_processor=lambda event: seen.append((event["x"], event["y"])) or None,
    )
    capture.running = True

    capture.on_move(100, 200)

    assert seen == [(100, 200)]
    assert _drain(q) == []


def test_click_can_refresh_pointer_state_before_queueing():
    q = queue.Queue()
    refreshed = []
    capture = InputCapture(
        q,
        synthetic_guard=SyntheticInputGuard(),
        pointer_state_refresher=lambda: refreshed.append("refresh"),
    )
    capture.running = True

    capture.on_click(100, 200, "Button.left", True)

    assert refreshed == ["refresh"]
    events = _drain(q)
    assert len(events) == 1
    assert events[0]["kind"] == "mouse_button"


def test_real_key_press_still_enqueues_event():
    q = queue.Queue()
    capture = InputCapture(q, synthetic_guard=SyntheticInputGuard())
    capture.running = True

    capture.on_key_press("a")

    events = _drain(q)
    assert len(events) == 1
    assert events[0]["kind"] == "key_down"
    assert events[0]["key"] == "a"


def test_pointer_events_use_latest_screen_bounds_provider_value():
    q = queue.Queue()
    bounds = [
        ScreenBounds(left=0, top=0, width=100, height=100),
        ScreenBounds(left=-100, top=0, width=200, height=100),
    ]
    capture = InputCapture(
        q,
        synthetic_guard=SyntheticInputGuard(),
        screen_bounds_provider=lambda: bounds.pop(0),
    )
    capture.running = True

    capture.on_move(50, 50)
    capture.on_move(-50, 50)

    events = _drain(q)
    assert round(events[0]["x_norm"], 3) == round(50 / 99, 3)
    assert round(events[1]["x_norm"], 3) == round(50 / 199, 3)


def test_plain_escape_release_no_longer_stops_capture():
    q = queue.Queue()
    capture = InputCapture(q, synthetic_guard=SyntheticInputGuard())
    capture.running = True

    capture.on_key_release("Key.esc")

    events = _drain(q)
    assert capture.running is True
    assert len(events) == 1
    assert events[0]["kind"] == "key_up"
    assert events[0]["key"] == "Key.esc"


def test_ctrl_alt_escape_hotkey_can_quit_app_without_forwarding_keys():
    q = queue.Queue()

    def quit_app():
        capture.put_event({"kind": "system", "message": "Ctrl+Alt+Esc input detected, quitting app"})
        capture.stop()

    capture = InputCapture(
        q,
        hotkey_matchers=[
            HotkeyMatcher(
                modifier_groups=[
                    ("Key.ctrl", "Key.ctrl_l", "Key.ctrl_r"),
                    ("Key.alt", "Key.alt_l", "Key.alt_r"),
                ],
                trigger="Key.esc",
                callback=quit_app,
                name="quit-application",
            )
        ],
        synthetic_guard=SyntheticInputGuard(),
    )
    capture.running = True

    capture.on_key_press("Key.ctrl_l")
    capture.on_key_press("Key.alt_l")
    capture.on_key_press("Key.esc")

    events = _drain(q)
    assert capture.running is False
    assert len(events) == 1
    assert events[0]["kind"] == "system"
    assert events[0]["message"] == "Ctrl+Alt+Esc input detected, quitting app"


def test_ctrl_alt_q_hotkey_works_even_if_layout_specific_char_is_reported():
    q = queue.Queue()
    fired = []
    capture = InputCapture(
        q,
        hotkey_matchers=[
            HotkeyMatcher(
                modifier_groups=[
                    ("Key.ctrl", "Key.ctrl_l", "Key.ctrl_r"),
                    ("Key.alt", "Key.alt_l", "Key.alt_r"),
                ],
                trigger="q",
                callback=lambda: fired.append("prev"),
                name="cycle-target-prev",
            )
        ],
        synthetic_guard=SyntheticInputGuard(),
    )
    capture.running = True

    class FakeKey:
        def __init__(self, vk, char):
            self.vk = vk
            self.char = char

    capture.on_key_press("Key.ctrl_l")
    capture.on_key_press("Key.alt_l")
    capture.on_key_press(FakeKey(0x51, "ㅂ"))

    assert fired == ["prev"]
    assert _drain(q) == []
