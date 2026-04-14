"""OS 입력 주입 인터페이스와 구현체."""

import ctypes
import logging

from runtime.local_cursor import best_effort_show_cursor, restore_system_cursors
from runtime.windows_interaction import log_possible_admin_interaction_warning

CURSOR_SHOWING = 0x00000001
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x01000
XBUTTON1 = 0x0001
XBUTTON2 = 0x0002
WHEEL_DELTA = 120


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

    def inject_mouse_move_relative(self, dx: int, dy: int) -> None:
        raise NotImplementedError

    def inject_mouse_button(self, button_str: str, x: int | None, y: int | None, down: bool) -> None:
        raise NotImplementedError

    def inject_mouse_wheel(self, x: int | None, y: int | None, dx: int, dy: int) -> None:
        raise NotImplementedError

    def prepare_remote_control(self) -> None:
        """Best-effort hook for making remote-control mode feel ready."""
        return None

    def end_remote_control(self) -> None:
        """Best-effort hook for clearing remote-control readiness state."""
        return None


class LoggingOSInjector(OSInjector):
    """실제 OS를 건드리지 않고 로그만 남기는 테스트용 구현."""

    def inject_key(self, key_str: str, down: bool) -> None:
        state = "DOWN" if down else "UP"
        logging.info("[INJECT KEY    ] %s key=%s", state, key_str)

    def inject_mouse_move(self, x: int, y: int) -> None:
        logging.info("[INJECT MOVE   ] x=%s y=%s", x, y)

    def inject_mouse_move_relative(self, dx: int, dy: int) -> None:
        logging.info("[INJECT MOVE   ] relative dx=%s dy=%s", dx, dy)

    def inject_mouse_button(self, button_str: str, x: int | None, y: int | None, down: bool) -> None:
        state = "DOWN" if down else "UP"
        logging.info("[INJECT CLICK  ] %s %s x=%s y=%s", button_str, state, x, y)

    def inject_mouse_wheel(self, x: int | None, y: int | None, dx: int, dy: int) -> None:
        logging.info("[INJECT WHEEL  ] x=%s y=%s dx=%s dy=%s", x, y, dx, dy)

    def prepare_remote_control(self) -> None:
        logging.info("[INJECT READY  ] prepare remote control")

    def end_remote_control(self) -> None:
        logging.info("[INJECT READY  ] end remote control")


