"""Tests for injection/os_injector.py::LoggingOSInjector.

OS 를 건드리지 않는 구현이므로 pynput 없이도 실행된다.
caplog 로 로그 라인만 검증한다.
"""

import ctypes
import logging

from injection.os_injector import LoggingOSInjector, OSInjector, ensure_cursor_visible


class FakeUser32:
    def __init__(self, *, visible=True):
        self.visible = visible
        self.show_calls = 0

    def GetCursorInfo(self, info_ptr):
        info = ctypes.cast(info_ptr, ctypes.POINTER(type(info_ptr._obj))).contents
        info.flags = 0x00000001 if self.visible else 0
        return 1

    def ShowCursor(self, show):
        self.show_calls += 1
        if show:
            self.visible = True
        return 1


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
    assert any("INJECT KEY" in rec.message and "DOWN" in rec.message
               and "key=a" in rec.message for rec in caplog.records)


def test_logging_injector_key_up(caplog):
    inj = LoggingOSInjector()
    with caplog.at_level(logging.INFO):
        inj.inject_key("a", False)
    assert any("INJECT KEY" in rec.message and "UP" in rec.message
               for rec in caplog.records)


def test_logging_injector_mouse_move(caplog):
    inj = LoggingOSInjector()
    with caplog.at_level(logging.INFO):
        inj.inject_mouse_move(100, 200)
    assert any("INJECT MOVE" in rec.message and "x=100" in rec.message
               and "y=200" in rec.message for rec in caplog.records)


def test_logging_injector_mouse_button_down(caplog):
    inj = LoggingOSInjector()
    with caplog.at_level(logging.INFO):
        inj.inject_mouse_button("Button.left", 5, 6, True)
    assert any("INJECT CLICK" in rec.message and "Button.left" in rec.message
               and "DOWN" in rec.message for rec in caplog.records)


def test_logging_injector_mouse_button_up(caplog):
    inj = LoggingOSInjector()
    with caplog.at_level(logging.INFO):
        inj.inject_mouse_button("Button.left", 5, 6, False)
    assert any("INJECT CLICK" in rec.message and "UP" in rec.message
               for rec in caplog.records)


def test_logging_injector_mouse_wheel(caplog):
    inj = LoggingOSInjector()
    with caplog.at_level(logging.INFO):
        inj.inject_mouse_wheel(0, 0, 1, -2)
    assert any("INJECT WHEEL" in rec.message and "dx=1" in rec.message
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
