"""Tests for platform/injection/os_injector.py::LoggingOSInjector.

OS 를 건드리지 않는 구현이므로 pynput 없이도 실행된다.
caplog 로 로그 라인만 검증한다.
"""

import ctypes
import logging

import msp_platform.injection.os_injector as injector_module
from msp_platform.injection.os_injector import (
    LoggingOSInjector,
    OSInjector,
    PynputOSInjector,
    ensure_cursor_visible,
)


class FakeUser32:
    def __init__(self, *, visible=True):
        self.visible = visible
        self.show_calls = 0
        self.clip_calls = []
        self.cursor_positions = []
        self.mouse_events = []
        self.key_events = []
        self.sendinput_events = []

    def GetCursorInfo(self, info_ptr):
        info = ctypes.cast(info_ptr, ctypes.POINTER(type(info_ptr._obj))).contents
        info.flags = 0x00000001 if self.visible else 0
        return 1

    def ShowCursor(self, show):
        self.show_calls += 1
        if show:
            self.visible = True
        return 1

    def ClipCursor(self, rect):
        self.clip_calls.append(rect)
        return 1

    def SetCursorPos(self, x, y):
        self.cursor_positions.append((x, y))
        return 1

    def GetCursorPos(self, point_ptr):
        if self.cursor_positions:
            x, y = self.cursor_positions[-1]
        else:
            x, y = 0, 0
        point_ptr._obj.x = x
        point_ptr._obj.y = y
        return 1

    def mouse_event(self, flags, dx, dy, data, extra):
        self.mouse_events.append((flags, dx, dy, data, extra))

    def keybd_event(self, vk, scan, flags, extra):
        self.key_events.append((vk, scan, flags, extra))

    def VkKeyScanW(self, char_code):
        if 97 <= int(char_code) <= 122:
            return ord(chr(int(char_code)).upper())
        return int(char_code)

    def MapVirtualKeyW(self, vk_code, map_type):
        return int(vk_code)

    def SendInput(self, count, input_ptr, size):
        payload = input_ptr._obj
        self.sendinput_events.append(
            (
                int(payload.type),
                int(payload.ki.wVk),
                int(payload.ki.wScan),
                int(payload.ki.dwFlags),
            )
        )
        return int(count)


def test_abstract_methods_raise():
    base = OSInjector()
    try:
        base.inject_key("a", True)
    except NotImplementedError:
        pass
    else:
        raise AssertionError("inject_key should raise NotImplementedError")

    try:
        base.inject_mouse_move(0, 0)
    except NotImplementedError:
        pass
    else:
        raise AssertionError("inject_mouse_move should raise NotImplementedError")

    try:
        base.inject_mouse_move_relative(1, -1)
    except NotImplementedError:
        pass
    else:
        raise AssertionError("inject_mouse_move_relative should raise NotImplementedError")

    try:
        base.inject_mouse_button("Button.left", 0, 0, True)
    except NotImplementedError:
        pass
    else:
        raise AssertionError(
            "inject_mouse_button should raise NotImplementedError"
        )

    try:
        base.inject_mouse_wheel(0, 0, 0, 0)
    except NotImplementedError:
        pass
    else:
        raise AssertionError("inject_mouse_wheel should raise NotImplementedError")


def test_logging_injector_key_down(caplog):
    inj = LoggingOSInjector()
    with caplog.at_level(logging.INFO):
        inj.inject_key("a", True)
    assert any("[INJECT]" in rec.message and "key down" in rec.message
               and "key=a" in rec.message for rec in caplog.records)


def test_logging_injector_key_up(caplog):
    inj = LoggingOSInjector()
    with caplog.at_level(logging.INFO):
        inj.inject_key("a", False)
    assert any("[INJECT]" in rec.message and "key up" in rec.message
               for rec in caplog.records)


def test_logging_injector_mouse_move(caplog):
    inj = LoggingOSInjector()
    with caplog.at_level(logging.INFO):
        inj.inject_mouse_move(100, 200)
    assert any("[INJECT]" in rec.message and "move absolute" in rec.message and "x=100" in rec.message
               and "y=200" in rec.message for rec in caplog.records)


def test_logging_injector_relative_mouse_move(caplog):
    inj = LoggingOSInjector()
    with caplog.at_level(logging.INFO):
        inj.inject_mouse_move_relative(10, -4)
    assert any("[INJECT]" in rec.message and "move relative dx=10 dy=-4" in rec.message for rec in caplog.records)


