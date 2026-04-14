"""Reusable retention helpers for managed files and directories."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


@dataclass(frozen=True)
class ManagedPathInfo:
    path: Path
    modified_at: datetime
    size_bytes: int


def path_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return int(path.stat().st_size)
        except OSError:
            return 0

    total = 0
    for child in path.rglob("*"):
        if not child.is_file():
            continue
        try:
            total += int(child.stat().st_size)
        except OSError:
            continue
    return total


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return
    path.unlink(missing_ok=True)


def prune_managed_paths(
    candidates: list[ManagedPathInfo],
    *,
    max_age_days: int | None = None,
    max_total_size_bytes: int | None = None,
    protected_count: int = 0,
    protected_paths: set[Path] | None = None,
    now: datetime | None = None,
    remove_func=remove_path,
) -> list[Path]:
    if not candidates:
        return []

    protected_paths = {path.resolve() for path in (protected_paths or set())}
    newest = sorted(candidates, key=lambda item: item.modified_at, reverse=True)
    for item in newest[: max(0, int(protected_count))]:
        protected_paths.add(item.path.resolve())

    removed: list[Path] = []

    def _remove(item: ManagedPathInfo) -> None:
        resolved = item.path.resolve()
        if resolved in protected_paths or item.path in removed:
            return
        remove_func(item.path)
        removed.append(item.path)

    if max_age_days is not None:
        cutoff = (now or datetime.now()) - timedelta(days=max_age_days)
        for item in sorted(candidates, key=lambda entry: entry.modified_at):
            if item.path.resolve() in protected_paths:
                continue
            if item.modified_at <= cutoff:
                _remove(item)

    if max_total_size_bytes is not None and max_total_size_bytes > 0:
        remaining = [
            item
            for item in candidates
            if item.path.exists() and item.path not in removed
        ]
        total_size = sum(item.size_bytes for item in remaining)
        if total_size > max_total_size_bytes:
            for item in sorted(remaining, key=lambda entry: entry.modified_at):
                if item.path.resolve() in protected_paths:
                    continue
                _remove(item)
                total_size -= item.size_bytes
                if total_size <= max_total_size_bytes:
                    break

    return removed
