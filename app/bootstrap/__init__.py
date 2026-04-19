"""런타임 시작/조립 경계를 모아두는 패키지."""

from app.bootstrap.cli import parse_args, resolve_ui_mode, validate_startup_args
from app.bootstrap.helpers import (
    AsyncHotkeyAction,
    format_peer_reject_notice,
    install_capture_hotkey_fallbacks,
    install_cursor_cleanup_hooks,
    notify_runtime_message,
    runtime_log_dir,
    start_local_input_services,
    start_local_input_services_async,
)
from app.bootstrap.session import RuntimeSession

__all__ = [
    "AsyncHotkeyAction",
    "RuntimeSession",
    "format_peer_reject_notice",
    "install_capture_hotkey_fallbacks",
    "install_cursor_cleanup_hooks",
    "notify_runtime_message",
    "parse_args",
    "resolve_ui_mode",
    "runtime_log_dir",
    "start_local_input_services",
    "start_local_input_services_async",
    "validate_startup_args",
]
