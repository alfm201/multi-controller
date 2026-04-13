"""컨트롤러 쪽 로컬 커서를 안전하게 이동시키는 유틸리티."""

import ctypes
import logging

from runtime.display import enable_best_effort_dpi_awareness


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("hCursor", ctypes.c_void_p),
        ("ptScreenPos", _POINT),
    ]


CURSOR_SHOWING = 0x00000001
CURSOR_VISIBILITY_ADJUST_MAX_ATTEMPTS = 128


def get_cursor_position(user32=None) -> tuple[int, int] | None:
    raw_user32 = user32
    if raw_user32 is None:
        try:
            raw_user32 = ctypes.windll.user32
        except Exception as exc:
            logging.debug("[CURSOR] user32 unavailable for GetCursorPos: %s", exc)
            return None
    enable_best_effort_dpi_awareness(user32=raw_user32)

    point = _POINT()
    try:
        if not raw_user32.GetCursorPos(ctypes.byref(point)):
            return None
    except Exception as exc:
        logging.debug("[CURSOR] GetCursorPos failed: %s", exc)
        return None
    return int(point.x), int(point.y)


def get_clip_rect(user32=None) -> tuple[int, int, int, int] | None:
    raw_user32 = user32
    if raw_user32 is None:
        try:
            raw_user32 = ctypes.windll.user32
        except Exception as exc:
            logging.debug("[CURSOR] user32 unavailable for GetClipCursor: %s", exc)
            return None
    enable_best_effort_dpi_awareness(user32=raw_user32)

    rect = _RECT()
    try:
        if not raw_user32.GetClipCursor(ctypes.byref(rect)):
            return None
    except Exception as exc:
        logging.debug("[CURSOR] GetClipCursor failed: %s", exc)
        return None
    return (
        int(rect.left),
        int(rect.top),
        int(rect.right) - 1,
        int(rect.bottom) - 1,
    )


