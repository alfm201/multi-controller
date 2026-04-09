"""multi-controller 실행 진입점."""

import argparse
import logging
import queue
import signal
import threading
import time

from coordinator.client import CoordinatorClient
from coordinator.election import pick_coordinator
from coordinator.service import CoordinatorService
from network.dispatcher import FrameDispatcher
from network.peer_dialer import PeerDialer
from network.peer_registry import PeerRegistry
from network.peer_server import PeerServer
from routing.router import InputRouter
from routing.sink import InputSink
from runtime.config_loader import load_config
from runtime.context import build_runtime_context
from runtime.state_watcher import StateWatcher
from runtime.status_reporter import StatusReporter
from utils.logger_setup import setup_logging


def parse_args():
    parser = argparse.ArgumentParser(
        description="multi-controller: shared keyboard and mouse control"
    )
    parser.add_argument(
        "--node-name",
        help="Override auto-detected self node with config.nodes[].name.",
    )
    parser.add_argument(
        "--config",
        help="Path to config.json. Defaults to bundled/project/CWD discovery.",
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
    return parser.parse_args()


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


def main():
    args = parse_args()
    setup_logging()

    config, config_path = load_config(args.config)
    ctx = build_runtime_context(
        config,
        override_name=args.node_name,
        config_path=config_path,
    )
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
    coordinator_resolver = lambda: pick_coordinator(ctx, registry)

    sink = None
    if ctx.self_node.has_role("target"):
        try:
            from injection.os_injector import PynputOSInjector

            injector = PynputOSInjector()
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
    if ctx.self_node.has_role("controller"):
        from capture.input_capture import InputCapture

        capture_queue = queue.Queue()
        capture = InputCapture(capture_queue)
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

    if capture is not None and router is not None:
        from capture.hotkey import HotkeyMatcher, TargetCycler

        cycler = TargetCycler(ctx, router, coord_client=coord_client)
        capture.hotkey_matchers.append(
            HotkeyMatcher(
                modifier_groups=[
                    ("Key.ctrl", "Key.ctrl_l", "Key.ctrl_r"),
                    ("Key.shift", "Key.shift_l", "Key.shift_r"),
                ],
                trigger="Key.tab",
                callback=cycler.cycle,
                name="cycle-target",
            )
        )
        logging.info("[HOTKEY] Ctrl+Shift+Tab cycles active target")

    server.start()
    dialer.start()
    coord_service.start()
    coord_client.start()
    state_watcher.start()
    status_reporter.start()
    if router_thread is not None:
        router_thread.start()
    if capture is not None:
        capture.start()

    if args.active_target and router is not None:
        coord_client.request_target(args.active_target)

    try:
        if capture is not None:
            while not shutdown_evt.is_set() and capture.running:
                shutdown_evt.wait(timeout=0.2)
        else:
            shutdown_evt.wait()
    finally:
        logging.info("[SHUTDOWN] stopping")
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

