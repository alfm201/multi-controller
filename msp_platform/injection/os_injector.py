"""OS 입력 주입 인터페이스와 구현체."""

import ctypes
import logging
import time

from app.logging.app_logging import TAG_CURSOR, TAG_INJECT, tag_message
from msp_platform.windows.local_cursor import best_effort_show_cursor, restore_system_cursors
from msp_platform.windows.clip_recovery import release_cursor_clip
from msp_platform.windows.windows_interaction import log_possible_admin_interaction_warning

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
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
INPUT_KEYBOARD = 1
MAPVK_VK_TO_VSC = 0
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SPACE = 0x20
VK_CAPITAL = 0x14
VK_ESCAPE = 0x1B
VK_PRIOR = 0x21
VK_NEXT = 0x22
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_INSERT = 0x2D
VK_DELETE = 0x2E
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_LWIN = 0x5B
VK_RWIN = 0x5C

_KEY_TOKEN_TO_VK = {
    "Key.shift": (VK_LSHIFT, 0),
    "Key.shift_l": (VK_LSHIFT, 0),
    "Key.shift_r": (VK_RSHIFT, 0),
    "Key.ctrl": (VK_LCONTROL, 0),
    "Key.ctrl_l": (VK_LCONTROL, 0),
    "Key.ctrl_r": (VK_RCONTROL, KEYEVENTF_EXTENDEDKEY),
    "Key.alt": (VK_LMENU, 0),
    "Key.alt_l": (VK_LMENU, 0),
    "Key.alt_r": (VK_RMENU, KEYEVENTF_EXTENDEDKEY),
    "Key.cmd": (VK_LWIN, KEYEVENTF_EXTENDEDKEY),
    "Key.cmd_l": (VK_LWIN, KEYEVENTF_EXTENDEDKEY),
    "Key.cmd_r": (VK_RWIN, KEYEVENTF_EXTENDEDKEY),
    "Key.backspace": (VK_BACK, 0),
    "Key.tab": (VK_TAB, 0),
    "Key.enter": (VK_RETURN, 0),
    "Key.caps_lock": (VK_CAPITAL, 0),
    "Key.esc": (VK_ESCAPE, 0),
    "Key.page_up": (VK_PRIOR, KEYEVENTF_EXTENDEDKEY),
    "Key.page_down": (VK_NEXT, KEYEVENTF_EXTENDEDKEY),
    "Key.end": (VK_END, KEYEVENTF_EXTENDEDKEY),
    "Key.home": (VK_HOME, KEYEVENTF_EXTENDEDKEY),
    "Key.left": (VK_LEFT, KEYEVENTF_EXTENDEDKEY),
    "Key.up": (VK_UP, KEYEVENTF_EXTENDEDKEY),
    "Key.right": (VK_RIGHT, KEYEVENTF_EXTENDEDKEY),
    "Key.down": (VK_DOWN, KEYEVENTF_EXTENDEDKEY),
    "Key.insert": (VK_INSERT, KEYEVENTF_EXTENDEDKEY),
    "Key.delete": (VK_DELETE, KEYEVENTF_EXTENDEDKEY),
}


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("hCursor", ctypes.c_void_p),
        ("ptScreenPos", _POINT),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_uint),
        ("time", ctypes.c_uint),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [
        ("type", ctypes.c_uint),
        ("union", _INPUT_UNION),
    ]


def _set_api_signature(api, name, argtypes, restype):
    func = getattr(api, name, None)
    if func is None:
        return
    try:
        func.argtypes = argtypes
        func.restype = restype
    except Exception:
        return


