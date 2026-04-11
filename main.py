"""multi-controller 실행 진입점."""

import argparse
import atexit
import json
import logging
import os
import queue
import signal
import sys
import threading
import time

from core.events import make_system_event
from coordinator.client import CoordinatorClient
from coordinator.election import pick_coordinator
from coordinator.service import CoordinatorService
from network.dispatcher import FrameDispatcher
from network.peer_dialer import PeerDialer
from network.peer_registry import PeerRegistry
from network.peer_server import PeerServer
from routing.router import InputRouter
from routing.sink import InputSink
from runtime.app_settings import hotkey_to_matcher_parts
from runtime.config_loader import (
    init_config,
    load_config,
    migrate_config,
    related_config_paths,
    validate_config_file,
)
from runtime.config_reloader import RuntimeConfigReloader
from runtime.context import build_runtime_context
from runtime.clip_recovery import release_cursor_clip, spawn_clip_watchdog
from runtime.diagnostics import build_runtime_diagnostics, format_runtime_diagnostics
from runtime.layout_diagnostics import build_layout_diagnostics
from runtime.layouts import replace_auto_switch_settings
from runtime.local_cursor import LocalCursorController
from runtime.monitor_inventory_manager import MonitorInventoryManager
from runtime.qt_app import QtRuntimeApp
from runtime.state_watcher import StateWatcher
from runtime.status_reporter import StatusReporter
from runtime.synthetic_input import SyntheticInputGuard
from runtime.windows_interaction import log_windows_interaction_diagnostics
from utils.logger_setup import setup_logging


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="multi-controller: shared keyboard and mouse control"
    )
    parser.add_argument(
        "--node-name",
        help="Override auto-detected self node with config.nodes[].name.",
    )
    parser.add_argument(
        "--config",
        help="Path to config/config.json. Defaults to bundled/project/CWD discovery with legacy root fallback.",
    )
    parser.add_argument(
        "--init-config",
        action="store_true",
        help="Create a starter split config and exit.",
    )
    parser.add_argument(
        "--migrate-config",
        action="store_true",
        help="Load the current config and rewrite it into the split config/ structure.",
    )
    parser.add_argument(
        "--validate-config",
        action="store_true",
        help="Load and validate the current config, then print the resolved file layout.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow init/migrate commands to overwrite existing files.",
    )
    parser.add_argument(
        "--active-target",
        help="Set an initial target at startup.",
    )
    parser.add_argument(
        "--status-interval",
        type=float,
        default=10.0,
        help="Seconds between periodic status logs. Use 0 to disable.",
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="Print local Windows privilege/display diagnostics and exit.",
    )
    parser.add_argument(
        "--layout-diagnostics",
        action="store_true",
        help="Print resolved PC layout, monitor topology, and auto-switch diagnostics and exit.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging for troubleshooting.",
    )
    ui_group = parser.add_mutually_exclusive_group()
    ui_group.add_argument(
        "--gui",
        action="store_true",
        help="Open the status window explicitly. This is now the default unless --console or --tray is used.",
    )
    ui_group.add_argument(
        "--console",
        action="store_true",
        help="Run without the status window. Use this for log-first or console-only operation.",
    )
    ui_group.add_argument(
        "--tray",
        action="store_true",
        help="Run a system tray icon for coordinator/target state and quick actions.",
    )
    return parser.parse_args(argv)


def validate_startup_args(ctx, active_target):
    if not active_target:
        return
    target = ctx.get_node(active_target)
    if target is None:
        raise ValueError(f"--active-target '{active_target}' is not defined in config.nodes")
    if not target.has_role("target"):
        raise ValueError(f"--active-target '{active_target}' does not have the target role")
    if target.node_id == ctx.self_node.node_id:
        raise ValueError("--active-target cannot point to self")


def resolve_ui_mode(args):
    """실행 인자에 따라 사용할 UI 모드를 결정한다."""
    if args.tray:
        return "tray"
    if args.console:
        return "console"
    return "gui"


