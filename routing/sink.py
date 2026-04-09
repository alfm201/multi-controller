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
"""

import logging


class InputSink:
    """기본 구현 - 로그만 찍는다. 나중에 OS 레벨 injector 로 교체."""

    def handle(self, peer_id, event):
        kind = event.get("kind")

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


class NullInputSink:
    """테스트용 no-op sink."""

    def handle(self, peer_id, event):
        pass
