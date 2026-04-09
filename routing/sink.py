"""
InputSink: target 쪽 data plane.

FrameDispatcher 가 input 이벤트를 이쪽으로 넘겨준다.
현재는 로깅만 수행하지만, 인터페이스는 "peer_id, event 를 받는 callable" 로 고정되어
나중에 OS 수준 입력 주입 (pynput.Controller, SendInput, uinput 등) 로 바꿀 때
교체 지점이 이 파일 하나가 되도록 설계되었다.

tcp_receiver 가 직접 print 하던 옛 구조와의 차이:
  - 수신 경로(PeerConnection._recv_loop -> FrameDispatcher) 와
    적용 경로(InputSink.handle) 가 분리되어 있다.
  - 따라서 나중에 InputSink 만 교체하면 진짜 입력 주입으로 전환 가능.

stale-key 보호:
  remote controller 가 끊기면 release_peer(peer_id) 가 호출된다.
  각 peer 별로 눌린 키/마우스 버튼을 추적하고, disconnect 시 OS에 release 를 주입한다.
  (현재 단계에서는 로깅만. OS 주입은 v2 에서 추가)
"""

import logging
import threading
import time
from collections import defaultdict


class InputSink:
    """기본 구현 - 로그만 찍는다. 나중에 OS 레벨 injector 로 교체."""

    def __init__(self):
        self._pressed = defaultdict(set)  # peer_id -> set of entry strings
        self._lock = threading.Lock()

    def handle(self, peer_id, event):
        kind = event.get("kind")
        self._track_pressed(peer_id, kind, event)

        if kind == "key_down":
            logging.info(
                f"[SINK KEY DOWN ] from={peer_id} key={event.get('key')}"
            )
        elif kind == "key_up":
            logging.info(
                f"[SINK KEY UP   ] from={peer_id} key={event.get('key')}"
            )
        elif kind == "mouse_move":
            logging.info(
                f"[SINK MOVE     ] from={peer_id} "
                f"x={event.get('x')} y={event.get('y')}"
            )
        elif kind == "mouse_button":
            state = "DOWN" if event.get("pressed") else "UP"
            logging.info(
                f"[SINK CLICK    ] from={peer_id} {event.get('button')} {state} "
                f"x={event.get('x')} y={event.get('y')}"
            )
        elif kind == "mouse_wheel":
            logging.info(
                f"[SINK WHEEL    ] from={peer_id} "
                f"x={event.get('x')} y={event.get('y')} "
                f"dx={event.get('dx')} dy={event.get('dy')}"
            )
        else:
            logging.info(f"[SINK UNKNOWN  ] from={peer_id} event={event}")

    def release_peer(self, peer_id):
        """
        peer_id 와의 연결이 끊겼을 때 호출.
        눌린 채로 남아있는 키/마우스 버튼의 release 이벤트를 처리한다.

        현재: 로깅만 수행.
        v2: 실제 OS 레벨 release 주입으로 교체.
        """
        with self._lock:
            entries = list(self._pressed.pop(peer_id, ()))

        if not entries:
            return

        ts = time.time()
        logging.info(
            f"[SINK RELEASE  ] peer={peer_id} releasing {len(entries)} stuck input(s)"
        )
        for entry in entries:
            if entry.startswith("mouse:"):
                button = entry[len("mouse:"):]
                logging.info(
                    f"[SINK RELEASE  ] peer={peer_id} mouse_button button={button} released"
                )
                # v2: inject mouse_button release via OS API
            else:
                logging.info(
                    f"[SINK RELEASE  ] peer={peer_id} key_up key={entry}"
                )
                # v2: inject key_up via OS API

    # ------------------------------------------------------------
    # internal tracking
    # ------------------------------------------------------------
    def _track_pressed(self, peer_id, kind, event):
        with self._lock:
            if kind == "key_down":
                self._pressed[peer_id].add(event["key"])
            elif kind == "key_up":
                self._pressed[peer_id].discard(event["key"])
            elif kind == "mouse_button":
                entry = f"mouse:{event['button']}"
                if event.get("pressed"):
                    self._pressed[peer_id].add(entry)
                else:
                    self._pressed[peer_id].discard(entry)


class NullInputSink:
    """테스트용 no-op sink."""

    def handle(self, peer_id, event):
        pass

    def release_peer(self, peer_id):
        pass
