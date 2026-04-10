"""Tests for capture/input_capture.py synthetic suppression behavior."""

import queue

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
