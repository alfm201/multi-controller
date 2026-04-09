"""
InputRouter: controller 쪽 data plane.

로컬 InputCapture 가 만든 이벤트를 꺼내 "지금 active 인 target 하나"에만 송신한다.
옛 fanout_loop (모든 peer 에 복제) 는 완전히 사라졌다. 최종 구조에서 입력은
항상 한 target 으로만 가야 하기 때문이다.

active target 을 누가 세팅하는가:
  - 현 단계(테스트): main.py 의 --active-target 옵션으로 직접 세팅
  - 다음 단계        : CoordinatorClient 가 coordinator 로부터 GRANT 를 받을 때
                        router.set_active_target(target_id) 호출
  - 그 다음 단계     : UI / 핫키로 controller 가 target 을 전환

active target 이 None 이거나 해당 peer 에 살아있는 연결이 없으면 이벤트는 드롭된다.
(버퍼링 옵션은 이후 도입 여지를 남겨둔다.)

stale-key 보호:
  set_active_target() 으로 target 을 전환할 때 이전 target 에 눌린 채로 남아있는
  키/마우스 버튼의 release 이벤트를 자동으로 전송한다.
  키보드 엔트리: 키 문자열 그대로 (e.g. "a", "Key.esc")
  마우스 버튼 엔트리: "mouse:<button_str>" (e.g. "mouse:Button.left")
"""

import logging
import queue
import threading
import time


class InputRouter:
    POLL_INTERVAL = 0.5

    def __init__(self, ctx, registry):
        self.ctx = ctx
        self.registry = registry
        self._active_target_id = None
        self._pressed = set()   # entries: key_str | "mouse:<button_str>"
        self._lock = threading.Lock()
        self._stop = threading.Event()

    # ------------------------------------------------------------
    # active target state
    # ------------------------------------------------------------
    def set_active_target(self, node_id):
        with self._lock:
            prev = self._active_target_id
            if prev == node_id:
                return
            # Grab and clear pressed set before the swap
            to_release = list(self._pressed)
            self._pressed.clear()
            self._active_target_id = node_id

        # Send releases outside lock to avoid holding lock during I/O
        if prev is not None and to_release:
            conn = self.registry.get(prev)
            if conn is not None:
                self._send_releases(conn, to_release)

        logging.info(f"[ROUTER ACTIVE] {prev} -> {node_id}")

    def clear_active_target(self):
        self.set_active_target(None)

    def get_active_target(self):
        with self._lock:
            return self._active_target_id

    # ------------------------------------------------------------
    # consume-and-forward loop
    # ------------------------------------------------------------
    def run(self, source_queue: "queue.Queue"):
        """blocking. 별도 스레드에서 호출."""
        while not self._stop.is_set():
            try:
                event = source_queue.get(timeout=self.POLL_INTERVAL)
            except queue.Empty:
                continue

            kind = event.get("kind")

            if kind == "system":
                # system 이벤트는 로컬 전용. 원격으로 보내지 않는다.
                if event.get("message") == "shutdown":
                    break
                continue

            target_id = self.get_active_target()
            if target_id is None:
                continue

            if target_id == self.ctx.self_node.node_id:
                # 자기 자신을 target 으로 두는 건 허용하지 않는다(로컬 loopback).
                continue

            conn = self.registry.get(target_id)
            if conn is None:
                logging.debug(f"[ROUTER DROP] no live conn to {target_id}")
                continue

            if conn.send_frame(event):
                self._track_pressed(kind, event)

    def stop(self):
        self._stop.set()

    # ------------------------------------------------------------
    # pressed-key tracking
    # ------------------------------------------------------------
    def _track_pressed(self, kind, event):
        with self._lock:
            if kind == "key_down":
                self._pressed.add(event["key"])
            elif kind == "key_up":
                self._pressed.discard(event["key"])
            elif kind == "mouse_button":
                entry = f"mouse:{event['button']}"
                if event.get("pressed"):
                    self._pressed.add(entry)
                else:
                    self._pressed.discard(entry)

    def _send_releases(self, conn, entries):
        ts = time.time()
        for entry in entries:
            if entry.startswith("mouse:"):
                button = entry[len("mouse:"):]
                conn.send_frame({
                    "kind": "mouse_button",
                    "ts": ts,
                    "button": button,
                    "pressed": False,
                    "x": 0,
                    "y": 0,
                })
            else:
                conn.send_frame({"kind": "key_up", "ts": ts, "key": entry})
