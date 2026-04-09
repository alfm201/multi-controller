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
import threading
import time

from capture.input_capture import InputCapture
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

    logging.info(f"[SELF] {ctx.self_node.label()}")
    if not ctx.peers:
        logging.warning("[PEERS] 연결 대상이 없습니다. 수신만 동작합니다.")
    for peer in ctx.peers:
        logging.info(f"[PEER] {peer.label()}")

    # 2) network core
    registry = PeerRegistry()
    dispatcher = FrameDispatcher()

    sink = InputSink()
    dispatcher.set_input_handler(sink.handle)

    server = PeerServer(ctx, registry, dispatcher)
    dialer = PeerDialer(ctx, registry, dispatcher)

    # 3) routing
    router = InputRouter(ctx, registry)

    # 4) coordinator (control plane)
    coord_node = pick_coordinator(ctx)
    coord_service = None
    coord_client = None
    if coord_node is None:
        logging.info("[COORDINATOR] none configured")
    elif coord_node.node_id == ctx.self_node.node_id:
        coord_service = CoordinatorService(ctx, registry, dispatcher)
    else:
        coord_client = CoordinatorClient(
            ctx, registry, dispatcher, coord_node, router=router
        )
    if coord_node is not None:
        logging.info(f"[COORDINATOR] elected={coord_node.node_id}")

    # 5) capture
    capture_queue: "queue.Queue" = queue.Queue()
    capture = InputCapture(capture_queue)

    # 6) lifecycle: start order = network -> coordinator -> router -> capture
    server.start()
    dialer.start()
    if coord_service is not None:
        coord_service.start()
    if coord_client is not None:
        coord_client.start()

    if args.active_target:
        router.set_active_target(args.active_target)

    router_thread = threading.Thread(
        target=router.run,
        args=(capture_queue,),
        daemon=True,
        name="input-router",
    )
    router_thread.start()

    try:
        capture.start()
        capture.join()
    except KeyboardInterrupt:
        logging.info("[INTERRUPTED] Ctrl+C")
    finally:
        capture.stop()
        router.stop()
        # router 루프가 queue.get 에서 블록 중이면 깨우기
        capture_queue.put({"kind": "system", "message": "shutdown"})
        dialer.stop()
        server.stop()
        registry.close_all()
        # router 스레드가 깔끔하게 빠져나오도록 아주 짧게 대기
        time.sleep(0.1)
        logging.info("[EXIT] main 종료")


if __name__ == "__main__":
    main()