class LocalCursorController:
    """로컬 커서를 이동시키고 캡처 단계에서 synthetic move를 소거한다."""

    def __init__(self, synthetic_guard=None, user32=None):
        self._synthetic_guard = synthetic_guard
        self._user32 = user32
        self._clip_rect: tuple[int, int, int, int] | None = None

    def move(self, x: int, y: int) -> bool:
        try:
            target_x = int(x)
            target_y = int(y)
        except (TypeError, ValueError):
            logging.warning("[CURSOR] invalid target position x=%r y=%r", x, y)
            return False

        user32 = self._user32
        if user32 is None:
            try:
                user32 = ctypes.windll.user32
            except Exception as exc:
                logging.debug("[CURSOR] user32 unavailable: %s", exc)
                return False

        try:
            logging.debug("[CURSOR] SetCursorPos request x=%s y=%s", target_x, target_y)
            success = bool(user32.SetCursorPos(target_x, target_y))
            logging.debug("[CURSOR] SetCursorPos result=%s x=%s y=%s", success, target_x, target_y)
            if not success:
                return False
            actual = get_cursor_position(user32)
            if actual is not None:
                actual_x, actual_y = actual
                logging.debug(
                    "[CURSOR] SetCursorPos landed x=%s y=%s (requested x=%s y=%s)",
                    actual_x,
                    actual_y,
                    target_x,
                    target_y,
                )
            else:
                actual_x, actual_y = target_x, target_y
                logging.debug(
                    "[CURSOR] SetCursorPos landed position unavailable; using requested x=%s y=%s",
                    target_x,
                    target_y,
                )
            if self._synthetic_guard is not None:
                self._synthetic_guard.record_mouse_move(actual_x, actual_y)
            return success
        except Exception as exc:
            logging.warning("[CURSOR] SetCursorPos failed x=%s y=%s: %s", target_x, target_y, exc)
            return False

    def position(self) -> tuple[int, int] | None:
        return get_cursor_position(self._user32)

    def current_clip_rect(self) -> tuple[int, int, int, int] | None:
        actual = get_clip_rect(self._user32)
        if actual is not None:
            return actual
        return self._clip_rect

    def clip_to_rect(self, left: int, top: int, right: int, bottom: int) -> bool:
        user32 = self._get_user32()
        if user32 is None:
            return False

        clip_rect = (int(left), int(top), int(right), int(bottom))
        actual_rect = get_clip_rect(user32)
        if self._clip_rect == clip_rect and actual_rect == clip_rect:
            return True
        if actual_rect is not None and actual_rect != clip_rect:
            logging.debug(
                "[CURSOR] clip rect changed externally actual=(%s,%s,%s,%s) expected=(%s,%s,%s,%s)",
                actual_rect[0],
                actual_rect[1],
                actual_rect[2],
                actual_rect[3],
                clip_rect[0],
                clip_rect[1],
                clip_rect[2],
                clip_rect[3],
            )

        rect = _RECT(
            left=clip_rect[0],
            top=clip_rect[1],
            right=clip_rect[2] + 1,
            bottom=clip_rect[3] + 1,
        )
        try:
            success = bool(user32.ClipCursor(ctypes.byref(rect)))
            logging.debug(
                "[CURSOR] ClipCursor rect=(%s,%s,%s,%s) success=%s",
                clip_rect[0],
                clip_rect[1],
                clip_rect[2],
                clip_rect[3],
                success,
            )
            if success:
                self._clip_rect = clip_rect
            return success
        except Exception as exc:
            logging.warning(
                "[CURSOR] ClipCursor failed rect=(%s,%s,%s,%s): %s",
                clip_rect[0],
                clip_rect[1],
                clip_rect[2],
                clip_rect[3],
                exc,
            )
            return False

    def clear_clip(self) -> bool:
        user32 = self._get_user32()
        if user32 is None:
            return False
        actual_rect = get_clip_rect(user32)
        if self._clip_rect is None and actual_rect is None:
            return True
        try:
            success = bool(user32.ClipCursor(None))
            logging.debug("[CURSOR] ClipCursor clear success=%s", success)
            if success:
                self._clip_rect = None
            return success
        except Exception as exc:
            logging.warning("[CURSOR] ClipCursor clear failed: %s", exc)
            return False

    def hide_cursor(self) -> bool:
        user32 = self._get_user32()
        if user32 is None or not hasattr(user32, "GetCursorInfo") or not hasattr(user32, "ShowCursor"):
            return False
        try:
            for _ in range(CURSOR_VISIBILITY_ADJUST_MAX_ATTEMPTS):
                showing = self._cursor_is_showing(user32)
                if showing is False:
                    return True
                if showing is None:
                    return False
                user32.ShowCursor(False)
            return self._cursor_is_showing(user32) is False
        except Exception as exc:
            logging.debug("[CURSOR] hide cursor failed: %s", exc)
            return False

    def show_cursor(self) -> bool:
        user32 = self._get_user32()
        if user32 is None or not hasattr(user32, "GetCursorInfo") or not hasattr(user32, "ShowCursor"):
            return False
        try:
            for _ in range(CURSOR_VISIBILITY_ADJUST_MAX_ATTEMPTS):
                showing = self._cursor_is_showing(user32)
                if showing is True:
                    return True
                if showing is None:
                    return False
                user32.ShowCursor(True)
            return self._cursor_is_showing(user32) is True
        except Exception as exc:
            logging.debug("[CURSOR] show cursor failed: %s", exc)
            return False

    def _cursor_is_showing(self, user32) -> bool | None:
        info = _CURSORINFO()
        info.cbSize = ctypes.sizeof(_CURSORINFO)
        try:
            if not user32.GetCursorInfo(ctypes.byref(info)):
                return None
        except Exception:
            return None
        return bool(info.flags & CURSOR_SHOWING)

    def _get_user32(self):
        user32 = self._user32
        if user32 is not None:
            enable_best_effort_dpi_awareness(user32=user32)
            return user32
        try:
            user32 = ctypes.windll.user32
            enable_best_effort_dpi_awareness(user32=user32)
            return user32
        except Exception as exc:
            logging.debug("[CURSOR] user32 unavailable: %s", exc)
            return None