class PynputOSInjector(OSInjector):
    """pynput 기반 실제 OS 입력 주입 구현."""

    def __init__(
        self,
        synthetic_guard=None,
        *,
        keyboard_controller=None,
        mouse_controller=None,
        user32=None,
    ):
        if keyboard_controller is None or mouse_controller is None:
            from pynput import keyboard, mouse

            keyboard_controller = keyboard_controller or keyboard.Controller()
            mouse_controller = mouse_controller or mouse.Controller()

        self._keyboard = keyboard_controller
        self._mouse = mouse_controller
        self._synthetic_guard = synthetic_guard
        self._user32 = user32
        self._remote_control_prepared = False
        self._remote_cursor_primed = False

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

    def prepare_remote_control(self) -> None:
        if self._remote_control_prepared:
            return
        try:
            self._ensure_remote_cursor_ready(user32=self._get_user32())
            self._remote_control_prepared = True
            self._remote_cursor_primed = False
        except Exception as exc:
            logging.debug("[CURSOR] prepare remote control failed: %s", exc)

    def end_remote_control(self) -> None:
        self._remote_control_prepared = False
        self._remote_cursor_primed = False

    def inject_mouse_move(self, x: int, y: int) -> None:
        try:
            user32 = self._get_user32()
            self._prime_remote_cursor(user32)
            if user32 is not None and hasattr(user32, "SetCursorPos"):
                user32.SetCursorPos(int(x), int(y))
            else:
                self._mouse.position = (int(x), int(y))
            self._record_current_pointer_position(fallback=(int(x), int(y)), user32=user32)
        except Exception as exc:
            logging.warning("[INJECT MOVE   ] OS call failed x=%s y=%s: %s", x, y, exc)
            log_possible_admin_interaction_warning(exc)

    def inject_mouse_move_relative(self, dx: int, dy: int) -> None:
        try:
            user32 = self._get_user32()
            self._prime_remote_cursor(user32)
            if user32 is not None and hasattr(user32, "mouse_event"):
                user32.mouse_event(MOUSEEVENTF_MOVE, int(dx), int(dy), 0, 0)
            else:
                current = self._current_pointer_position(user32)
                if current is None:
                    current = self._mouse.position or (0, 0)
                self._mouse.position = (int(current[0]) + int(dx), int(current[1]) + int(dy))
            self._record_current_pointer_position(user32=user32)
        except Exception as exc:
            logging.warning("[INJECT MOVE   ] relative OS call failed dx=%s dy=%s: %s", dx, dy, exc)
            log_possible_admin_interaction_warning(exc)

    def inject_mouse_button(self, button_str: str, x: int | None, y: int | None, down: bool) -> None:
        try:
            button = self._parse_button(button_str)
        except Exception as exc:
            logging.warning("[INJECT CLICK  ] parse failed button=%r: %s", button_str, exc)
            return

        if button is None:
            logging.warning("[INJECT CLICK  ] unknown button=%r, dropped", button_str)
            return

        try:
            user32 = self._get_user32()
            self._prime_remote_cursor(user32)
            resolved_x, resolved_y = self._resolve_pointer_args(x, y, user32)
            if self._synthetic_guard is not None:
                self._synthetic_guard.record_mouse_button(
                    button_str,
                    resolved_x,
                    resolved_y,
                    down=down,
                )
            if x is not None and y is not None:
                if user32 is not None and hasattr(user32, "SetCursorPos"):
                    user32.SetCursorPos(int(x), int(y))
                else:
                    self._mouse.position = (int(x), int(y))
            if not self._send_mouse_button_via_user32(button_str, down, user32):
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

    def inject_mouse_wheel(self, x: int | None, y: int | None, dx: int, dy: int) -> None:
        try:
            user32 = self._get_user32()
            self._prime_remote_cursor(user32)
            resolved_x, resolved_y = self._resolve_pointer_args(x, y, user32)
            if self._synthetic_guard is not None:
                self._synthetic_guard.record_mouse_wheel(resolved_x, resolved_y, int(dx), int(dy))
            if x is not None and y is not None:
                if user32 is not None and hasattr(user32, "SetCursorPos"):
                    user32.SetCursorPos(int(x), int(y))
                else:
                    self._mouse.position = (int(x), int(y))
            if not self._send_mouse_wheel_via_user32(int(dx), int(dy), user32):
                self._mouse.scroll(int(dx), int(dy))
        except Exception as exc:
            logging.warning(
                "[INJECT WHEEL  ] OS call failed dx=%s dy=%s: %s",
                dx,
                dy,
                exc,
            )
            log_possible_admin_interaction_warning(exc)

    def _get_user32(self):
        if self._user32 is not None:
            return self._user32
        try:
            self._user32 = ctypes.windll.user32
        except Exception:
            self._user32 = None
        return self._user32

    def _ensure_remote_cursor_ready(self, user32=None) -> bool:
        raw_user32 = user32 or self._get_user32()
        restored = restore_system_cursors(user32=raw_user32)
        shown = best_effort_show_cursor(user32=raw_user32)
        if shown:
            return True
        return ensure_cursor_visible(user32=raw_user32, max_attempts=32) or restored

    def _prime_remote_cursor(self, user32=None) -> None:
        if self._remote_cursor_primed:
            return
        try:
            self._ensure_remote_cursor_ready(user32=user32)
        finally:
            self._remote_cursor_primed = True

    def _current_pointer_position(self, user32=None):
        raw_user32 = user32 or self._get_user32()
        if raw_user32 is not None and hasattr(raw_user32, "GetCursorPos"):
            point = _POINT()
            try:
                if raw_user32.GetCursorPos(ctypes.byref(point)):
                    return int(point.x), int(point.y)
            except Exception:
                pass
        return self._mouse.position

    def _record_current_pointer_position(self, fallback=None, user32=None):
        if self._synthetic_guard is None:
            return
        current = self._current_pointer_position(user32)
        if current is None:
            current = fallback
        if current is None:
            return
        self._synthetic_guard.record_mouse_move(int(current[0]), int(current[1]))

    def _resolve_pointer_args(self, x, y, user32=None):
        if x is not None and y is not None:
            return int(x), int(y)
        current = self._current_pointer_position(user32)
        if current is None:
            return 0, 0
        return int(current[0]), int(current[1])

    def _send_mouse_button_via_user32(self, button_str: str, down: bool, user32=None) -> bool:
        raw_user32 = user32 or self._get_user32()
        if raw_user32 is None or not hasattr(raw_user32, "mouse_event"):
            return False
        flags, data = _mouse_button_flag(button_str, down)
        if flags is None:
            return False
        raw_user32.mouse_event(flags, 0, 0, data, 0)
        return True

    def _send_mouse_wheel_via_user32(self, dx: int, dy: int, user32=None) -> bool:
        raw_user32 = user32 or self._get_user32()
        if raw_user32 is None or not hasattr(raw_user32, "mouse_event"):
            return False
        handled = False
        if dy:
            raw_user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, int(dy) * WHEEL_DELTA, 0)
            handled = True
        if dx:
            raw_user32.mouse_event(MOUSEEVENTF_HWHEEL, 0, 0, int(dx) * WHEEL_DELTA, 0)
            handled = True
        return handled


def _mouse_button_flag(button_str: str, down: bool) -> tuple[int | None, int]:
    mapping = {
        "Button.left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, 0),
        "Button.right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, 0),
        "Button.middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, 0),
        "Button.x1": (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON1),
        "Button.x2": (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON2),
    }
    down_flag, up_flag, data = mapping.get(button_str, (None, None, 0))
    return (down_flag if down else up_flag), data
