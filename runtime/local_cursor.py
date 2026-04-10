"""컨트롤러 쪽 로컬 커서를 안전하게 이동시키는 유틸리티."""

import ctypes
import logging


class LocalCursorController:
    """로컬 커서를 이동시키고 캡처 단계에서 synthetic move를 소거한다."""

    def __init__(self, synthetic_guard=None, user32=None):
        self._synthetic_guard = synthetic_guard
        self._user32 = user32

    def move(self, x: int, y: int) -> bool:
        try:
            target_x = int(x)
            target_y = int(y)
        except (TypeError, ValueError):
            logging.warning("[CURSOR] invalid target position x=%r y=%r", x, y)
            return False

        if self._synthetic_guard is not None:
            self._synthetic_guard.record_mouse_move(target_x, target_y)

        user32 = self._user32
        if user32 is None:
            try:
                user32 = ctypes.windll.user32
            except Exception as exc:
                logging.debug("[CURSOR] user32 unavailable: %s", exc)
                return False

        try:
            return bool(user32.SetCursorPos(target_x, target_y))
        except Exception as exc:
            logging.warning("[CURSOR] SetCursorPos failed x=%s y=%s: %s", target_x, target_y, exc)
            return False
