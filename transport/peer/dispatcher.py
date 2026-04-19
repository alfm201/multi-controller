"""
FrameDispatcher: peer connection 에서 올라온 프레임을 kind 로 분기한다.

  - input event 들            -> InputSink.handle(peer_id, event)
  - "ctrl.*" 프레임            -> 등록된 control handler(peer_id, frame)
  - hello/bye/ping/pong       -> peer 레이어 자체가 처리 (여기까진 오지 않음)
  - 그 외                     -> debug 로그

InputSink 와 CoordinatorService/Client 를 느슨하게 결합시키는 역할이다.
tcp_receiver 가 직접 print 하던 옛 구조를 대체한다.
"""

import logging
import threading


INPUT_KINDS = frozenset({
    "key_down",
    "key_up",
    "mouse_move",
    "mouse_button",
    "mouse_wheel",
})


class FrameDispatcher:
    def __init__(self):
        self._lock = threading.Lock()
        self._input_handler = None
        self._control_handlers = {}  # kind -> handler(peer_id, frame)

    def set_input_handler(self, handler):
        with self._lock:
            self._input_handler = handler

    def register_control_handler(self, kind, handler):
        if not kind.startswith("ctrl."):
            raise ValueError(f"control kinds must start with 'ctrl.': {kind}")
        with self._lock:
            self._control_handlers[kind] = handler

    def dispatch(self, peer_id, frame):
        kind = frame.get("kind")

        if kind in INPUT_KINDS:
            handler = self._input_handler
            if handler is None:
                return
            handler(peer_id, frame)
            return

        if isinstance(kind, str) and kind.startswith("ctrl."):
            with self._lock:
                handler = self._control_handlers.get(kind)
            if handler is None:
                logging.debug(f"[DISPATCH] no control handler for {kind}")
                return
            handler(peer_id, frame)
            return

        if kind in ("ping", "pong", "hello", "bye"):
            # 연결 레이어 수준. 현 단계에서는 처리할 것 없음.
            return

        logging.debug(f"[DISPATCH] unknown frame kind={kind!r} from {peer_id}")
