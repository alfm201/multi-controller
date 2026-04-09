"""Tests for routing/sink.py injector delegation and authorization."""

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
    sink = InputSink(injector=inj, screen_size_provider=lambda: (1920, 1080))
    sink.handle("A", {"kind": "mouse_move", "x": 100, "y": 200})
    assert inj.calls == [("move", 100, 200)]


def test_mouse_button_down_forwarded():
    inj = RecordingInjector()
    sink = InputSink(injector=inj, screen_size_provider=lambda: (1920, 1080))
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


def test_mouse_wheel_forwarded():
    inj = RecordingInjector()
    sink = InputSink(injector=inj, screen_size_provider=lambda: (1920, 1080))
    sink.handle("A", {"kind": "mouse_wheel", "x": 1, "y": 2, "dx": 3, "dy": -4})
    assert inj.calls == [("wheel", 1, 2, 3, -4)]


def test_mouse_move_uses_normalized_coordinates_when_present():
    inj = RecordingInjector()
    sink = InputSink(injector=inj, screen_size_provider=lambda: (200, 100))
    sink.handle("A", {"kind": "mouse_move", "x": 1, "y": 2, "x_norm": 0.5, "y_norm": 0.25})
    assert inj.calls == [("move", 100, 25)]


def test_release_peer_injects_key_up_for_held_key():
    inj = RecordingInjector()
    sink = InputSink(injector=inj)
    sink.handle("A", {"kind": "key_down", "key": "a"})
    inj.calls.clear()
    sink.release_peer("A")
    assert ("key", "a", False) in inj.calls


def test_default_injector_is_logging():
    from injection.os_injector import LoggingOSInjector

    sink = InputSink()
    assert isinstance(sink._injector, LoggingOSInjector)


def test_authorized_mode_drops_other_peers():
    inj = RecordingInjector()
    sink = InputSink(injector=inj, require_authorization=True)
    sink.set_authorized_controller("A")
    sink.handle("B", {"kind": "key_down", "key": "x"})
    assert inj.calls == []


def test_authorized_mode_accepts_current_holder():
    inj = RecordingInjector()
    sink = InputSink(injector=inj, require_authorization=True)
    sink.set_authorized_controller("A")
    sink.handle("A", {"kind": "key_down", "key": "x"})
    assert inj.calls == [("key", "x", True)]


def test_clearing_authorized_controller_releases_stuck_input():
    inj = RecordingInjector()
    sink = InputSink(injector=inj, require_authorization=True)
    sink.set_authorized_controller("A")
    sink.handle("A", {"kind": "key_down", "key": "x"})
    inj.calls.clear()
    sink.set_authorized_controller(None)
    assert ("key", "x", False) in inj.calls
