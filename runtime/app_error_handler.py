"""Friendly process-wide handlers for otherwise unhandled exceptions."""

from __future__ import annotations

import atexit
import ctypes
import logging
import sys
import threading
from pathlib import Path


def _run_cleanup_actions(cleanup_actions) -> None:
    for action in cleanup_actions:
        try:
            action()
        except Exception as exc:
            logging.warning("[ERROR HANDLER] cleanup action failed during exception handling: %s", exc)


def _exception_summary(exc_type, exc_value) -> str:
    summary = str(exc_value).strip()
    if summary:
        return f"{exc_type.__name__}: {summary}"
    return exc_type.__name__


def build_user_friendly_error_message(
    *,
    app_name: str,
    exc_type,
    exc_value,
    log_path: str | Path | None = None,
) -> str:
    lines = [
        f"{app_name}에서 예기치 않은 오류가 발생했습니다.",
        "",
        "앱을 다시 실행해 주세요.",
        "문제가 반복되면 아래 정보를 함께 전달해 주세요.",
        "",
        f"오류: {_exception_summary(exc_type, exc_value)}",
    ]
    if log_path is not None:
        lines.extend(
            [
                "",
                f"로그 파일: {Path(log_path)}",
            ]
        )
    return "\n".join(lines)


def _show_qt_error_dialog(title: str, message: str) -> bool:
    try:
        from PySide6.QtCore import QThread
        from PySide6.QtWidgets import QApplication, QMessageBox
    except Exception:
        return False

    app = QApplication.instance()
    if app is None:
        return False
    if QThread.currentThread() != app.thread():
        return False

    try:
        QMessageBox.critical(None, title, message)
        return True
    except Exception as exc:
        logging.debug("[ERROR HANDLER] Qt error dialog failed: %s", exc)
        return False


def _show_windows_error_dialog(title: str, message: str) -> bool:
    try:
        user32 = ctypes.windll.user32
    except Exception:
        return False
    try:
        user32.MessageBoxW(None, message, title, 0x00000010)
        return True
    except Exception as exc:
        logging.debug("[ERROR HANDLER] MessageBoxW failed: %s", exc)
        return False


def show_user_friendly_error_dialog(
    *,
    app_name: str,
    exc_type,
    exc_value,
    log_path: str | Path | None = None,
) -> bool:
    title = f"{app_name} 오류"
    message = build_user_friendly_error_message(
        app_name=app_name,
        exc_type=exc_type,
        exc_value=exc_value,
        log_path=log_path,
    )
    if _show_qt_error_dialog(title, message):
        return True
    if _show_windows_error_dialog(title, message):
        return True
    try:
        sys.stderr.write(message + "\n")
        sys.stderr.flush()
        return True
    except Exception:
        return False


def install_unhandled_exception_handler(
    *,
    app_name: str,
    cleanup_actions=(),
    log_path: str | Path | None = None,
    delegate_previous: bool = False,
) -> None:
    cleanup_actions = tuple(cleanup_actions)
    shown_lock = threading.Lock()
    state = {"shown": False}

    def _handle_exception(exc_type, exc_value, traceback_obj, *, previous_hook=None, thread_args=None) -> None:
        _run_cleanup_actions(cleanup_actions)
        try:
            logging.critical(
                "[ERROR HANDLER] unhandled exception",
                exc_info=(exc_type, exc_value, traceback_obj),
            )
        except Exception:
            pass

        should_show = False
        with shown_lock:
            if not state["shown"]:
                state["shown"] = True
                should_show = True

        if should_show:
            try:
                show_user_friendly_error_dialog(
                    app_name=app_name,
                    exc_type=exc_type,
                    exc_value=exc_value,
                    log_path=log_path,
                )
            except Exception as exc:
                logging.debug("[ERROR HANDLER] failed to show user-friendly error dialog: %s", exc)

        if delegate_previous and previous_hook is not None:
            if thread_args is not None:
                previous_hook(thread_args)
            else:
                previous_hook(exc_type, exc_value, traceback_obj)

    atexit.register(lambda: _run_cleanup_actions(cleanup_actions))

    previous_sys_excepthook = sys.excepthook

    def _sys_excepthook(exc_type, exc_value, traceback_obj):
        _handle_exception(
            exc_type,
            exc_value,
            traceback_obj,
            previous_hook=previous_sys_excepthook,
        )

    sys.excepthook = _sys_excepthook

    previous_thread_excepthook = getattr(threading, "excepthook", None)
    if previous_thread_excepthook is not None:

        def _thread_excepthook(args):
            _handle_exception(
                args.exc_type,
                args.exc_value,
                args.exc_traceback,
                previous_hook=previous_thread_excepthook,
                thread_args=args,
            )

        threading.excepthook = _thread_excepthook
