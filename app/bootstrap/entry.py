"""multi-controller 실행 진입점."""

import json
import logging
import signal
import sys
import threading

from control.routing.display_state import DisplayStateTracker
from app.logging.app_error_handler import (
    install_unhandled_exception_handler,
    show_user_friendly_error_dialog,
)
from app.logging.app_logging import TAG_LOG, TAG_PEER, TAG_SELF, tag_message
from app.config.app_settings import AppSettings, load_app_settings
from app.bootstrap.cli import parse_args, resolve_ui_mode, validate_startup_args
from app.bootstrap.helpers import (
    AsyncHotkeyAction,
    format_peer_reject_notice,
    install_capture_hotkey_fallbacks as _install_capture_hotkey_fallbacks,
    notify_runtime_message as _notify_runtime_message,
    runtime_log_dir as _runtime_log_dir,
    start_local_input_services as _start_local_input_services,
    start_local_input_services_async as _start_local_input_services_async,
)
from app.bootstrap.session import RuntimeSession
from msp_platform.windows.clip_recovery import release_input_guards
from app.config.config_loader import (
    ensure_runtime_config,
    init_config,
    migrate_config,
    related_config_paths,
    validate_config_file,
)
from control.state.context import build_runtime_context
from app.diagnostics.diagnostics import build_runtime_diagnostics, format_runtime_diagnostics
from model.display.display import (
    enable_best_effort_dpi_awareness,
    get_primary_screen_bounds,
    get_virtual_screen_bounds,
)
from app.diagnostics.layout_diagnostics import build_layout_diagnostics
from msp_platform.windows.windows_interaction import log_windows_interaction_diagnostics
from app.logging.logger_setup import setup_logging

__all__ = [
    "AsyncHotkeyAction",
    "format_peer_reject_notice",
    "main",
    "parse_args",
    "resolve_ui_mode",
    "run_main",
    "validate_startup_args",
    "_build_target_primary_center_anchor",
    "_emit_requested_diagnostics",
    "_handle_config_commands",
    "_host_cursor_parking_point",
    "_install_capture_hotkey_fallbacks",
    "_install_cursor_cleanup_hooks",
    "_log_runtime_context",
    "_notify_runtime_message",
    "_park_local_cursor_for_active_target",
    "_restore_local_cursor_after_target_exit",
    "_runtime_log_dir",
    "_setup_runtime_logging",
    "_start_local_input_services",
    "_start_local_input_services_async",
    "_target_primary_display_id",
]


def _install_cursor_cleanup_hooks(*cleanup_actions, log_path=None):
    install_unhandled_exception_handler(
        app_name="Multi Screen Pass",
        cleanup_actions=cleanup_actions,
        log_path=log_path,
        delegate_previous=not getattr(sys, "frozen", False),
    )


def _host_cursor_parking_point(ctx):
    snapshot = ctx.get_monitor_inventory(ctx.self_node.node_id)
    if snapshot is not None and snapshot.monitors:
        primary = next((item for item in snapshot.monitors if item.is_primary), None)
        chosen = primary or snapshot.ordered()[0]
        width = max(int(chosen.bounds.width), 1)
        height = max(int(chosen.bounds.height), 1)
        return (
            int(chosen.bounds.left) + max(width - 1, 0) // 2,
            int(chosen.bounds.top) + max(height - 1, 0) // 2,
        )
    bounds = get_primary_screen_bounds()
    return (
        int(bounds.left) + max(int(bounds.width) - 1, 0) // 2,
        int(bounds.top) + max(int(bounds.height) - 1, 0) // 2,
    )


def _target_primary_display_id(ctx, target_id: str) -> str | None:
    layout = ctx.layout
    if layout is None:
        return None
    node = layout.get_node(target_id)
    if node is None:
        return None
    snapshot = ctx.get_monitor_inventory(target_id)
    if snapshot is not None and snapshot.monitors:
        primary = next((item for item in snapshot.monitors if item.is_primary), None)
        chosen = primary or snapshot.ordered()[0]
        return chosen.monitor_id
    logical = node.monitors().logical
    if logical:
        return logical[0].display_id
    physical = node.monitors().physical
    if physical:
        return physical[0].display_id
    return None


def _build_target_primary_center_anchor(ctx, target_id: str):
    layout = ctx.layout
    if layout is None or target_id == ctx.self_node.node_id:
        return None
    node = layout.get_node(target_id)
    if node is None:
        return None
    display_id = _target_primary_display_id(ctx, target_id)
    if not display_id:
        return None
    tracker = DisplayStateTracker(ctx)
    bounds = tracker.node_screen_bounds(target_id, node, get_virtual_screen_bounds())
    return tracker.build_display_center_event(node, display_id, bounds)


def _park_local_cursor_for_active_target(local_cursor, ctx):
    x, y = _host_cursor_parking_point(ctx)
    moved = local_cursor.move(x, y)
    cleared = local_cursor.clear_clip()
    return bool(moved and cleared)