def _configure_user32_input_api(user32) -> None:
    _set_api_signature(user32, "ShowCursor", [ctypes.c_int], ctypes.c_int)
    _set_api_signature(user32, "GetCursorInfo", [ctypes.POINTER(_CURSORINFO)], ctypes.c_int)
    _set_api_signature(user32, "SetCursorPos", [ctypes.c_int, ctypes.c_int], ctypes.c_int)
    _set_api_signature(user32, "GetCursorPos", [ctypes.POINTER(_POINT)], ctypes.c_int)
    _set_api_signature(
        user32,
        "mouse_event",
        [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_size_t],
        None,
    )
    _set_api_signature(
        user32,
        "keybd_event",
        [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_uint, ctypes.c_size_t],
        None,
    )
    _set_api_signature(
        user32,
        "MapVirtualKeyW",
        [ctypes.c_uint, ctypes.c_uint],
        ctypes.c_uint,
    )
    _set_api_signature(
        user32,
        "SendInput",
        [ctypes.c_uint, ctypes.POINTER(_INPUT), ctypes.c_int],
        ctypes.c_uint,
    )


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
                logging.info(tag_message(TAG_CURSOR, "restored visible cursor for remote input"))
                return True
    except Exception as exc:
        logging.debug(tag_message(TAG_CURSOR, "ensure visible failed: %s"), exc)
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
        logging.info(tag_message(TAG_INJECT, "key %s key=%s"), state.lower(), key_str)

    def inject_mouse_move(self, x: int, y: int) -> None:
        logging.info(tag_message(TAG_INJECT, "move absolute x=%s y=%s"), x, y)

    def inject_mouse_move_relative(self, dx: int, dy: int) -> None:
        logging.info(tag_message(TAG_INJECT, "move relative dx=%s dy=%s"), dx, dy)

    def inject_mouse_button(self, button_str: str, x: int | None, y: int | None, down: bool) -> None:
        state = "DOWN" if down else "UP"
        logging.info(tag_message(TAG_INJECT, "click %s %s x=%s y=%s"), button_str, state.lower(), x, y)

    def inject_mouse_wheel(self, x: int | None, y: int | None, dx: int, dy: int) -> None:
        logging.info(tag_message(TAG_INJECT, "wheel x=%s y=%s dx=%s dy=%s"), x, y, dx, dy)

    def prepare_remote_control(self) -> None:
        logging.info(tag_message(TAG_INJECT, "prepare remote control"))

    def end_remote_control(self) -> None:
        logging.info(tag_message(TAG_INJECT, "end remote control"))


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
        self._user32_configured = False
        self._remote_control_prepared = False
        self._remote_cursor_primed = False
        self._remote_cursor_retry_interval_sec = 0.25
        self._next_remote_cursor_retry_at = 0.0

        from msp_platform.injection import key_parser

        self._parse_key = key_parser.parse_key
        self._parse_button = key_parser.parse_button

    def inject_key(self, key_str: str, down: bool) -> None:
        user32 = self._get_user32()
        try:
            if self._synthetic_guard is not None:
                self._synthetic_guard.record_key(key_str, down=down)
            if self._inject_key_via_user32(key_str, down, user32):
                return

            key = self._parse_key(key_str)
        except Exception as exc:
            logging.warning(tag_message(TAG_INJECT, "key parse failed key=%r: %s"), key_str, exc)
            return

        if key is None:
            logging.warning(tag_message(TAG_INJECT, "key dropped unknown key=%r"), key_str)
            return

        try:
            if down:
                self._keyboard.press(key)
            else:
                self._keyboard.release(key)
        except Exception as exc:
            logging.warning(
                tag_message(TAG_INJECT, "key OS call failed key=%r down=%s: %s"),
                key_str,
                down,
                exc,
            )
            log_possible_admin_interaction_warning(exc)

    def prepare_remote_control(self) -> None:
        if self._remote_control_prepared and self._remote_cursor_primed:
            return
        try:
            self._remote_cursor_primed = self._attempt_remote_cursor_recovery(
                user32=self._get_user32(),
                respect_retry_window=False,
            )
            self._remote_control_prepared = True
        except Exception as exc:
            logging.debug(tag_message(TAG_CURSOR, "prepare remote control failed: %s"), exc)

    def end_remote_control(self) -> None:
        self._remote_control_prepared = False
        self._remote_cursor_primed = False
        self._next_remote_cursor_retry_at = 0.0

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
            logging.warning(tag_message(TAG_INJECT, "move absolute OS call failed x=%s y=%s: %s"), x, y, exc)
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
            logging.warning(tag_message(TAG_INJECT, "move relative OS call failed dx=%s dy=%s: %s"), dx, dy, exc)
            log_possible_admin_interaction_warning(exc)

    def inject_mouse_button(self, button_str: str, x: int | None, y: int | None, down: bool) -> None:
        try:
            button = self._parse_button(button_str)
        except Exception as exc:
            logging.warning(tag_message(TAG_INJECT, "click parse failed button=%r: %s"), button_str, exc)
            return

        if button is None:
            logging.warning(tag_message(TAG_INJECT, "click dropped unknown button=%r"), button_str)
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
                tag_message(TAG_INJECT, "click OS call failed button=%r down=%s: %s"),
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
                tag_message(TAG_INJECT, "wheel OS call failed dx=%s dy=%s: %s"),
                dx,
                dy,
                exc,
            )
            log_possible_admin_interaction_warning(exc)

    def _get_user32(self):
        if self._user32 is not None:
            if not self._user32_configured:
                _configure_user32_input_api(self._user32)
                self._user32_configured = True
            return self._user32
        try:
            self._user32 = ctypes.windll.user32
        except Exception:
            self._user32 = None
        if self._user32 is not None and not self._user32_configured:
            _configure_user32_input_api(self._user32)
            self._user32_configured = True
        return self._user32

    def _inject_key_via_user32(self, key_str: str, down: bool, user32=None) -> bool:
        raw_user32 = user32 or self._get_user32()
        if raw_user32 is None:
            return False

        resolved = self._resolve_virtual_key(key_str, raw_user32)
        if resolved is None:
            return False

        vk_code, event_flags = resolved
        prefer_scancode = key_str not in _KEY_TOKEN_TO_VK
        if self._send_key_via_sendinput(
            int(vk_code),
            int(event_flags),
            down,
            raw_user32,
            prefer_scancode=prefer_scancode,
        ):
            return True
        if not hasattr(raw_user32, "keybd_event"):
            return False
        flags = event_flags | (KEYEVENTF_KEYUP if not down else 0)
        raw_user32.keybd_event(int(vk_code), 0, int(flags), 0)
        return True

    def _resolve_virtual_key(self, key_str: str, user32=None):
        if key_str in _KEY_TOKEN_TO_VK:
            return _KEY_TOKEN_TO_VK[key_str]

        if isinstance(key_str, str) and key_str.startswith("Key.f") and key_str[5:].isdigit():
            number = int(key_str[5:])
            if 1 <= number <= 24:
                return 0x70 + number - 1, 0

        if isinstance(key_str, str) and len(key_str) == 1:
            raw_user32 = user32 or self._get_user32()
            if raw_user32 is not None and hasattr(raw_user32, "VkKeyScanW"):
                try:
                    vk_scan = int(raw_user32.VkKeyScanW(ord(key_str)))
                except Exception:
                    vk_scan = -1
                if vk_scan != -1:
                    return vk_scan & 0xFF, 0
            if key_str.isascii() and key_str.isprintable():
                return ord(key_str.upper()), 0

        return None

    def _send_key_via_sendinput(
        self,
        vk_code: int,
        event_flags: int,
        down: bool,
        user32=None,
        *,
        prefer_scancode: bool = True,
    ) -> bool:
        raw_user32 = user32 or self._get_user32()
        if raw_user32 is None or not hasattr(raw_user32, "SendInput"):
            return False

        scan_code = 0
        if prefer_scancode and hasattr(raw_user32, "MapVirtualKeyW"):
            try:
                scan_code = int(raw_user32.MapVirtualKeyW(int(vk_code), MAPVK_VK_TO_VSC))
            except Exception:
                scan_code = 0

        flags = int(event_flags)
        if scan_code:
            flags |= KEYEVENTF_SCANCODE
        if not down:
            flags |= KEYEVENTF_KEYUP

        keyboard_input = _KEYBDINPUT(
            wVk=0 if scan_code else int(vk_code),
            wScan=int(scan_code),
            dwFlags=flags,
            time=0,
            dwExtraInfo=0,
        )
        payload = _INPUT(type=INPUT_KEYBOARD, ki=keyboard_input)
        sent = int(raw_user32.SendInput(1, ctypes.byref(payload), ctypes.sizeof(_INPUT)))
        return sent == 1

    def _ensure_remote_cursor_ready(self, user32=None) -> bool:
        raw_user32 = user32 or self._get_user32()
        restored = restore_system_cursors(user32=raw_user32)
        shown = best_effort_show_cursor(user32=raw_user32)
        if shown:
            return True
        return ensure_cursor_visible(user32=raw_user32, max_attempts=32) or restored

    def _recover_remote_cursor_and_clip(self, user32=None) -> bool:
        raw_user32 = user32 or self._get_user32()
        clip_cleared = release_cursor_clip(user32=raw_user32)
        cursor_ready = self._ensure_remote_cursor_ready(user32=raw_user32)
        logging.debug(
            tag_message(TAG_CURSOR, "remote readiness clip_cleared=%s cursor_ready=%s"),
            clip_cleared,
            cursor_ready,
        )
        return bool(cursor_ready)

    def _attempt_remote_cursor_recovery(
        self,
        user32=None,
        *,
        respect_retry_window: bool = True,
    ) -> bool:
        if self._remote_cursor_primed:
            return True
        now = time.monotonic()
        if respect_retry_window and now < self._next_remote_cursor_retry_at:
            return False
        self._next_remote_cursor_retry_at = now + max(float(self._remote_cursor_retry_interval_sec), 0.0)
        cursor_ready = self._recover_remote_cursor_and_clip(user32=user32)
        if cursor_ready:
            self._next_remote_cursor_retry_at = 0.0
        return cursor_ready

    def _prime_remote_cursor(self, user32=None) -> None:
        if self._remote_cursor_primed:
            return
        try:
            self._remote_cursor_primed = self._attempt_remote_cursor_recovery(user32=user32)
        except Exception as exc:
            logging.debug(tag_message(TAG_CURSOR, "prime remote cursor failed: %s"), exc)

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
