"""Tests for capture/input_capture.py synthetic suppression behavior."""

import queue

from capture.hotkey import HotkeyMatcher
from capture.input_capture import InputCapture, MoveProcessingResult
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


def test_move_processor_can_consume_and_request_local_block():
    q = queue.Queue()
    capture = InputCapture(
        q,
        synthetic_guard=SyntheticInputGuard(),
        move_processor=lambda event: MoveProcessingResult(None, True),
    )
    capture.running = True

    blocked = capture.on_move(100, 200)

    assert blocked is True
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


def test_local_activity_callback_fires_for_real_input_only():
    q = queue.Queue()
    guard = SyntheticInputGuard()
    activity = []
    capture = InputCapture(
        q,
        synthetic_guard=guard,
        local_activity_callback=lambda: activity.append("local"),
    )
    capture.running = True

    capture.on_move(10, 20)
    guard.record_mouse_move(30, 40)
    capture.on_move(31, 39)

    assert activity == ["local"]


def test_local_activity_callback_is_skipped_for_recent_remote_input():
    q = queue.Queue()
    activity = []

    class SinkLike:
        def __init__(self):
            self.authorized = "B"
            self._recent = True

        def get_authorized_controller(self):
            return self.authorized

        def remote_input_recent(self):
            return self._recent

    sink = SinkLike()

    def callback():
        controller_id = sink.get_authorized_controller()
        if not controller_id:
            return
        if sink.remote_input_recent():
            return
        activity.append("override")

    capture = InputCapture(
        q,
        synthetic_guard=SyntheticInputGuard(),
        local_activity_callback=callback,
    )
    capture.running = True

    capture.on_move(10, 20)
    assert activity == []

    sink._recent = False
    capture.on_move(15, 25)
    assert activity == ["override"]


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


def test_input_capture_prefers_supplied_low_level_hooks_on_windows(monkeypatch):
    q = queue.Queue()
    started = []
    stopped = []
    joined = []

    class DummyHook:
        def __init__(self, _receiver, *, should_block=None):
            self.should_block = should_block

        def start(self):
            started.append(self.should_block)

        def stop(self):
            stopped.append(True)

        def join(self, timeout=None):
            joined.append(timeout)

    monkeypatch.setattr("capture.input_capture.sys.platform", "win32")

    from pynput import keyboard, mouse

    monkeypatch.setattr(keyboard, "Listener", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("keyboard listener should not start")))
    monkeypatch.setattr(mouse, "Listener", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("mouse listener should not start")))

    capture = InputCapture(
        q,
        synthetic_guard=SyntheticInputGuard(),
        keyboard_hook_factory=DummyHook,
        mouse_hook_factory=DummyHook,
        keyboard_block_predicate=lambda kind, event: False,
        mouse_block_predicate=lambda kind, event: True,
    )

    capture.start()
    capture.stop()
    capture.join()

    assert len(started) == 2
    assert started[0]("key_down", {}) is False
    assert started[1]("mouse_move", {}) is True
    assert len(stopped) == 2
    assert len(joined) == 2


def test_input_capture_falls_back_to_pynput_listeners_when_hook_start_fails(monkeypatch):
    q = queue.Queue()
    started = []
    stopped = []
    joined = []
    keyboard_callbacks = {}
    mouse_callbacks = {}

    class FailingHook:
        def __init__(self, _receiver, *, should_block=None):
            self.should_block = should_block

        def start(self):
            raise RuntimeError("hook failed")

    class DummyListener:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            started.append(True)

        def stop(self):
            stopped.append(True)

        def join(self):
            joined.append(True)

    monkeypatch.setattr("capture.input_capture.sys.platform", "win32")

    from pynput import keyboard, mouse

    def _keyboard_listener(**kwargs):
        keyboard_callbacks.update(kwargs)
        return DummyListener(**kwargs)

    def _mouse_listener(**kwargs):
        mouse_callbacks.update(kwargs)
        return DummyListener(**kwargs)

    monkeypatch.setattr(keyboard, "Listener", _keyboard_listener)
    monkeypatch.setattr(mouse, "Listener", _mouse_listener)

    capture = InputCapture(
        q,
        synthetic_guard=SyntheticInputGuard(),
        keyboard_hook_factory=FailingHook,
        mouse_hook_factory=FailingHook,
    )

    capture.start()
    capture.stop()
    capture.join()

    assert len(started) == 2
    assert len(stopped) == 2
    assert len(joined) == 2
    assert keyboard_callbacks["on_press"]("a") is None
    assert keyboard_callbacks["on_release"]("a") is None
    assert mouse_callbacks["on_move"](10, 20) is None
    assert mouse_callbacks["on_click"](10, 20, "Button.left", True) is None
    assert mouse_callbacks["on_scroll"](10, 20, 0, 1) is None
