"""Cursor clip recovery helpers for startup, watchdog, and manual tools."""

from __future__ import annotations

import ctypes
import logging
from pathlib import Path
import subprocess
import sys
import time

from runtime.app_identity import RECOVERY_EXECUTABLE_NAME


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
RECOVERY_EXECUTABLE_FILENAMES = (
    f"{RECOVERY_EXECUTABLE_NAME}.exe",
    "\ub9c8\uc6b0\uc2a4_\uc7a0\uae08_\ud574\uc81c.exe",
)


def release_cursor_clip(user32=None) -> bool:
    raw_user32 = user32
    if raw_user32 is None:
        try:
            raw_user32 = ctypes.windll.user32
        except Exception as exc:
            logging.debug("[CURSOR] user32 unavailable for recovery release: %s", exc)
            return False
    try:
        success = bool(raw_user32.ClipCursor(None))
        logging.debug("[CURSOR] recovery ClipCursor clear success=%s", success)
        return success
    except Exception as exc:
        logging.warning("[CURSOR] recovery ClipCursor clear failed: %s", exc)
        return False


def is_process_alive(pid: int, kernel32=None) -> bool:
    try:
        process_id = int(pid)
    except (TypeError, ValueError):
        return False
    if process_id <= 0:
        return False
    raw_kernel32 = kernel32
    if raw_kernel32 is None:
        try:
            raw_kernel32 = ctypes.windll.kernel32
        except Exception:
            return False
    handle = raw_kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not raw_kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return int(exit_code.value) == STILL_ACTIVE
    finally:
        raw_kernel32.CloseHandle(handle)


def wait_for_parent_exit(
    parent_pid: int,
    *,
    poll_interval: float = 0.25,
    is_alive_fn=None,
    sleep_fn=None,
    on_parent_exit=None,
) -> None:
    alive = is_alive_fn or is_process_alive
    sleeper = sleep_fn or time.sleep
    while alive(parent_pid):
        sleeper(max(float(poll_interval), 0.01))
    if callable(on_parent_exit):
        on_parent_exit()


def resolve_mouse_unlock_executable_path(root: Path) -> Path | None:
    for filename in RECOVERY_EXECUTABLE_FILENAMES:
        exe_path = root / filename
        if exe_path.exists():
            return exe_path
    return None


def resolve_mouse_unlock_tool_command(root_dir: str | Path | None = None) -> list[str]:
    root = Path(root_dir) if root_dir is not None else Path(__file__).resolve().parents[1]
    exe_path = resolve_mouse_unlock_executable_path(root)
    script_path = root / "scripts" / "mouse_unlock_tool.py"
    if getattr(sys, "frozen", False) and exe_path is not None:
        return [str(exe_path)]
    if script_path.exists():
        return [sys.executable, str(script_path)]
    if exe_path is not None:
        return [str(exe_path)]
    return [sys.executable, str(script_path)]


def spawn_clip_watchdog(
    parent_pid: int,
    *,
    root_dir: str | Path | None = None,
) -> subprocess.Popen | None:
    command = resolve_mouse_unlock_tool_command(root_dir)
    command.extend(
        [
            "--watch-parent",
            str(int(parent_pid)),
            "--poll-interval",
            "0.25",
            "--quiet",
        ]
    )
    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
            close_fds=True,
            cwd=str(Path(root_dir) if root_dir is not None else Path(__file__).resolve().parents[1]),
        )
        logging.debug("[CURSOR] clip watchdog started pid=%s command=%s", proc.pid, command)
        return proc
    except Exception as exc:
        logging.warning("[CURSOR] failed to start clip watchdog: %s", exc)
        return None
