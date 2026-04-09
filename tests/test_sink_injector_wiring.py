"""Tests for routing/sink.py — injector delegation.

A recording mock injector captures every call. We verify that InputSink
forwards each event kind to the correct injector method, and that
release_peer replays a release for every key/button still marked pressed.
"""

from injection.os_injector import OSInjector
from routing.sink import InputSink


class RecordingInjector(OSInjector):
    def __init__(self):
        self.calls = []

    def inject_key(self, key_str, down):
        self.calls.append(("key", key_str, down))

    def inject_mouse_move(self, x, y):
        self.calls.append(("move", x, y))

    def inject_mouse_button(self, button_str, x, y, down):
        self.calls.append(("btn", button_str, x, y, down))

    def inject_mouse_wheel(self, x, y, dx, dy):
        self.calls.append(("wheel", x, y, dx, dy))


def test_key_down_forwarded():
    inj = RecordingInjector()
    sink = InputSink(injector=inj)
    sink.handle("A", {"kind": "key_down", "key": "a"})
    assert inj.calls == [("key", "a", True)]


def test_key_up_forwarded():
    inj = RecordingInjector()
    sink = InputSink(injector=inj)
    sink.handle("A", {"kind": "key_up", "key": "a"})
    assert inj.calls == [("key", "a", False)]


def test_mouse_move_forwarded():
    inj = RecordingInjector()
    sink = InputSink(injector=inj)
    sink.handle("A", {"kind": "mouse_move", "x": 100, "y": 200})
    assert inj.calls == [("move", 100, 200)]


def test_mouse_button_down_forwarded():
    inj = RecordingInjector()
    sink = InputSink(injector=inj)
    sink.handle(
        "A",
        {
            "kind": "mouse_button",
            "button": "Button.left",
            "x": 5,
            "y": 6,
            "pressed": True,
        },
    )
    assert inj.calls == [("btn", "Button.left", 5, 6, True)]


def test_mouse_button_up_forwarded():
    inj = RecordingInjector()
    sink = InputSink(injector=inj)
    sink.handle(
        "A",
        {
            "kind": "mouse_button",
            "button": "Button.left",
            "x": 5,
            "y": 6,
            "pressed": False,
        },
    )
    assert inj.calls == [("btn", "Button.left", 5, 6, False)]


def test_mouse_wheel_forwarded():
    inj = RecordingInjector()
    sink = InputSink(injector=inj)
    sink.handle(
        "A",
        {"kind": "mouse_wheel", "x": 1, "y": 2, "dx": 3, "dy": -4},
    )
    assert inj.calls == [("wheel", 1, 2, 3, -4)]


def test_unknown_event_does_not_call_injector():
    inj = RecordingInjector()
    sink = InputSink(injector=inj)
    sink.handle("A", {"kind": "heartbeat"})
    assert inj.calls == []


def test_release_peer_injects_key_up_for_held_key():
    inj = RecordingInjector()
    sink = InputSink(injector=inj)
    sink.handle("A", {"kind": "key_down", "key": "a"})
    inj.calls.clear()
    sink.release_peer("A")
    assert ("key", "a", False) in inj.calls


def test_release_peer_injects_button_up_for_held_button():
    inj = RecordingInjector()
    sink = InputSink(injector=inj)
    sink.handle(
        "A",
        {
            "kind": "mouse_button",
            "button": "Button.left",
            "x": 0,
            "y": 0,
            "pressed": True,
        },
    )
    inj.calls.clear()
    sink.release_peer("A")
    assert ("btn", "Button.left", 0, 0, False) in inj.calls


def test_release_peer_no_held_input_no_calls():
    inj = RecordingInjector()
    sink = InputSink(injector=inj)
    sink.release_peer("A")
    assert inj.calls == []


def test_key_up_removes_from_pressed_set():
    """key_up 후에는 release_peer 가 해당 키를 다시 release 하면 안 된다."""
    inj = RecordingInjector()
    sink = InputSink(injector=inj)
    sink.handle("A", {"kind": "key_down", "key": "a"})
    sink.handle("A", {"kind": "key_up", "key": "a"})
    inj.calls.clear()
    sink.release_peer("A")
    assert inj.calls == []


def test_release_peer_isolated_per_peer():
    """peer A 의 release_peer 는 peer B 의 눌림에 영향을 주지 않는다."""
    inj = RecordingInjector()
    sink = InputSink(injector=inj)
    sink.handle("A", {"kind": "key_down", "key": "a"})
    sink.handle("B", {"kind": "key_down", "key": "b"})
    inj.calls.clear()
    sink.release_peer("A")
    # "a" was released, "b" was not touched
    assert ("key", "a", False) in inj.calls
    assert not any(c == ("key", "b", False) for c in inj.calls)


def test_default_injector_is_logging():
    """injector= 생략 시 LoggingOSInjector 가 기본으로 주입된다."""
    from injection.os_injector import LoggingOSInjector

    sink = InputSink()
    assert isinstance(sink._injector, LoggingOSInjector)


def test_multiple_held_keys_all_released():
    inj = RecordingInjector()
    sink = InputSink(injector=inj)
    sink.handle("A", {"kind": "key_down", "key": "a"})
    sink.handle("A", {"kind": "key_down", "key": "b"})
    sink.handle("A", {"kind": "key_down", "key": "c"})
    inj.calls.clear()
    sink.release_peer("A")
    released = {c[1] for c in inj.calls if c[0] == "key" and c[2] is False}
    assert released == {"a", "b", "c"}