def _install_cursor_cleanup_hooks(release_clip):
    def _safe_release():
        try:
            release_clip()
        except Exception as exc:
            logging.warning("[CURSOR] failed to clear clip during exception cleanup: %s", exc)

    atexit.register(_safe_release)

    previous_sys_excepthook = sys.excepthook

    def _sys_excepthook(exc_type, exc_value, traceback):
        _safe_release()
        previous_sys_excepthook(exc_type, exc_value, traceback)

    sys.excepthook = _sys_excepthook

    previous_thread_excepthook = getattr(threading, "excepthook", None)
    if previous_thread_excepthook is not None:

        def _thread_excepthook(args):
            _safe_release()
            previous_thread_excepthook(args)

        threading.excepthook = _thread_excepthook


def main():
    args = parse_args()
    if args.init_config:
        path = init_config(args.config, overwrite=args.force)
        sys.stdout.write(f"initialized starter config at {path}\n")
        return
    if args.migrate_config:
        source, destination = migrate_config(args.config, overwrite=args.force)
        sys.stdout.write(f"migrated config from {source} to {destination}\n")
        return
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
        return

    setup_logging(debug=args.debug)
    if args.debug:
        logging.debug("[DEBUG] verbose logging enabled")
    release_cursor_clip()
    log_windows_interaction_diagnostics()

    if args.diagnostics and not args.layout_diagnostics:
        sys.stdout.write(format_runtime_diagnostics(build_runtime_diagnostics()) + "\n")
        return

    config, config_path = load_config(args.config)
    ctx = build_runtime_context(
        config,
        override_name=args.node_name,
        config_path=config_path,
    )
    if args.diagnostics or args.layout_diagnostics:
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
        return

    validate_startup_args(ctx, args.active_target)

    logging.info("[SELF] %s roles=%s", ctx.self_node.label(), list(ctx.self_node.roles))
    if not ctx.peers:
        logging.warning("[PEERS] no peers configured; node will only receive local state")
    for peer in ctx.peers:
        logging.info("[PEER] %s roles=%s", peer.label(), list(peer.roles))

    shutdown_evt = threading.Event()

    def _handle_signal(*_args):
        shutdown_evt.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    registry = PeerRegistry()
    dispatcher = FrameDispatcher()

    def coordinator_resolver():
        return pick_coordinator(ctx, registry)
    synthetic_guard = None
    if ctx.self_node.has_role("controller") or ctx.self_node.has_role("target"):
        synthetic_guard = SyntheticInputGuard()

    sink = None
    if ctx.self_node.has_role("target"):
        try:
            from injection.os_injector import PynputOSInjector

            injector = PynputOSInjector(synthetic_guard=synthetic_guard)
            logging.info("[INJECTOR] pynput OS injection enabled")
        except Exception as exc:
            from injection.os_injector import LoggingOSInjector

            injector = LoggingOSInjector()
            logging.warning(
                "[INJECTOR] pynput unavailable (%s); using logging injector",
                exc,
            )
        sink = InputSink(
            injector=injector,
            require_authorization=True,
        )
        dispatcher.set_input_handler(sink.handle)
        registry.add_unbind_listener(sink.release_peer)

    server = PeerServer(ctx, registry, dispatcher)
    dialer = PeerDialer(ctx, registry, dispatcher)

    capture = None
    capture_queue = None
    router = None
    router_thread = None
    local_cursor = None
    if ctx.self_node.has_role("controller"):
        from capture.input_capture import InputCapture
        from routing.auto_switch import AutoTargetSwitcher

        capture_queue = queue.Queue()
        capture = InputCapture(capture_queue, synthetic_guard=synthetic_guard)
        router = InputRouter(ctx, registry)
        router_thread = threading.Thread(
            target=router.run,
            args=(capture_queue,),
            daemon=True,
            name="input-router",
        )

    coord_service = CoordinatorService(ctx, registry, dispatcher)
    coord_client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=coordinator_resolver,
        router=router,
        sink=sink,
    )
    monitor_inventory_manager = MonitorInventoryManager(
        ctx,
        coord_client=coord_client,
    )
    coord_client.set_monitor_inventory_manager(monitor_inventory_manager)
    if router is not None:
        local_cursor = LocalCursorController(synthetic_guard=synthetic_guard)
        _install_cursor_cleanup_hooks(local_cursor.clear_clip)
        spawn_clip_watchdog(os.getpid())
        auto_switcher = AutoTargetSwitcher(
            ctx,
            router,
            request_target=coord_client.request_target,
            clear_target=coord_client.clear_target,
            is_target_online=lambda node_id: (
                (conn := registry.get(node_id)) is not None and not conn.closed
            ),
            pointer_mover=local_cursor.move,
            actual_pointer_provider=local_cursor.position,
            pointer_clipper=local_cursor,
        )
        if capture is not None:
            capture.move_processor = auto_switcher.process
            capture.pointer_state_refresher = auto_switcher.refresh_self_clip
    status_reporter = StatusReporter(
        ctx,
        registry,
        coordinator_resolver,
        router=router,
        sink=sink,
        interval_sec=args.status_interval,
    )
    state_watcher = StateWatcher(
        ctx,
        registry,
        coordinator_resolver,
        router=router,
        sink=sink,
    )
    qt_runtime_app = None
    ui_mode = resolve_ui_mode(args)
    config_reloader = RuntimeConfigReloader(
        ctx,
        dialer=dialer,
        router=router,
        coord_client=coord_client,
    )
    coord_client.set_config_reloader(config_reloader)
    monitor_inventory_manager.config_reloader = config_reloader
    config_reloader.start_periodic_backup_pruning()
    if ui_mode in {"gui", "tray"}:
        qt_runtime_app = QtRuntimeApp(
            ctx=ctx,
            registry=registry,
            coordinator_resolver=coordinator_resolver,
            router=router,
            sink=sink,
            coord_client=coord_client,
            config_reloader=config_reloader,
            monitor_inventory_manager=monitor_inventory_manager,
            ui_mode=ui_mode,
        )

    if capture is not None and router is not None:
        from capture.hotkey import HotkeyMatcher, TargetCycler

        def _notify_tray(message: str) -> None:
            if qt_runtime_app is not None:
                qt_runtime_app.request_tray_notification(message)

        def _online_target_ids():
            online_ids = [
                node_id
                for node_id, conn in registry.all()
                if conn is not None and not conn.closed
            ]
            return [
                node_id
                for node_id in online_ids
                if (node := ctx.get_node(node_id)) is not None and node.has_role("target")
            ]

        cycler = TargetCycler(
            ctx,
            router,
            coord_client=coord_client,
            targets_provider=_online_target_ids,
        )
        previous_modifiers, previous_trigger = hotkey_to_matcher_parts(
            ctx.settings.hotkeys.previous_target
        )
        next_modifiers, next_trigger = hotkey_to_matcher_parts(ctx.settings.hotkeys.next_target)
        toggle_modifiers, toggle_trigger = hotkey_to_matcher_parts(
            ctx.settings.hotkeys.toggle_auto_switch
        )
        quit_modifiers, quit_trigger = hotkey_to_matcher_parts(ctx.settings.hotkeys.quit_app)

        def _cycle_previous():
            current = router.get_selected_target()
            next_id = cycler.previous()
            if next_id is None:
                _notify_tray("PC 전환: 가능한 온라인 PC 없음")
            elif next_id == current:
                _notify_tray(f"PC 전환: {next_id} 이미 선택됨")
            else:
                _notify_tray(f"PC 전환: {next_id}")

        def _cycle_next():
            current = router.get_selected_target()
            next_id = cycler.next()
            if next_id is None:
                _notify_tray("PC 전환: 가능한 온라인 PC 없음")
            elif next_id == current:
                _notify_tray(f"PC 전환: {next_id} 이미 선택됨")
            else:
                _notify_tray(f"PC 전환: {next_id}")

        def _toggle_auto_switch():
            if ctx.layout is None:
                return
            enabled = not ctx.layout.auto_switch.enabled
            next_layout = replace_auto_switch_settings(ctx.layout, enabled=enabled)
            ctx.replace_layout(next_layout)
            if config_reloader is not None:
                try:
                    config_reloader.apply_layout(next_layout, persist=True, debounce_persist=False)
                except Exception as exc:
                    logging.warning("[HOTKEY] failed to persist auto switch toggle: %s", exc)
            logging.info(
                "[HOTKEY] %s %s auto boundary switching",
                ctx.settings.hotkeys.toggle_auto_switch,
                "enabled" if enabled else "disabled",
            )
            capture.put_event(
                make_system_event(
                    f"{ctx.settings.hotkeys.toggle_auto_switch} toggled auto boundary switching "
                    f"{'on' if enabled else 'off'}"
                )
            )
            _notify_tray(f"자동 경계 전환: {'ON' if enabled else 'OFF'}")

        def _quit_application():
            logging.info("[HOTKEY] %s quitting application", ctx.settings.hotkeys.quit_app)
            capture.put_event(make_system_event(f"{ctx.settings.hotkeys.quit_app} input detected, quitting app"))
            _notify_tray("앱 종료")
            shutdown_evt.set()
            if qt_runtime_app is not None:
                qt_runtime_app.request_quit()
            else:
                capture.stop()

        capture.hotkey_matchers.append(
            HotkeyMatcher(
                modifier_groups=previous_modifiers,
                trigger=previous_trigger,
                callback=_cycle_previous,
                name="cycle-target-prev",
            )
        )
        capture.hotkey_matchers.append(
            HotkeyMatcher(
                modifier_groups=next_modifiers,
                trigger=next_trigger,
                callback=_cycle_next,
                name="cycle-target-next",
            )
        )
        capture.hotkey_matchers.append(
            HotkeyMatcher(
                modifier_groups=toggle_modifiers,
                trigger=toggle_trigger,
                callback=_toggle_auto_switch,
                name="toggle-auto-switch",
            )
        )
        capture.hotkey_matchers.append(
            HotkeyMatcher(
                modifier_groups=quit_modifiers,
                trigger=quit_trigger,
                callback=_quit_application,
                name="quit-application",
            )
        )
        logging.info("[HOTKEY] %s selects previous target", ctx.settings.hotkeys.previous_target)
        logging.info("[HOTKEY] %s selects next target", ctx.settings.hotkeys.next_target)
        logging.info(
            "[HOTKEY] %s toggles auto boundary switching",
            ctx.settings.hotkeys.toggle_auto_switch,
        )
        logging.info("[HOTKEY] %s quits the application", ctx.settings.hotkeys.quit_app)

    server.start()
    dialer.start()
    coord_service.start()
    coord_client.start()
    monitor_inventory_manager.refresh()
    state_watcher.start()
    status_reporter.start()
    if router_thread is not None:
        router_thread.start()
    if capture is not None:
        capture.start()

    if args.active_target and router is not None:
        coord_client.request_target(args.active_target)

    try:
        if qt_runtime_app is not None and shutdown_evt.is_set():
            qt_runtime_app = None
        if qt_runtime_app is not None:
            try:
                qt_runtime_app.run(shutdown_evt.set)
            except Exception as exc:
                logging.warning("[GUI] Qt runtime UI failed: %s", exc)
                qt_runtime_app = None

        if qt_runtime_app is None and capture is not None:
            while not shutdown_evt.is_set() and capture.running:
                shutdown_evt.wait(timeout=0.2)
        elif qt_runtime_app is None:
            shutdown_evt.wait()
    finally:
        logging.info("[SHUTDOWN] stopping")
        if local_cursor is not None:
            local_cursor.clear_clip()
        config_reloader.stop_periodic_backup_pruning()
        try:
            config_reloader.flush_pending_layout()
        except Exception as exc:
            logging.warning("[CONFIG] failed to flush pending layout on shutdown: %s", exc)
        if capture is not None:
            capture.stop()
        if router is not None:
            router.stop()
        if capture_queue is not None:
            capture_queue.put({"kind": "system", "message": "shutdown"})
        status_reporter.stop()
        state_watcher.stop()
        coord_client.stop()
        coord_service.stop()
        dialer.stop()
        server.stop()
        registry.close_all()
        time.sleep(0.1)
        logging.info("[EXIT] main stopped")


if __name__ == "__main__":
    main()

