"""Controller-side router that forwards input to one active target at a time."""

import logging
import queue
import threading
import time


class InputRouter:
    POLL_INTERVAL = 0.5
    VALID_STATES = frozenset({"inactive", "pending", "active"})

    def __init__(self, ctx, registry, event_processors=None):
        self.ctx = ctx
        self.registry = registry
        self._state = "inactive"
        self._target_id = None
        self._held_entries = set()
        self._remote_pressed_entries = set()
        self._pending_handoff_entries = set()
        self._handoff_anchor_event = None
        self._last_pointer_event = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._event_processors = list(event_processors or [])

    def _swap_state(self, state, node_id, reason=None):
        if state not in self.VALID_STATES:
            raise ValueError(f"invalid router state: {state}")

        with self._lock:
            prev_state = self._state
            prev_target = self._target_id
            if prev_state == state and prev_target == node_id:
                return
            to_release = list(self._remote_pressed_entries)
            if prev_state == "active" and prev_target is not None and node_id is not None and prev_target != node_id:
                self._pending_handoff_entries = {
                    entry for entry in self._held_entries if entry.startswith("mouse:")
                }
            elif state == "inactive" or node_id is None:
                self._pending_handoff_entries.clear()
                self._handoff_anchor_event = None
            self._remote_pressed_entries.clear()
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
        """Switch into pending state while waiting for a grant."""
        if node_id is None:
            self.clear_target(reason="pending-none")
            return
        target = self.ctx.get_node(node_id)
        if target is None:
            logging.warning("[ROUTER STATE] invalid target=%s", node_id)
            self.clear_target(reason="invalid-target")
            return
        if node_id == self.ctx.self_node.node_id:
            logging.warning("[ROUTER STATE] refusing self-target=%s", node_id)
            self.clear_target(reason="self-target")
            return
        self._swap_state("pending", node_id)

    def activate_target(self, node_id):
        """Mark a granted target as the current active target."""
        target = self.ctx.get_node(node_id)
        if target is None:
            logging.warning("[ROUTER STATE] invalid grant target=%s", node_id)
            self.clear_target(reason="invalid-grant")
            return
        if node_id == self.ctx.self_node.node_id:
            logging.warning("[ROUTER STATE] refusing self-grant=%s", node_id)
            self.clear_target(reason="self-grant")
            return
        self._swap_state("active", node_id)
        self._apply_pending_handoff(node_id)

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

    def has_pressed_mouse_buttons(self) -> bool:
        with self._lock:
            return any(entry.startswith("mouse:") for entry in self._held_entries)

    def prepare_pointer_handoff(self, anchor_event: dict | None):
        if anchor_event is None:
            return
        with self._lock:
            self._handoff_anchor_event = dict(anchor_event)

    def add_event_processor(self, processor):
        self._event_processors.append(processor)

    def run(self, source_queue: "queue.Queue"):
        """Consume capture events and forward them to the active target."""
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

            event = self._process_event(event)
            if event is None:
                continue
            kind = event.get("kind")
            self._track_held(kind, event)

            if kind in {"mouse_move", "mouse_button", "mouse_wheel"}:
                with self._lock:
                    self._last_pointer_event = dict(event)

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
                self._track_remote_pressed(kind, event)
                logging.debug("[ROUTER SEND] kind=%s target=%s", kind, target_id)

    def stop(self):
        self._stop.set()

    def _track_held(self, kind, event):
        with self._lock:
            if kind == "key_down":
                self._held_entries.add(event["key"])
            elif kind == "key_up":
                self._held_entries.discard(event["key"])
            elif kind == "mouse_button":
                entry = f"mouse:{event['button']}"
                if event.get("pressed"):
                    self._held_entries.add(entry)
                else:
                    self._held_entries.discard(entry)

    def _track_remote_pressed(self, kind, event):
        with self._lock:
            if kind == "key_down":
                self._remote_pressed_entries.add(event["key"])
            elif kind == "key_up":
                self._remote_pressed_entries.discard(event["key"])
            elif kind == "mouse_button":
                entry = f"mouse:{event['button']}"
                if event.get("pressed"):
                    self._remote_pressed_entries.add(entry)
                else:
                    self._remote_pressed_entries.discard(entry)

    def _send_releases(self, conn, entries):
        """Release keys/buttons on the previously active target."""
        ts = time.time()
        for entry in entries:
            if entry.startswith("mouse:"):
                button = entry[len("mouse:") :]
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

    def _apply_pending_handoff(self, node_id):
        with self._lock:
            entries = sorted(
                entry
                for entry in self._pending_handoff_entries
                if entry in self._held_entries and entry.startswith("mouse:")
            )
            anchor_event = (
                dict(self._handoff_anchor_event)
                if self._handoff_anchor_event is not None
                else (
                    dict(self._last_pointer_event)
                    if self._last_pointer_event is not None and self._last_pointer_event.get("kind") == "mouse_move"
                    else None
                )
            )
            self._pending_handoff_entries.clear()
            self._handoff_anchor_event = None

        conn = self.registry.get(node_id)
        if conn is None:
            return

        pointer_event = None
        if anchor_event is not None:
            pointer_event = {
                "kind": "mouse_move",
                "ts": time.time(),
            }
            for key in ("x", "y", "x_norm", "y_norm"):
                if key in anchor_event:
                    pointer_event[key] = anchor_event[key]
            conn.send_frame(pointer_event)

        if not entries:
            return

        button_x = 0 if pointer_event is None else pointer_event.get("x", 0)
        button_y = 0 if pointer_event is None else pointer_event.get("y", 0)
        ts = time.time()
        for entry in entries:
            button = entry[len("mouse:") :]
            frame = {
                "kind": "mouse_button",
                "ts": ts,
                "button": button,
                "pressed": True,
                "x": button_x,
                "y": button_y,
            }
            if pointer_event is not None:
                if "x_norm" in pointer_event:
                    frame["x_norm"] = pointer_event["x_norm"]
                if "y_norm" in pointer_event:
                    frame["y_norm"] = pointer_event["y_norm"]
            if conn.send_frame(frame):
                with self._lock:
                    self._remote_pressed_entries.add(entry)

    def _process_event(self, event):
        current = event
        for processor in self._event_processors:
            current = processor(current)
            if current is None:
                return None
        return current