def _restore_local_cursor_after_target_exit(router, local_cursor, ctx):
    cleared = local_cursor.clear_clip()
    anchor_event = None
    if hasattr(router, "consume_local_return_anchor_event"):
        anchor_event = router.consume_local_return_anchor_event()
    if anchor_event is not None and "x" in anchor_event and "y" in anchor_event:
        moved = local_cursor.move(int(anchor_event["x"]), int(anchor_event["y"]))
        return bool(cleared and moved)
    x, y = _host_cursor_parking_point(ctx)
    moved = local_cursor.move(x, y)
    return bool(cleared and moved)


def _handle_config_commands(args) -> bool:
    if args.init_config:
        path = init_config(args.config, overwrite=args.force)
        sys.stdout.write(f"initialized starter config at {path}\n")
        return True
    if args.migrate_config:
        source, destination = migrate_config(args.config, overwrite=args.force)
        sys.stdout.write(f"migrated config from {source} to {destination}\n")
        return True
    if args.validate_config:
        _config, resolved_path = validate_config_file(args.config)
        paths = related_config_paths(resolved_path)
        sys.stdout.write(
            json.dumps(
                {key: str(value) for key, value in paths.items()},
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        return True
    return False


def _emit_requested_diagnostics(args, ctx=None) -> bool:
    if not args.diagnostics and not args.layout_diagnostics:
        return False
    payload = {}
    if args.diagnostics:
        payload["runtime"] = build_runtime_diagnostics()
    if args.layout_diagnostics:
        payload["layout"] = build_layout_diagnostics(ctx)
    if len(payload) == 1 and "runtime" in payload:
        sys.stdout.write(format_runtime_diagnostics(payload["runtime"]) + "\n")
    elif len(payload) == 1 and "layout" in payload:
        sys.stdout.write(json.dumps(payload["layout"], ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return True


def _setup_runtime_logging(args, config_path, settings):
    log_path = setup_logging(
        debug=args.debug,
        log_dir=_runtime_log_dir(config_path),
        retention_days=settings.logs.retention_days,
        max_total_size_mb=settings.logs.max_total_size_mb,
    )
    if args.debug:
        logging.debug(tag_message(TAG_LOG, "verbose logging enabled"))
        if log_path is not None:
            logging.debug(tag_message(TAG_LOG, "writing log file to %s"), log_path)
    return log_path


def _log_runtime_context(ctx) -> None:
    self_label = (
        ctx.self_node.display_label()
        if hasattr(ctx.self_node, "display_label") and callable(ctx.self_node.display_label)
        else ctx.self_node.label()
    )
    logging.info(tag_message(TAG_SELF, "%s"), self_label)
    if not ctx.peers:
        logging.warning(tag_message(TAG_PEER, "no peers configured; node will only receive local state"))
    for peer in ctx.peers:
        peer_label = peer.display_label() if hasattr(peer, "display_label") and callable(peer.display_label) else peer.label()
        logging.info(tag_message(TAG_PEER, "%s"), peer_label)


def main():
    args = parse_args()
    if _handle_config_commands(args):
        return

    if args.diagnostics and not args.layout_diagnostics:
        default_settings = AppSettings()
        setup_logging(
            debug=args.debug,
            log_dir=_runtime_log_dir(None),
            retention_days=default_settings.logs.retention_days,
            max_total_size_mb=default_settings.logs.max_total_size_mb,
        )
        if args.debug:
            logging.debug(tag_message(TAG_LOG, "verbose logging enabled"))
        enable_best_effort_dpi_awareness()
        release_input_guards()
        log_windows_interaction_diagnostics()
        _emit_requested_diagnostics(args)
        return

    config, config_path = ensure_runtime_config(args.config)
    loaded_settings = load_app_settings(config)
    log_path = _setup_runtime_logging(args, config_path, loaded_settings)
    enable_best_effort_dpi_awareness()
    release_input_guards()
    log_windows_interaction_diagnostics()

    ctx = build_runtime_context(
        config,
        override_name=None,
        config_path=config_path,
    )
    if _emit_requested_diagnostics(args, ctx):
        return

    validate_startup_args(ctx, args.active_target)
    _log_runtime_context(ctx)

    shutdown_evt = threading.Event()

    def _handle_signal(*_args):
        shutdown_evt.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    session = RuntimeSession(
        ctx,
        active_target=args.active_target,
        status_interval=args.status_interval,
        ui_mode=resolve_ui_mode(args),
        shutdown_evt=shutdown_evt,
        log_path=log_path,
    )
    session.run_forever()


def run_main() -> None:
    try:
        main()
    except Exception as exc:
        try:
            release_input_guards()
        except Exception:
            pass
        if getattr(sys, "frozen", False):
            show_user_friendly_error_dialog(
                app_name="Multi Screen Pass",
                exc_type=type(exc),
                exc_value=exc,
                log_path=None,
            )
            sys.exit(1)
        raise


if __name__ == "__main__":
    run_main()
