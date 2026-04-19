"""In-memory application log buffer for the advanced runtime UI."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import logging
from threading import RLock

UI_LOG_LEVELS = ("INFO", "WARNING", "ERROR")
DEBUG_UI_LOG_LEVELS = ("DETAIL", "DEBUG")
MAX_UI_LOG_ENTRIES = {
    "INFO": 120,
    "DETAIL": 180,
    "DEBUG": 180,
    "WARNING": 260,
    "ERROR": 260,
}


@dataclass(frozen=True)
class ApplicationLogEntry:
    sequence: int
    timestamp: str
    level: str
    message: str


class ApplicationLogStore:
    def __init__(self) -> None:
        self._entries = {
            level: deque(maxlen=MAX_UI_LOG_ENTRIES[level])
            for level in (*UI_LOG_LEVELS, *DEBUG_UI_LOG_LEVELS)
        }
        self._lock = RLock()
        self._sequence = 0
        self._version = 0

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def add(self, *, timestamp: str, level: str, message: str) -> None:
        if level not in self._entries:
            return
        with self._lock:
            self._sequence += 1
            self._version += 1
            self._entries[level].appendleft(
                ApplicationLogEntry(
                    sequence=self._sequence,
                    timestamp=timestamp,
                    level=level,
                    message=message,
                )
            )

    def snapshot(self) -> tuple[ApplicationLogEntry, ...]:
        with self._lock:
            merged = []
            for queue in self._entries.values():
                merged.extend(queue)
            merged.sort(key=lambda item: item.sequence, reverse=True)
            return tuple(merged)

    def clear(self) -> None:
        with self._lock:
            for queue in self._entries.values():
                queue.clear()
            self._version += 1


class UILogHandler(logging.Handler):
    def __init__(self, store: ApplicationLogStore):
        super().__init__(level=logging.DEBUG)
        self._store = store

    def emit(self, record) -> None:
        try:
            level = _normalize_ui_level(record.levelname)
            if level is None:
                return
            message = record.getMessage()
            timestamp = self.formatter.formatTime(record, self.formatter.datefmt) if self.formatter else ""
            self._store.add(
                timestamp=timestamp,
                level=level,
                message=message,
            )
        except Exception:
            self.handleError(record)


def _normalize_ui_level(level_name: str | None) -> str | None:
    normalized = (level_name or "").upper()
    if normalized in {"CRITICAL", "ERROR"}:
        return "ERROR"
    if normalized == "WARNING":
        return "WARNING"
    if normalized == "DETAIL":
        return "DETAIL"
    if normalized == "DEBUG":
        return "DEBUG"
    if normalized == "INFO":
        return "INFO"
    return None


def available_ui_log_levels(*, debug_enabled: bool) -> tuple[str, ...]:
    return (*UI_LOG_LEVELS, *DEBUG_UI_LOG_LEVELS) if debug_enabled else UI_LOG_LEVELS


_STORE = ApplicationLogStore()


def get_application_log_store() -> ApplicationLogStore:
    return _STORE
