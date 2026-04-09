"""
InputSink: target 쪽 data plane.

FrameDispatcher 가 input 이벤트를 이쪽으로 넘겨준다. 이 파일의 책임은
수신 → 추적 → 주입 위임의 세 단계로 고정돼 있다.

  1. 수신: `handle(peer_id, event)` — FrameDispatcher 가 직접 호출.
  2. 추적: peer 별로 눌린 키/마우스 버튼을 기록. peer 가 끊기거나
     target switch 가 일어나면 release_peer(peer_id) 가 호출되어
     눌린 상태를 반드시 풀어 준다 (stale-key 보호).
  3. 주입: 실제 OS 호출은 injection.os_injector.OSInjector 에 위임한다.
     - 프로덕션: PynputOSInjector
     - 테스트/미설치 fallback: LoggingOSInjector

설계 메모
  - 이 파일은 pynput 에 직접 의존하지 않는다. OSInjector 만 알면 충분.
  - 기존의 `[SINK ...]` 로그 라인은 유지한다. 이벤트 흐름 관찰에 유용하고
    injector 로그(`[INJECT ...]`)와 책임이 분리돼 있기 때문.
"""

import logging
import threading
from collections import defaultdict

from injection.os_injector import LoggingOSInjector, OSInjector


class InputSink:
    """
    수신 이벤트를 OS 로 주입하는 sink.

    Args:
        injector: 실제 OS 호출을 담당하는 OSInjector 구현체.
                  None 이면 LoggingOSInjector 로 기본 설정한다.
    """

    def __init__(self, injector: OSInjector | None = None):
        self._injector: OSInjector = injector or LoggingOSInjector()
        self._pressed = defaultdict(set)  # peer_id -> set of entry strings
        self._lock = threading.Lock()

    def handle(self, peer_id, event):
        kind = event.get("kind")
        self._track_pressed(peer_id, kind, event)

        if kind == "key_down":
            key = event.get("key")
            logging.info(f"[SINK KEY DOWN ] from={peer_id} key={key}")
            if key is not None:
                self._injector.inject_key(str(key), down=True)

        elif kind == "key_up":
            key = event.get("key")
            logging.info(f"[SINK KEY UP   ] from={peer_id} key={key}")
            if key is not None:
                self._injector.inject_key(str(key), down=False)

        elif kind == "mouse_move":
            x = event.get("x")
            y = event.get("y")
            logging.info(f"[SINK MOVE     ] from={peer_id} x={x} y={y}")
            if x is not None and y is not None:
                self._injector.inject_mouse_move(int(x), int(y))

        elif kind == "mouse_button":
            pressed = bool(event.get("pressed"))
            state = "DOWN" if pressed else "UP"
            button = event.get("button")
            x = event.get("x") or 0
            y = event.get("y") or 0
            logging.info(
                f"[SINK CLICK    ] from={peer_id} {button} {state} x={x} y={y}"
            )
            if button is not None:
                self._injector.inject_mouse_button(
                    str(button), int(x), int(y), down=pressed
                )

        elif kind == "mouse_wheel":
            x = event.get("x") or 0
            y = event.get("y") or 0
            dx = event.get("dx") or 0
            dy = event.get("dy") or 0
            logging.info(
                f"[SINK WHEEL    ] from={peer_id} x={x} y={y} dx={dx} dy={dy}"
            )
            self._injector.inject_mouse_wheel(int(x), int(y), int(dx), int(dy))

        else:
            logging.info(f"[SINK UNKNOWN  ] from={peer_id} event={event}")

    def release_peer(self, peer_id):
        """
        peer_id 와의 연결이 끊겼을 때 (또는 target 이 switch 될 때) 호출.
        그 peer 가 눌러 놓은 상태로 남아 있는 키/마우스 버튼 전부를
        OS 에 실제 release 로 주입한다. stale-key 보호.
        """
        with self._lock:
            entries = list(self._pressed.pop(peer_id, ()))

        if not entries:
            return

        logging.info(
            f"[SINK RELEASE  ] peer={peer_id} releasing {len(entries)} stuck input(s)"
        )
        for entry in entries:
            if entry.startswith("mouse:"):
                button = entry[len("mouse:"):]
                logging.info(
                    f"[SINK RELEASE  ] peer={peer_id} mouse_button button={button} released"
                )
                self._injector.inject_mouse_button(button, 0, 0, down=False)
            else:
                logging.info(
                    f"[SINK RELEASE  ] peer={peer_id} key_up key={entry}"
                )
                self._injector.inject_key(entry, down=False)

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
