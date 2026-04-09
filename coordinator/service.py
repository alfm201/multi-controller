"""Coordinator-side lease management."""

import logging
import threading
import time

from coordinator.protocol import (
    DEFAULT_LEASE_TTL_MS,
    make_deny,
    make_grant,
    make_lease_update,
)


class CoordinatorService:
    DEFAULT_LEASE_TTL_MS = DEFAULT_LEASE_TTL_MS
    EXPIRY_POLL_INTERVAL = 0.25

    def __init__(self, ctx, registry, dispatcher):
        self.ctx = ctx
        self.registry = registry
        self.dispatcher = dispatcher

        self._lock = threading.Lock()
        self._leases = {}  # target_id -> {"controller_id": str, "expires_at": float}
        self._stop = threading.Event()
        self._thread = None

        dispatcher.register_control_handler("ctrl.claim", self._on_claim)
        dispatcher.register_control_handler("ctrl.release", self._on_release)
        dispatcher.register_control_handler("ctrl.heartbeat", self._on_heartbeat)
        registry.add_listener(self._on_registry_event)

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._expiry_loop,
            daemon=True,
            name="coordinator-expiry",
        )
        self._thread.start()
        logging.info(
            "[COORDINATOR SERVICE] started on self=%s ttl_ms=%s",
            self.ctx.self_node.node_id,
            self.DEFAULT_LEASE_TTL_MS,
        )

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _now(self) -> float:
        return time.monotonic()

    def _lease_expiry(self) -> float:
        return self._now() + (self.DEFAULT_LEASE_TTL_MS / 1000.0)

    def _reply(self, peer_id, frame):
        if peer_id == self.ctx.self_node.node_id:
            self.dispatcher.dispatch(peer_id, frame)
            return True
        conn = self.registry.get(peer_id)
        if conn is None:
            logging.info("[COORDINATOR] no conn to reply to %s", peer_id)
            return False
        return conn.send_frame(frame)

    def _send_lease_update(self, target_id, controller_id):
        if target_id == self.ctx.self_node.node_id:
            self.dispatcher.dispatch(
                target_id,
                make_lease_update(
                    target_id=target_id,
                    controller_id=controller_id,
                    lease_ttl_ms=self.DEFAULT_LEASE_TTL_MS,
                ),
            )
            return True
        conn = self.registry.get(target_id)
        if conn is None:
            logging.debug("[COORDINATOR] target %s not connected for lease_update", target_id)
            return False
        return conn.send_frame(
            make_lease_update(
                target_id=target_id,
                controller_id=controller_id,
                lease_ttl_ms=self.DEFAULT_LEASE_TTL_MS,
            )
        )

    def _notify_target_locked(self, target_id):
        lease = self._leases.get(target_id)
        controller_id = None if lease is None else lease["controller_id"]
        self._send_lease_update(target_id, controller_id)

    def _validate_target(self, target_id):
        node = self.ctx.get_node(target_id)
        if node is None:
            return None, "unknown_target"
        if not node.has_role("target"):
            return None, "not_a_target"
        return node, None

    def _on_registry_event(self, event, node_id):
        if event != "bound":
            return
        node = self.ctx.get_node(node_id)
        if node is None or not node.has_role("target"):
            return
        with self._lock:
            self._notify_target_locked(node_id)

    def _on_claim(self, peer_id, frame):
        target_id = frame.get("target_id")
        controller_id = frame.get("controller_id") or peer_id
        if not target_id:
            return

        _, error = self._validate_target(target_id)
        if error is not None:
            logging.info(
                "[COORDINATOR] DENY target=%s to %s (%s)",
                target_id,
                controller_id,
                error,
            )
            self._reply(peer_id, make_deny(target_id, controller_id, error))
            return

        with self._lock:
            holder = self._leases.get(target_id)
            holder_id = None if holder is None else holder["controller_id"]
            if holder_id is None or holder_id == controller_id:
                self._leases[target_id] = {
                    "controller_id": controller_id,
                    "expires_at": self._lease_expiry(),
                }
                granted = True
                self._notify_target_locked(target_id)
            else:
                granted = False

        if granted:
            logging.info("[COORDINATOR] GRANT target=%s to %s", target_id, controller_id)
            self._reply(
                peer_id,
                make_grant(
                    target_id=target_id,
                    controller_id=controller_id,
                    lease_ttl_ms=self.DEFAULT_LEASE_TTL_MS,
                ),
            )
        else:
            logging.info(
                "[COORDINATOR] DENY target=%s to %s (held by %s)",
                target_id,
                controller_id,
                holder_id,
            )
            self._reply(peer_id, make_deny(target_id, controller_id, "held_by_other"))

    def _on_release(self, peer_id, frame):
        target_id = frame.get("target_id")
        controller_id = frame.get("controller_id") or peer_id
        if not target_id:
            return

        with self._lock:
            holder = self._leases.get(target_id)
            holder_id = None if holder is None else holder["controller_id"]
            if holder_id == controller_id:
                del self._leases[target_id]
                released = True
                self._notify_target_locked(target_id)
            else:
                released = False

        if released:
            logging.info("[COORDINATOR] RELEASED target=%s by %s", target_id, controller_id)

    def _on_heartbeat(self, peer_id, frame):
        target_id = frame.get("target_id")
        controller_id = frame.get("controller_id") or peer_id
        if not target_id:
            return

        _, error = self._validate_target(target_id)
        if error is not None:
            return

        with self._lock:
            holder = self._leases.get(target_id)
            holder_id = None if holder is None else holder["controller_id"]
            if holder_id is not None and holder_id != controller_id:
                return
            self._leases[target_id] = {
                "controller_id": controller_id,
                "expires_at": self._lease_expiry(),
            }
            if holder_id != controller_id:
                logging.info(
                    "[COORDINATOR] HEARTBEAT restored target=%s holder=%s",
                    target_id,
                    controller_id,
                )
                self._notify_target_locked(target_id)

    def _expire_loop(self):
        while not self._stop.wait(self.EXPIRY_POLL_INTERVAL):
            expired = self._expire_once()
            for target_id, controller_id in expired:
                logging.info(
                    "[COORDINATOR] EXPIRED target=%s controller=%s",
                    target_id,
                    controller_id,
                )

    def _expire_once(self):
        expired = []
        now = self._now()
        with self._lock:
            for target_id, lease in list(self._leases.items()):
                if lease["expires_at"] <= now:
                    expired.append((target_id, lease["controller_id"]))
                    del self._leases[target_id]
            for target_id, _controller_id in expired:
                self._notify_target_locked(target_id)
        return expired