def test_logging_injector_mouse_button_down(caplog):
    inj = LoggingOSInjector()
    with caplog.at_level(logging.INFO):
        inj.inject_mouse_button("Button.left", 5, 6, True)
    assert any("[INJECT]" in rec.message and "click" in rec.message and "Button.left" in rec.message
               and "down" in rec.message for rec in caplog.records)


def test_logging_injector_mouse_button_up(caplog):
    inj = LoggingOSInjector()
    with caplog.at_level(logging.INFO):
        inj.inject_mouse_button("Button.left", 5, 6, False)
    assert any("[INJECT]" in rec.message and "click" in rec.message and "up" in rec.message
               for rec in caplog.records)


def test_logging_injector_mouse_wheel(caplog):
    inj = LoggingOSInjector()
    with caplog.at_level(logging.INFO):
        inj.inject_mouse_wheel(0, 0, 1, -2)
    assert any("[INJECT]" in rec.message and "wheel" in rec.message and "dx=1" in rec.message
               and "dy=-2" in rec.message for rec in caplog.records)


def test_logging_injector_implements_interface():
    """LoggingOSInjector 는 OSInjector 의 서브클래스여야 한다."""
    assert issubclass(LoggingOSInjector, OSInjector)


def test_ensure_cursor_visible_returns_true_when_already_visible():
    user32 = FakeUser32(visible=True)

    assert ensure_cursor_visible(user32=user32) is True
    assert user32.show_calls == 0


def test_ensure_cursor_visible_restores_hidden_cursor():
    user32 = FakeUser32(visible=False)

    assert ensure_cursor_visible(user32=user32) is True
    assert user32.show_calls >= 1


class FakeKeyboardController:
    def __init__(self):
        self.calls = []

    def press(self, key):
        self.calls.append(("press", key))

    def release(self, key):
        self.calls.append(("release", key))


class FakeMouseController:
    def __init__(self):
        self.positions = []
        self.clicks = []
        self.scrolls = []

    @property
    def position(self):
        return self.positions[-1] if self.positions else None

    @position.setter
    def position(self, value):
        self.positions.append(tuple(value))

    def press(self, button):
        self.clicks.append(("press", button))

    def release(self, button):
        self.clicks.append(("release", button))

    def scroll(self, dx, dy):
        self.scrolls.append((dx, dy))


def test_pynput_injector_uses_user32_for_mouse_move_and_button():
    user32 = FakeUser32()
    injector = PynputOSInjector(
        keyboard_controller=FakeKeyboardController(),
        mouse_controller=FakeMouseController(),
        user32=user32,
    )

    injector.inject_mouse_move(100, 200)
    injector.inject_mouse_button("Button.left", 100, 200, True)
    injector.inject_mouse_wheel(100, 200, 0, -1)

    assert user32.cursor_positions[:2] == [(100, 200), (100, 200)]
    assert user32.mouse_events[0][0] != 0
    assert user32.mouse_events[1][0] != 0


def test_pynput_injector_uses_user32_key_events_for_modifier_keys():
    user32 = FakeUser32()
    keyboard = FakeKeyboardController()
    injector = PynputOSInjector(
        keyboard_controller=keyboard,
        mouse_controller=FakeMouseController(),
        user32=user32,
    )

    injector.inject_key("Key.ctrl", True)
    injector.inject_key("Key.ctrl_l", True)
    injector.inject_key("Key.shift", True)
    injector.inject_key("Key.ctrl_l", False)

    assert [event[1] for event in user32.sendinput_events] == [0xA2, 0xA2, 0xA0, 0xA2]
    assert [event[2] for event in user32.sendinput_events] == [0, 0, 0, 0]
    assert [event[3] for event in user32.sendinput_events] == [0x0000, 0x0000, 0x0000, 0x0002]
    assert keyboard.calls == []
    assert user32.key_events == []


def test_pynput_injector_falls_back_to_keyboard_controller_when_user32_cannot_map_key():
    class NoKeyboardUser32(FakeUser32):
        def keybd_event(self, vk, scan, flags, extra):
            raise AssertionError("keybd_event should not be used for unknown keys")

        def VkKeyScanW(self, char_code):
            return -1

        def SendInput(self, count, input_ptr, size):
            raise AssertionError("SendInput should not be used for unknown keys")

    user32 = NoKeyboardUser32()
    keyboard = FakeKeyboardController()
    injector = PynputOSInjector(
        keyboard_controller=keyboard,
        mouse_controller=FakeMouseController(),
        user32=user32,
    )

    injector.inject_key("Key.media_play_pause", True)

    assert len(keyboard.calls) == 1
    assert keyboard.calls[0][0] == "press"
    assert str(keyboard.calls[0][1]) == "Key.media_play_pause"


