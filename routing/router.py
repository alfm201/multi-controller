"""Controller 쪽 입력을 현재 활성 target 하나로만 전달하는 라우터."""

import logging
import queue
import threading
import time


class InputRouter:
    POLL_INTERVAL = 0.5
    VALID_STATES = frozenset({"inactive", "pending", "active"})

    def __init__(self, ctx, registry):
        self.ctx = ctx
        self.registry = registry
        self._state = "inactive"
        self._target_id = None
        self._pressed = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def _swap_state(self, state, node_id, reason=None):
        if state not in self.VALID_STATES:
            raise ValueError(f"invalid router state: {state}")

        with self._lock:
            prev_state = self._state
            prev_target = self._target_id
            if prev_state == state and prev_target == node_id:
                return
            to_release = list(self._pressed)
            self._pressed.clear()
            self._state = state
            self._target_id = node_id

        if prev_state == "active" and prev_target is not None and to_release:
            conn = self.registry.get(prev_target)
            if conn is not None:
                self._send_releases(conn, to_release)

        if reason:
            logging.info(
                "[ROUTER STATE] %s:%s -> %s:%s (%s)",
                prev_state,
                prev_target,
                state,
                node_id,
                reason,
            )
        else:
            logging.info(
                "[ROUTER STATE] %s:%s -> %s:%s",
                prev_state,
                prev_target,
                state,
                node_id,
            )

    def set_pending_target(self, node_id):
        """grant를 기다리는 target으로 전환한다."""
        if node_id is None:
            self.clear_target(reason="pending-none")
            return
        target = self.ctx.get_node(node_id)
        if target is None:
            logging.warning("[ROUTER STATE] invalid target=%s", node_id)
            self.clear_target(reason="invalid-target")
            return
        if not target.has_role("target"):
            logging.warning("[ROUTER STATE] non-target peer=%s", node_id)
            self.clear_target(reason="non-target")
            return
        if node_id == self.ctx.self_node.node_id:
            logging.warning("[ROUTER STATE] refusing self-target=%s", node_id)
            self.clear_target(reason="self-target")
            return
        self._swap_state("pending", node_id)

    def activate_target(self, node_id):
        """grant를 받은 target을 실제 active 상태로 만든다."""
        target = self.ctx.get_node(node_id)
        if target is None:
            logging.warning("[ROUTER STATE] invalid grant target=%s", node_id)
            self.clear_target(reason="invalid-grant")
            return
        if not target.has_role("target"):
            logging.warning("[ROUTER STATE] granted peer without target role=%s", node_id)
            self.clear_target(reason="grant-non-target")
            return
        if node_id == self.ctx.self_node.node_id:
            logging.warning("[ROUTER STATE] refusing self-grant=%s", node_id)
            self.clear_target(reason="self-grant")
            return
        self._swap_state("active", node_id)

    def clear_target(self, reason=None):
        self._swap_state("inactive", None, reason=reason)

    def get_target_state(self):
        with self._lock:
            return self._state

    def get_active_target(self):
        with self._lock:
            if self._state != "active":
                return None
            return self._target_id

    def get_selected_target(self):
        with self._lock:
            return self._target_id

    def run(self, source_queue: "queue.Queue"):
        """capture queue를 소비하면서 active target으로 입력을 전달한다."""
        while not self._stop.is_set():
            try:
                event = source_queue.get(timeout=self.POLL_INTERVAL)
            except queue.Empty:
                continue

            kind = event.get("kind")
            if kind == "system":
                if event.get("message") == "shutdown":
                    break
                continue

            with self._lock:
                state = self._state
                target_id = self._target_id

            if state != "active" or target_id is None:
                continue

            if target_id == self.ctx.self_node.node_id:
                logging.warning("[ROUTER DROP] loopback target=%s", target_id)
                continue

            conn = self.registry.get(target_id)
            if conn is None:
                logging.debug("[ROUTER DROP] no live conn to %s", target_id)
                continue

            if conn.send_frame(event):
                self._track_pressed(kind, event)
                logging.debug("[ROUTER SEND] kind=%s target=%s", kind, target_id)

    def stop(self):
        self._stop.set()

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
        """target 전환 시 이전 target으로 눌린 키/버튼 해제를 보낸다."""
        ts = time.time()
        for entry in entries:
            if entry.startswith("mouse:"):
                button = entry[len("mouse:"):]
                conn.send_frame(
                    {
                        "kind": "mouse_button",
                        "ts": ts,
                        "button": button,
                        "pressed": False,
                        "x": 0,
                        "y": 0,
                    }
                )
            else:
                conn.send_frame({"kind": "key_up", "ts": ts, "key": entry})

