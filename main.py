"""
main.py: 조립/수명주기만 담당한다.

여기에 로직을 새로 추가하지 말 것. 어떤 모듈을 만들고 어떤 순서로 start/stop 할지
만 정의한다. 각 모듈의 책임은 해당 파일 docstring 참조.

실행 예시:
  # 같은 PC 에서 두 인스턴스 테스트:
  python main.py --node-name A --active-target B
  python main.py --node-name B --active-target A

  # 배포된 exe (config.json 은 exe 옆에 둠):
  multi-controller.exe
"""

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
from utils.logger_setup import setup_logging


def parse_args():
    parser = argparse.ArgumentParser(
        description="multi-controller: 키보드/마우스 공유 프로그램"
    )
    parser.add_argument(
        "--node-name",
        help="같은 PC 다중 인스턴스 테스트용 self override (config.nodes[].name 과 일치)",
    )
    parser.add_argument(
        "--config",
        help="config.json 경로. 지정하지 않으면 exe/스크립트 옆 -> CWD 순으로 자동 탐지",
    )
    parser.add_argument(
        "--active-target",
        help="[테스트용] router 의 active target 을 기동 시 직접 세팅. "
             "coordinator 흐름이 완성되기 전까지의 임시 스위치.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    setup_logging()

    # 1) config & runtime context
    config, config_path = load_config(args.config)
    ctx = build_runtime_context(
        config, override_name=args.node_name, config_path=config_path
    )

    logging.info(f"[SELF] {ctx.self_node.label()} roles={list(ctx.self_node.roles)}")
    if not ctx.peers:
        logging.warning("[PEERS] 연결 대상이 없습니다. 수신만 동작합니다.")
    for peer in ctx.peers:
        logging.info(f"[PEER] {peer.label()} roles={list(peer.roles)}")

    # 2) shutdown primitive — works for all role combinations
    shutdown_evt = threading.Event()

    def _handle_signal(*_):
        shutdown_evt.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # 3) network core
    registry = PeerRegistry()
    dispatcher = FrameDispatcher()

    # 4) data plane: only wire sink if this node is a target
    if ctx.self_node.has_role("target"):
        # Prefer real OS injection via pynput. If pynput is missing or the
        # controllers cannot attach (headless Linux, missing perms on macOS,
        # Wayland, etc.) fall back to LoggingOSInjector so the node still
        # runs and logs received events — degraded but not crashed.
        try:
            from injection.os_injector import PynputOSInjector
            injector = PynputOSInjector()
            logging.info("[INJECTOR] pynput OS injection enabled")
        except Exception as e:
            from injection.os_injector import LoggingOSInjector
            injector = LoggingOSInjector()
            logging.warning(
                f"[INJECTOR] pynput unavailable ({e}); using logging injector"
            )
        sink = InputSink(injector=injector)
        dispatcher.set_input_handler(sink.handle)
        registry.add_unbind_listener(sink.release_peer)
    else:
        sink = None

    server = PeerServer(ctx, registry, dispatcher)
    dialer = PeerDialer(ctx, registry, dispatcher)

    # 5) routing: only wire router/capture if this node is a controller
    if ctx.self_node.has_role("controller"):
        from capture.input_capture import InputCapture
        capture_queue: "queue.Queue" = queue.Queue()
        capture = InputCapture(capture_queue)
        router = InputRouter(ctx, registry)

        if args.active_target:
            router.set_active_target(args.active_target)

        router_thread = threading.Thread(
            target=router.run,
            args=(capture_queue,),
            daemon=True,
            name="input-router",
        )
    else:
        capture = None
        capture_queue = None
        router = None
        router_thread = None

    # 6) coordinator (control plane)
    coord_node = pick_coordinator(ctx)
    coord_service = None
    coord_client = None
    if coord_node is None:
        logging.info("[COORDINATOR] none configured")
    elif coord_node.node_id == ctx.self_node.node_id:
        coord_service = CoordinatorService(ctx, registry, dispatcher)
    else:
        coord_client = CoordinatorClient(
            ctx, registry, dispatcher, coord_node,
            router=router,
        )
    if coord_node is not None:
        logging.info(f"[COORDINATOR] elected={coord_node.node_id}")

    # 7) lifecycle: start order = network -> coordinator -> router -> capture
    server.start()
    dialer.start()
    if coord_service is not None:
        coord_service.start()
    if coord_client is not None:
        coord_client.start()

    if router_thread is not None:
        router_thread.start()

    if capture is not None:
        capture.start()

    # 8) block until shutdown
    try:
        if capture is not None:
            # controller path: also exit when ESC stops capture (sets running=False)
            while not shutdown_evt.is_set() and capture.running:
                shutdown_evt.wait(timeout=0.2)
        else:
            shutdown_evt.wait()
    finally:
        logging.info("[SHUTDOWN] 종료 중...")
        if capture is not None:
            capture.stop()
        if router is not None:
            router.stop()
        if capture_queue is not None:
            # router 루프가 queue.get 에서 블록 중이면 깨우기
            capture_queue.put({"kind": "system", "message": "shutdown"})
        dialer.stop()
        server.stop()
        registry.close_all()
        time.sleep(0.1)
        logging.info("[EXIT] main 종료")


if __name__ == "__main__":
    main()