def test_pynput_injector_uses_relative_mouse_event_for_relative_move():
    user32 = FakeUser32()
    injector = PynputOSInjector(
        keyboard_controller=FakeKeyboardController(),
        mouse_controller=FakeMouseController(),
        user32=user32,
    )

    injector.inject_mouse_move_relative(15, -9)

    assert user32.mouse_events[0][0] != 0
    assert user32.mouse_events[0][1] == 15
    assert user32.mouse_events[0][2] == -9


def test_pynput_prepare_remote_control_restores_cursor_once_per_lease():
    user32 = FakeUser32(visible=False)
    injector = PynputOSInjector(
        keyboard_controller=FakeKeyboardController(),
        mouse_controller=FakeMouseController(),
        user32=user32,
    )

    injector.prepare_remote_control()
    first_show_calls = user32.show_calls

    injector.inject_mouse_move(100, 200)
    injector.inject_mouse_move_relative(5, -3)
    injector.inject_mouse_button("Button.left", 100, 200, True)
    injector.inject_mouse_wheel(100, 200, 0, -1)

    assert first_show_calls >= 1
    assert user32.show_calls == first_show_calls


def test_pynput_prepare_remote_control_clears_clip_before_restoring_cursor(monkeypatch):
    user32 = FakeUser32(visible=False)
    calls = []
    injector = PynputOSInjector(
        keyboard_controller=FakeKeyboardController(),
        mouse_controller=FakeMouseController(),
        user32=user32,
    )

    monkeypatch.setattr(
        injector_module,
        "release_cursor_clip",
        lambda *, user32=None: calls.append(("clip", user32)) or True,
    )
    monkeypatch.setattr(
        injector_module,
        "restore_system_cursors",
        lambda *, user32=None: calls.append(("restore", user32)) or True,
    )
    monkeypatch.setattr(
        injector_module,
        "best_effort_show_cursor",
        lambda *, user32=None: calls.append(("show", user32)) or True,
    )

    injector.prepare_remote_control()

    assert calls == [("clip", user32), ("restore", user32), ("show", user32)]


def test_pynput_first_remote_move_does_not_reprime_cursor_after_prepare(monkeypatch):
    user32 = FakeUser32(visible=False)
    calls = []
    injector = PynputOSInjector(
        keyboard_controller=FakeKeyboardController(),
        mouse_controller=FakeMouseController(),
        user32=user32,
    )

    monkeypatch.setattr(
        injector_module,
        "release_cursor_clip",
        lambda *, user32=None: calls.append(("clip", user32)) or True,
    )
    monkeypatch.setattr(
        injector_module,
        "restore_system_cursors",
        lambda *, user32=None: calls.append(("restore", user32)) or True,
    )
    monkeypatch.setattr(
        injector_module,
        "best_effort_show_cursor",
        lambda *, user32=None: calls.append(("show", user32)) or True,
    )

    injector.prepare_remote_control()
    calls.clear()

    injector.inject_mouse_move(100, 200)
    injector.inject_mouse_move(110, 210)

    assert calls == []


def test_pynput_retries_remote_cursor_recovery_until_success(monkeypatch):
    user32 = FakeUser32(visible=False)
    attempts = []
    outcomes = iter((False, True))
    injector = PynputOSInjector(
        keyboard_controller=FakeKeyboardController(),
        mouse_controller=FakeMouseController(),
        user32=user32,
    )
    injector._remote_cursor_retry_interval_sec = 0.0

    monkeypatch.setattr(
        injector,
        "_recover_remote_cursor_and_clip",
        lambda user32=None: attempts.append(user32) or next(outcomes),
    )

    injector.prepare_remote_control()
    assert injector._remote_cursor_primed is False

    injector.inject_mouse_move(100, 200)

    assert injector._remote_cursor_primed is True
    assert attempts == [user32, user32]


def test_pynput_prepare_remote_control_can_run_again_after_end_remote_control():
    user32 = FakeUser32(visible=False)
    injector = PynputOSInjector(
        keyboard_controller=FakeKeyboardController(),
        mouse_controller=FakeMouseController(),
        user32=user32,
    )

    injector.prepare_remote_control()
    assert user32.show_calls >= 1

    injector.end_remote_control()
    user32.visible = False
    show_calls_before_retry = user32.show_calls

    injector.prepare_remote_control()

    assert user32.show_calls > show_calls_before_retry
