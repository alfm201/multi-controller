"""OS 입력 주입 인터페이스와 구현체."""

import ctypes
import logging

from runtime.windows_interaction import log_possible_admin_interaction_warning

CURSOR_SHOWING = 0x00000001


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("hCursor", ctypes.c_void_p),
        ("ptScreenPos", _POINT),
    ]


def ensure_cursor_visible(user32=None, *, max_attempts: int = 8) -> bool:
    """Best-effort cursor visibility recovery for targets receiving remote mouse input."""
    raw_user32 = user32
    if raw_user32 is None:
        try:
            raw_user32 = ctypes.windll.user32
        except Exception:
            return False

    if not hasattr(raw_user32, "GetCursorInfo"):
        return False

    def _cursor_showing() -> bool | None:
        info = _CURSORINFO()
        info.cbSize = ctypes.sizeof(_CURSORINFO)
        try:
            if not raw_user32.GetCursorInfo(ctypes.byref(info)):
                return None
        except Exception:
            return None
        return bool(info.flags & CURSOR_SHOWING)

    visible = _cursor_showing()
    if visible is True:
        return True
    if visible is None or not hasattr(raw_user32, "ShowCursor"):
        return False

    try:
        for _ in range(max(1, int(max_attempts))):
            raw_user32.ShowCursor(True)
            if _cursor_showing() is True:
                logging.info("[CURSOR] restored visible cursor for remote input")
                return True
    except Exception as exc:
        logging.debug("[CURSOR] ensure visible failed: %s", exc)
        return False
    return False


class OSInjector:
    """InputSink가 의존하는 OS 입력 주입 인터페이스."""

    def inject_key(self, key_str: str, down: bool) -> None:
        raise NotImplementedError

    def inject_mouse_move(self, x: int, y: int) -> None:
        raise NotImplementedError

    def inject_mouse_button(self, button_str: str, x: int, y: int, down: bool) -> None:
        raise NotImplementedError

    def inject_mouse_wheel(self, x: int, y: int, dx: int, dy: int) -> None:
        raise NotImplementedError


class LoggingOSInjector(OSInjector):
    """실제 OS를 건드리지 않고 로그만 남기는 테스트용 구현."""

    def inject_key(self, key_str: str, down: bool) -> None:
        state = "DOWN" if down else "UP"
        logging.info("[INJECT KEY    ] %s key=%s", state, key_str)

    def inject_mouse_move(self, x: int, y: int) -> None:
        logging.info("[INJECT MOVE   ] x=%s y=%s", x, y)

    def inject_mouse_button(self, button_str: str, x: int, y: int, down: bool) -> None:
        state = "DOWN" if down else "UP"
        logging.info("[INJECT CLICK  ] %s %s x=%s y=%s", button_str, state, x, y)

    def inject_mouse_wheel(self, x: int, y: int, dx: int, dy: int) -> None:
        logging.info("[INJECT WHEEL  ] x=%s y=%s dx=%s dy=%s", x, y, dx, dy)


class PynputOSInjector(OSInjector):
    """pynput 기반 실제 OS 입력 주입 구현."""

    def __init__(self, synthetic_guard=None):
        from pynput import keyboard, mouse

        self._keyboard = keyboard.Controller()
        self._mouse = mouse.Controller()
        self._synthetic_guard = synthetic_guard

        from injection import key_parser

        self._parse_key = key_parser.parse_key
        self._parse_button = key_parser.parse_button

    def inject_key(self, key_str: str, down: bool) -> None:
        try:
            key = self._parse_key(key_str)
        except Exception as exc:
            logging.warning("[INJECT KEY    ] parse failed key=%r: %s", key_str, exc)
            return

        if key is None:
            logging.warning("[INJECT KEY    ] unknown key=%r, dropped", key_str)
            return

        try:
            if self._synthetic_guard is not None:
                self._synthetic_guard.record_key(key_str, down=down)
            if down:
                self._keyboard.press(key)
            else:
                self._keyboard.release(key)
        except Exception as exc:
            logging.warning(
                "[INJECT KEY    ] OS call failed key=%r down=%s: %s",
                key_str,
                down,
                exc,
            )
            log_possible_admin_interaction_warning(exc)

    def inject_mouse_move(self, x: int, y: int) -> None:
        try:
            ensure_cursor_visible()
            if self._synthetic_guard is not None:
                self._synthetic_guard.record_mouse_move(int(x), int(y))
            self._mouse.position = (int(x), int(y))
        except Exception as exc:
            logging.warning("[INJECT MOVE   ] OS call failed x=%s y=%s: %s", x, y, exc)
            log_possible_admin_interaction_warning(exc)

    def inject_mouse_button(self, button_str: str, x: int, y: int, down: bool) -> None:
        try:
            button = self._parse_button(button_str)
        except Exception as exc:
            logging.warning("[INJECT CLICK  ] parse failed button=%r: %s", button_str, exc)
            return

        if button is None:
            logging.warning("[INJECT CLICK  ] unknown button=%r, dropped", button_str)
            return

        try:
            ensure_cursor_visible()
            if self._synthetic_guard is not None:
                self._synthetic_guard.record_mouse_button(
                    button_str,
                    int(x),
                    int(y),
                    down=down,
                )
            self._mouse.position = (int(x), int(y))
            if down:
                self._mouse.press(button)
            else:
                self._mouse.release(button)
        except Exception as exc:
            logging.warning(
                "[INJECT CLICK  ] OS call failed button=%r down=%s: %s",
                button_str,
                down,
                exc,
            )
            log_possible_admin_interaction_warning(exc)

    def inject_mouse_wheel(self, x: int, y: int, dx: int, dy: int) -> None:
        try:
            ensure_cursor_visible()
            if self._synthetic_guard is not None:
                self._synthetic_guard.record_mouse_wheel(int(x), int(y), int(dx), int(dy))
            self._mouse.scroll(int(dx), int(dy))
        except Exception as exc:
            logging.warning(
                "[INJECT WHEEL  ] OS call failed dx=%s dy=%s: %s",
                dx,
                dy,
                exc,
            )
            log_possible_admin_interaction_warning(exc)
