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
from pathlib import Path

from core.events import make_mouse_move_event, make_system_event
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
    ensure_runtime_config,
    init_config,
    migrate_config,
    related_config_paths,
    validate_config_file,
)
from runtime.config_reloader import RuntimeConfigReloader
from runtime.context import build_runtime_context
from runtime.clip_recovery import release_cursor_clip, spawn_clip_watchdog
from runtime.display import enable_best_effort_dpi_awareness, enrich_pointer_event, get_virtual_screen_bounds
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
    if target.node_id == ctx.self_node.node_id:
        raise ValueError("--active-target cannot point to self")


def resolve_ui_mode(args):
    """실행 인자에 따라 사용할 UI 모드를 결정한다."""
    if args.tray:
        return "tray"
    if args.console:
        return "console"
    return "gui"


def _install_cursor_cleanup_hooks(*cleanup_actions):
    def _safe_release():
        for action in cleanup_actions:
            try:
                action()
            except Exception as exc:
                logging.warning("[CURSOR] cleanup action failed during exception cleanup: %s", exc)

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

    if args.diagnostics and not args.layout_diagnostics:
        setup_logging(debug=args.debug)
        if args.debug:
            logging.debug("[DEBUG] verbose logging enabled")
        enable_best_effort_dpi_awareness()
        release_cursor_clip()
        log_windows_interaction_diagnostics()
        sys.stdout.write(format_runtime_diagnostics(build_runtime_diagnostics()) + "\n")
        return

    config, config_path = ensure_runtime_config(args.config, override_name=args.node_name)
    debug_log_dir = None
    if args.debug:
        if getattr(sys, "frozen", False):
            debug_log_dir = Path(sys.executable).resolve().parent / "logs"
        else:
            debug_log_dir = related_config_paths(config_path)["config"].parent / "logs"
    log_path = setup_logging(
        debug=args.debug,
        log_dir=debug_log_dir,
    )
    if args.debug:
        logging.debug("[DEBUG] verbose logging enabled")
        if log_path is not None:
            logging.debug("[DEBUG] writing log file to %s", log_path)
    enable_best_effort_dpi_awareness()
    release_cursor_clip()
    log_windows_interaction_diagnostics()

    effective_override = None
    if args.node_name and any(
        isinstance(node, dict) and node.get("name") == args.node_name
        for node in config.get("nodes", [])
    ):
        effective_override = args.node_name
    ctx = build_runtime_context(
        config,
        override_name=effective_override,
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

    logging.info("[SELF] %s", ctx.self_node.label())
    if not ctx.peers:
        logging.warning("[PEERS] no peers configured; node will only receive local state")
    for peer in ctx.peers:
        logging.info("[PEER] %s", peer.label())

    shutdown_evt = threading.Event()

    def _handle_signal(*_args):
        shutdown_evt.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    registry = PeerRegistry()
    dispatcher = FrameDispatcher()

    def coordinator_resolver():
        return pick_coordinator(ctx, registry)
    synthetic_guard = SyntheticInputGuard()

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
    from capture.input_capture import InputCapture
    from routing.auto_switch import AutoTargetSwitcher

    capture_queue = queue.Queue()
    capture = InputCapture(
        capture_queue,
        synthetic_guard=synthetic_guard,
        mouse_block_predicate=lambda kind, event: (
            router is not None
            and router.get_target_state() == "active"
            and kind in {"mouse_move", "mouse_button", "mouse_wheel"}
        ),
        keyboard_block_predicate=lambda kind, event: False,
    )
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
    local_cursor = LocalCursorController(synthetic_guard=synthetic_guard)
    def _sync_local_cursor_visibility(state, node_id):
        if state == "active":
            if not local_cursor.hide_cursor():
                logging.debug("[CURSOR] failed to hide local cursor for active target=%s", node_id)
            return
        if not local_cursor.show_cursor():
            logging.debug("[CURSOR] failed to show local cursor for state=%s", state)

    router.add_state_listener(_sync_local_cursor_visibility)
    _install_cursor_cleanup_hooks(local_cursor.clear_clip, local_cursor.show_cursor)
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
        pointer_clipper=local_cursor,
        actual_pointer_provider=local_cursor.position,
    )
    router.add_state_listener(auto_switcher.on_router_state_change)
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
    global_hotkeys = None
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

        def _notify_status(message: str, tone: str = "neutral") -> None:
            if qt_runtime_app is not None:
                qt_runtime_app.request_status_message(message, tone)

        def _announce_hotkey(message: str, *, tone: str = "neutral") -> None:
            _notify_status(message, tone)
            _notify_tray(message)

        def _handle_target_result(
            status: str,
            target_id: str,
            reason: str | None,
            source: str | None,
        ) -> None:
            if source not in {"hotkey", "ui", "tray"}:
                return
            if status == "active":
                if source == "hotkey":
                    _announce_hotkey(f"PC 전환: {target_id}", tone="accent")
                else:
                    _notify_status(f"PC 전환 완료: {target_id}", tone="accent")
                return
            if status != "failed":
                return
            reason_text = {
                "target_offline": "대상 PC가 오프라인입니다.",
                "held_by_other": "다른 사용자가 현재 제어 중입니다.",
                "local_activity": "대상 PC에서 로컬 입력이 감지되었습니다.",
                "coordinator_unreachable": "코디네이터에 연결할 수 없습니다.",
            }.get(reason, "전환을 완료하지 못했습니다.")
            message = f"PC 전환 실패: {target_id} | {reason_text}"
            if source == "hotkey":
                _announce_hotkey(message, tone="warning")
            else:
                _notify_status(message, tone="warning")

        coord_client.add_target_result_listener(_handle_target_result)

        def _prepare_pointer_handoff(_target_id: str) -> None:
            if not hasattr(router, "prepare_pointer_handoff"):
                return
            current_pos = local_cursor.position()
            if current_pos is None:
                return
            anchor_event = enrich_pointer_event(
                make_mouse_move_event(int(current_pos[0]), int(current_pos[1])),
                get_virtual_screen_bounds(),
            )
            router.prepare_pointer_handoff(anchor_event)

        def _online_target_ids():
            online_ids = {
                node_id
                for node_id, conn in registry.all()
                if conn is not None and not conn.closed
            }
            ordered = []
            for node in ctx.nodes:
                if node.node_id in online_ids:
                    ordered.append(node.node_id)
            if ordered:
                ordered.insert(0, ctx.self_node.node_id)
            return ordered

        cycler = TargetCycler(
            ctx,
            router,
            coord_client=coord_client,
            targets_provider=_online_target_ids,
            before_select=_prepare_pointer_handoff,
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
            current = router.get_requested_target()
            next_id = cycler.previous()
            if next_id == ctx.self_node.node_id:
                auto_switcher.refresh_self_clip()
            if next_id is None:
                _announce_hotkey("PC 전환: 가능한 온라인 PC 없음", tone="warning")
            elif next_id == current:
                _announce_hotkey(f"PC 전환: {next_id} 이미 선택됨")
            elif next_id == ctx.self_node.node_id:
                _announce_hotkey("PC 전환: 내 PC", tone="accent")

        def _cycle_next():
            current = router.get_requested_target()
            next_id = cycler.next()
            if next_id == ctx.self_node.node_id:
                auto_switcher.refresh_self_clip()
            if next_id is None:
                _announce_hotkey("PC 전환: 가능한 온라인 PC 없음", tone="warning")
            elif next_id == current:
                _announce_hotkey(f"PC 전환: {next_id} 이미 선택됨")
            elif next_id == ctx.self_node.node_id:
                _announce_hotkey("PC 전환: 내 PC", tone="accent")

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
            _announce_hotkey(
                f"자동 경계 전환: {'ON' if enabled else 'OFF'}",
                tone="success" if enabled else "neutral",
            )

        def _quit_application():
            logging.info("[HOTKEY] %s quitting application", ctx.settings.hotkeys.quit_app)
            capture.put_event(make_system_event(f"{ctx.settings.hotkeys.quit_app} input detected, quitting app"))
            _announce_hotkey("앱 종료")
            shutdown_evt.set()
            if qt_runtime_app is not None:
                qt_runtime_app.request_quit()
            else:
                capture.stop()

        registered_global_hotkeys = set()
        if sys.platform.startswith("win"):
            try:
                from runtime.app_settings import hotkey_to_windows_binding
                from runtime.windows_global_hotkeys import WindowsGlobalHotkeyManager

                windows_hotkeys = {
                    "cycle-target-prev": (ctx.settings.hotkeys.previous_target, _cycle_previous),
                    "cycle-target-next": (ctx.settings.hotkeys.next_target, _cycle_next),
                    "toggle-auto-switch": (ctx.settings.hotkeys.toggle_auto_switch, _toggle_auto_switch),
                    "quit-application": (ctx.settings.hotkeys.quit_app, _quit_application),
                }
                bindings = []
                for binding_name, (hotkey_value, callback) in windows_hotkeys.items():
                    modifiers, vk_code = hotkey_to_windows_binding(hotkey_value)
                    bindings.append(
                        {
                            "name": binding_name,
                            "modifiers": modifiers,
                            "vk": vk_code,
                            "callback": callback,
                        }
                    )
                global_hotkeys = WindowsGlobalHotkeyManager(bindings)
                global_hotkeys.start()
                registered_global_hotkeys = global_hotkeys.active_binding_names
                for binding_name in sorted(registered_global_hotkeys):
                    logging.info("[HOTKEY] %s registered as Windows global hotkey", binding_name)
            except Exception as exc:
                logging.warning("[HOTKEY] Windows global hotkey registration unavailable: %s", exc)

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

    if capture is not None and sink is not None:

        def _local_input_override():
            controller_id = sink.get_authorized_controller()
            if not controller_id or controller_id == ctx.self_node.node_id:
                return
            if hasattr(sink, "remote_input_recent") and sink.remote_input_recent():
                return
            coord_client.notify_local_input_override()

        capture.local_activity_callback = _local_input_override

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
        auto_switcher.refresh_self_clip()

    if args.active_target and router is not None:
        coord_client.request_target(args.active_target, source="startup")

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
            local_cursor.show_cursor()
        config_reloader.stop_periodic_backup_pruning()
        try:
            config_reloader.flush_pending_layout()
        except Exception as exc:
            logging.warning("[CONFIG] failed to flush pending layout on shutdown: %s", exc)
        if global_hotkeys is not None:
            global_hotkeys.stop()
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
        if global_hotkeys is not None:
            global_hotkeys.join(timeout=1.0)
        time.sleep(0.1)
        logging.info("[EXIT] main stopped")


if __name__ == "__main__":
    main()

