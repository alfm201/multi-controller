"""주입한 synthetic 입력을 다시 캡처하지 않도록 막는 공통 guard."""

from collections import deque
import threading
import time


class SyntheticInputGuard:
    """최근 주입 입력을 짧게 기억하고 캡처 단계에서 한 번만 소거한다."""

    KEY_TTL_SEC = 0.75
    POINTER_TTL_SEC = 0.35
    POINTER_TOLERANCE_PX = 3
    POINTER_MOVE_TOLERANCE_PX = 0
    MAX_MOVE_SAMPLES = 8

    def __init__(self, now_fn=None):
        self._now = now_fn or time.monotonic
        self._event_lock = threading.Lock()
        self._move_lock = threading.Lock()
        self._key_events = deque()
        self._button_events = deque()
        self._wheel_events = deque()
        self._move_events = []

    def record_key(self, key_str: str, down: bool) -> None:
        self._record_event(
            self._key_events,
            self.KEY_TTL_SEC,
            {"key": str(key_str), "down": bool(down)},
        )

    def record_mouse_move(self, x: int, y: int, *, tolerance_px: int | None = None) -> None:
        now = self._now()
        entry = {
            "expires_at": now + self.POINTER_TTL_SEC,
            "x": int(x),
            "y": int(y),
            "tolerance_px": max(
                int(self.POINTER_MOVE_TOLERANCE_PX if tolerance_px is None else tolerance_px),
                0,
            ),
        }
        with self._move_lock:
            self._purge_move_locked(now)
            self._move_events.append(entry)
            if len(self._move_events) > self.MAX_MOVE_SAMPLES:
                del self._move_events[:-self.MAX_MOVE_SAMPLES]

    def record_mouse_button(self, button_str: str, x: int, y: int, down: bool) -> None:
        self._record_event(
            self._button_events,
            self.POINTER_TTL_SEC,
            {
                "button": str(button_str),
                "x": int(x),
                "y": int(y),
                "down": bool(down),
            },
        )

    def record_mouse_wheel(self, x: int, y: int, dx: int, dy: int) -> None:
        self._record_event(
            self._wheel_events,
            self.POINTER_TTL_SEC,
            {
                "x": int(x),
                "y": int(y),
                "dx": int(dx),
                "dy": int(dy),
            },
        )

    def should_suppress_key(self, key_str: str, down: bool) -> bool:
        return self._consume_event(
            self._key_events,
            lambda entry: entry["key"] == str(key_str) and entry["down"] == bool(down),
        )

    def should_suppress_mouse_move(self, x: int, y: int) -> bool:
        now = self._now()
        with self._move_lock:
            self._purge_move_locked(now)
            for index in range(len(self._move_events) - 1, -1, -1):
                entry = self._move_events[index]
                if self._close_xy(
                    entry["x"],
                    entry["y"],
                    x,
                    y,
                    tolerance_px=entry.get("tolerance_px", self.POINTER_MOVE_TOLERANCE_PX),
                ):
                    del self._move_events[index]
                    return True
        return False

    def should_suppress_mouse_button(self, button_str: str, x: int, y: int, down: bool) -> bool:
        return self._consume_event(
            self._button_events,
            lambda entry: (
                entry["button"] == str(button_str)
                and entry["down"] == bool(down)
                and self._close_xy(
                    entry["x"],
                    entry["y"],
                    x,
                    y,
                    tolerance_px=self.POINTER_TOLERANCE_PX,
                )
            ),
        )

    def should_suppress_mouse_wheel(self, x: int, y: int, dx: int, dy: int) -> bool:
        return self._consume_event(
            self._wheel_events,
            lambda entry: (
                entry["dx"] == int(dx)
                and entry["dy"] == int(dy)
                and self._close_xy(
                    entry["x"],
                    entry["y"],
                    x,
                    y,
                    tolerance_px=self.POINTER_TOLERANCE_PX,
                )
            ),
        )

    def _record_event(self, bucket, ttl_sec, payload):
        now = self._now()
        with self._event_lock:
            self._purge_event_bucket_locked(bucket, now)
            bucket.append({"expires_at": now + ttl_sec, **payload})

    def _consume_event(self, bucket, predicate) -> bool:
        now = self._now()
        with self._event_lock:
            self._purge_event_bucket_locked(bucket, now)
            for index, entry in enumerate(bucket):
                if predicate(entry):
                    del bucket[index]
                    return True
        return False

    def _purge_event_bucket_locked(self, bucket, now):
        while bucket and bucket[0]["expires_at"] <= now:
            bucket.popleft()

    def _purge_move_locked(self, now):
        if not self._move_events:
            return
        self._move_events = [
            entry
            for entry in self._move_events
            if entry["expires_at"] > now
        ]

    def _close_xy(self, expected_x, expected_y, actual_x, actual_y, *, tolerance_px: int) -> bool:
        return (
            abs(int(expected_x) - int(actual_x)) <= int(tolerance_px)
            and abs(int(expected_y) - int(actual_y)) <= int(tolerance_px)
        )
