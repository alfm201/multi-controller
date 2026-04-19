"""Structured runtime log file management."""

from __future__ import annotations

import logging
import zipfile
from datetime import datetime
from pathlib import Path
import re

from app.config.storage_maintenance import ManagedPathInfo, path_size_bytes, prune_managed_paths

LOG_FILE_EXTENSION = ".log"
LOG_ARCHIVE_EXTENSION = ".zip"
LOG_ARCHIVE_DIRNAME = "archive"
KNOWN_LOG_KINDS = ("application", "warning", "error", "debug")
_ROOT_LOG_PATTERN = re.compile(
    r"^(?P<kind>application|warning|error|debug|multiscreenpass)-(?P<date>\d{4}-\d{2}-\d{2})\.log$"
)


class ManagedDailyLogHandler(logging.Handler):
    """Write one managed log stream and archive old day files by date bundle."""

    terminator = "\n"

    def __init__(
        self,
        *,
        log_dir: str | Path,
        kind: str,
        retention_days: int,
        max_total_size_mb: int,
        min_level: int = logging.INFO,
        max_level: int | None = None,
        now_provider=None,
    ):
        super().__init__(level=logging.DEBUG)
        self.log_dir = Path(log_dir)
        self.archive_dir = self.log_dir / LOG_ARCHIVE_DIRNAME
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.kind = kind
        self.retention_days = int(retention_days)
        self.max_total_size_mb = int(max_total_size_mb)
        self.min_level = int(min_level)
        self.max_level = None if max_level is None else int(max_level)
        self._now_provider = now_provider or datetime.now
        self._current_date_key: str | None = None
        self._current_path: Path | None = None
        self._stream = None
        self._maintenance_counter = 0
        self.createLock()
        self._ensure_current_stream()
        self.run_maintenance()

    @property
    def current_log_path(self) -> Path | None:
        return self._current_path

    def update_policy(self, *, retention_days: int, max_total_size_mb: int) -> None:
        self.acquire()
        try:
            self.retention_days = int(retention_days)
            self.max_total_size_mb = int(max_total_size_mb)
            self.run_maintenance()
        finally:
            self.release()

    def emit(self, record) -> None:
        if record.levelno < self.min_level:
            return
        if self.max_level is not None and record.levelno > self.max_level:
            return
        try:
            message = self.format(record)
            self.acquire()
            try:
                self._ensure_current_stream()
                if self._stream is None:
                    return
                self._stream.write(message + self.terminator)
                self._stream.flush()
                self._maintenance_counter += 1
                if self._maintenance_counter >= 500:
                    self._maintenance_counter = 0
                    self.run_maintenance()
            finally:
                self.release()
        except Exception:
            self.handleError(record)

    def flush(self) -> None:
        self.acquire()
        try:
            if self._stream is not None:
                self._stream.flush()
        finally:
            self.release()

    def close(self) -> None:
        self.acquire()
        try:
            if self._stream is not None:
                self._stream.flush()
                self._stream.close()
                self._stream = None
        finally:
            self.release()
        super().close()

    def run_maintenance(self) -> None:
        self._compress_previous_logs()
        prune_managed_paths(
            self._collect_archive_paths(),
            max_age_days=self.retention_days,
            max_total_size_bytes=max(1, self.max_total_size_mb) * 1024 * 1024,
        )

    def _ensure_current_stream(self) -> None:
        date_key = self._current_date_key_for_now()
        if date_key == self._current_date_key and self._stream is not None:
            return

        if self._stream is not None:
            self._stream.flush()
            self._stream.close()
            self._stream = None

        self._current_date_key = date_key
        self._current_path = self._path_for_date_key(date_key)
        self._stream = self._current_path.open("a", encoding="utf-8")
        self.run_maintenance()

    def _current_date_key_for_now(self) -> str:
        return self._now_provider().strftime("%Y-%m-%d")

    def _path_for_date_key(self, date_key: str) -> Path:
        return self.log_dir / f"{self.kind}-{date_key}{LOG_FILE_EXTENSION}"

    def _compress_previous_logs(self) -> None:
        current_date = self._current_date_key_for_now()
        grouped: dict[str, list[Path]] = {}
        for path in self.log_dir.iterdir():
            if not path.is_file():
                continue
            match = _ROOT_LOG_PATTERN.match(path.name)
            if match is None:
                continue
            date_key = match.group("date")
            if date_key == current_date:
                continue
            grouped.setdefault(date_key, []).append(path)

        for date_key, paths in grouped.items():
            self._compress_date_group(date_key, paths)

    def _compress_date_group(self, date_key: str, paths: list[Path]) -> None:
        archive_path = self.archive_dir / f"{date_key}{LOG_ARCHIVE_EXTENSION}"
        try:
            mode = "a" if archive_path.exists() else "w"
            with zipfile.ZipFile(archive_path, mode, compression=zipfile.ZIP_DEFLATED) as archive:
                existing_names = set(archive.namelist())
                for path in sorted(paths):
                    if path.name in existing_names:
                        continue
                    archive.write(path, arcname=path.name)
            for path in paths:
                path.unlink(missing_ok=True)
        except OSError:
            return

    def _collect_archive_paths(self) -> list[ManagedPathInfo]:
        candidates: list[ManagedPathInfo] = []
        if not self.archive_dir.exists():
            return candidates
        for path in self.archive_dir.iterdir():
            if not path.is_file():
                continue
            if path.suffix.lower() != LOG_ARCHIVE_EXTENSION:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            candidates.append(
                ManagedPathInfo(
                    path=path,
                    modified_at=datetime.fromtimestamp(stat.st_mtime),
                    size_bytes=path_size_bytes(path),
                )
            )
        return candidates
