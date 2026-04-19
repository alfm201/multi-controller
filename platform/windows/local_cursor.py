"""컨트롤러 쪽 로컬 커서를 안전하게 이동시키는 유틸리티."""

import ctypes
import logging

from model.display.display import enable_best_effort_dpi_awareness


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
SPI_SETCURSORS = 0x0057
STANDARD_CURSOR_IDS = (
    32512,  # OCR_NORMAL
    32513,  # OCR_IBEAM
    32514,  # OCR_WAIT
    32515,  # OCR_CROSS
    32516,  # OCR_UP
    32642,  # OCR_SIZENWSE
    32643,  # OCR_SIZENESW
    32644,  # OCR_SIZEWE
    32645,  # OCR_SIZENS
    32646,  # OCR_SIZEALL
    32648,  # OCR_NO
    32649,  # OCR_HAND
    32650,  # OCR_APPSTARTING
    32651,  # OCR_HELP
)


def _cursor_is_showing(user32) -> bool | None:
    info = _CURSORINFO()
    info.cbSize = ctypes.sizeof(_CURSORINFO)
    try:
        if not user32.GetCursorInfo(ctypes.byref(info)):
            return None
    except Exception:
        return None
    return bool(info.flags & CURSOR_SHOWING)


def _transparent_cursor_handle(user32, *, width: int = 32, height: int = 32):
    if not hasattr(user32, "CreateCursor"):
        return None
    mask_bytes = max(1, (int(width) * int(height)) // 8)
    and_mask = ctypes.create_string_buffer(b"\xFF" * mask_bytes)
    xor_mask = ctypes.create_string_buffer(b"\x00" * mask_bytes)
    try:
        return user32.CreateCursor(
            None,
            0,
            0,
            int(width),
            int(height),
            and_mask,
            xor_mask,
        )
    except Exception:
        return None


def apply_transparent_system_cursors(user32=None) -> bool:
    raw_user32 = user32
    if raw_user32 is None:
        try:
            raw_user32 = ctypes.windll.user32
        except Exception as exc:
            logging.debug("[CURSOR] user32 unavailable for transparent system cursors: %s", exc)
            return False
    enable_best_effort_dpi_awareness(user32=raw_user32)
    if not hasattr(raw_user32, "SetSystemCursor"):
        return False

    applied = False
    for cursor_id in STANDARD_CURSOR_IDS:
        handle = _transparent_cursor_handle(raw_user32)
        if not handle:
            logging.debug("[CURSOR] transparent cursor creation failed for id=%s", cursor_id)
            return False
        try:
            if not raw_user32.SetSystemCursor(handle, int(cursor_id)):
                logging.debug("[CURSOR] SetSystemCursor failed id=%s", cursor_id)
                return False
        except Exception as exc:
            logging.debug("[CURSOR] SetSystemCursor raised id=%s: %s", cursor_id, exc)
            return False
        applied = True
    return applied


def restore_system_cursors(user32=None) -> bool:
    raw_user32 = user32
    if raw_user32 is None:
        try:
            raw_user32 = ctypes.windll.user32
        except Exception as exc:
            logging.debug("[CURSOR] user32 unavailable for cursor restore: %s", exc)
            return False
    enable_best_effort_dpi_awareness(user32=raw_user32)
    if not hasattr(raw_user32, "SystemParametersInfoW"):
        return False
    try:
        return bool(raw_user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0))
    except Exception as exc:
        logging.debug("[CURSOR] SystemParametersInfoW(SPI_SETCURSORS) failed: %s", exc)
        return False


def best_effort_hide_cursor(user32=None) -> bool:
    raw_user32 = user32
    if raw_user32 is None:
        try:
            raw_user32 = ctypes.windll.user32
        except Exception as exc:
            logging.debug("[CURSOR] user32 unavailable for hide: %s", exc)
            return False
    enable_best_effort_dpi_awareness(user32=raw_user32)
    if not hasattr(raw_user32, "GetCursorInfo") or not hasattr(raw_user32, "ShowCursor"):
        return False
    try:
        for _ in range(CURSOR_VISIBILITY_ADJUST_MAX_ATTEMPTS):
            showing = _cursor_is_showing(raw_user32)
            if showing is False:
                return True
            if showing is None:
                return False
            raw_user32.ShowCursor(False)
        return _cursor_is_showing(raw_user32) is False
    except Exception as exc:
        logging.debug("[CURSOR] best-effort hide failed: %s", exc)
        return False


def best_effort_show_cursor(user32=None) -> bool:
    raw_user32 = user32
    if raw_user32 is None:
        try:
            raw_user32 = ctypes.windll.user32
        except Exception as exc:
            logging.debug("[CURSOR] user32 unavailable for show: %s", exc)
            return False
    enable_best_effort_dpi_awareness(user32=raw_user32)
    if not hasattr(raw_user32, "GetCursorInfo") or not hasattr(raw_user32, "ShowCursor"):
        return False
    try:
        for _ in range(CURSOR_VISIBILITY_ADJUST_MAX_ATTEMPTS):
            showing = _cursor_is_showing(raw_user32)
            if showing is True:
                return True
            if showing is None:
                return False
            raw_user32.ShowCursor(True)
        return _cursor_is_showing(raw_user32) is True
    except Exception as exc:
        logging.debug("[CURSOR] best-effort show failed: %s", exc)
        return False


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
        self._transparent_cursors_active = False

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
            success = bool(user32.SetCursorPos(target_x, target_y))
            if not success:
                return False
            actual = get_cursor_position(user32)
            if actual is not None:
                actual_x, actual_y = actual
            else:
                actual_x, actual_y = target_x, target_y
            if self._synthetic_guard is not None:
                self._synthetic_guard.record_mouse_move(actual_x, actual_y, tolerance_px=1)
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

        rect = _RECT(
            left=clip_rect[0],
            top=clip_rect[1],
            right=clip_rect[2] + 1,
            bottom=clip_rect[3] + 1,
        )
        try:
            success = bool(user32.ClipCursor(ctypes.byref(rect)))
            if success:
                self._clip_rect = clip_rect
                if self._synthetic_guard is not None:
                    current = get_cursor_position(user32)
                    if current is not None:
                        self._synthetic_guard.record_mouse_move(int(current[0]), int(current[1]), tolerance_px=1)
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
            if success:
                self._clip_rect = None
                if self._synthetic_guard is not None:
                    current = get_cursor_position(user32)
                    if current is not None:
                        self._synthetic_guard.record_mouse_move(int(current[0]), int(current[1]), tolerance_px=1)
            return success
        except Exception as exc:
            logging.warning("[CURSOR] ClipCursor clear failed: %s", exc)
            return False

    def hide_cursor(self) -> bool:
        user32 = self._get_user32()
        if user32 is None:
            return False
        if not self._transparent_cursors_active:
            if apply_transparent_system_cursors(user32=user32):
                self._transparent_cursors_active = True
                logging.debug("[CURSOR] applied transparent system cursors for host hide")
                return True
            logging.debug("[CURSOR] transparent system cursor apply failed; falling back to ShowCursor")
        return best_effort_hide_cursor(user32=user32)

    def show_cursor(self) -> bool:
        user32 = self._get_user32()
        if user32 is None:
            return False
        restored = True
        if self._transparent_cursors_active:
            restored = restore_system_cursors(user32=user32)
            if restored:
                logging.debug("[CURSOR] restored system cursor scheme after host hide")
            else:
                logging.debug("[CURSOR] failed to restore system cursor scheme; falling back to ShowCursor only")
            self._transparent_cursors_active = False
        visible = best_effort_show_cursor(user32=user32)
        return bool(restored and visible)

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
