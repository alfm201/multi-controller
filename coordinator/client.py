"""Controller와 target 양쪽에서 쓰는 coordinator client."""

import logging
import threading

from coordinator.protocol import DEFAULT_LEASE_TTL_MS, make_claim, make_heartbeat, make_release


class CoordinatorClient:
    HEARTBEAT_INTERVAL_SEC = 1.0
    CONTROL_POLL_INTERVAL_SEC = 0.5

    def __init__(
        self,
        ctx,
        registry,
        dispatcher,
        coordinator_resolver,
        router=None,
        sink=None,
    ):
        self.ctx = ctx
        self.registry = registry
        self.dispatcher = dispatcher
        self.coordinator_resolver = coordinator_resolver
        self.router = router
        self.sink = sink

        self._requested_target_id = None
        self._last_coordinator_id = None
        self._stop = threading.Event()
        self._thread = None

        dispatcher.register_control_handler("ctrl.grant", self._on_grant)
        dispatcher.register_control_handler("ctrl.deny", self._on_deny)
        dispatcher.register_control_handler("ctrl.lease_update", self._on_lease_update)

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._control_loop,
            daemon=True,
            name="coordinator-control",
        )
        self._thread.start()
        logging.info("[COORDINATOR CLIENT] started")

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _send(self, frame) -> bool:
        coordinator_node = self.coordinator_resolver()
        if coordinator_node is None:
            logging.info("[COORDINATOR CLIENT] no elected coordinator")
            return False
        if coordinator_node.node_id == self.ctx.self_node.node_id:
            self.dispatcher.dispatch(coordinator_node.node_id, frame)
            return True
        conn = self.registry.get(coordinator_node.node_id)
        if conn is None:
            logging.info(
                "[COORDINATOR CLIENT] no conn to coordinator %s",
                coordinator_node.node_id,
            )
            return False
        return conn.send_frame(frame)

    def claim(self, target_id: str) -> bool:
        return self._send(make_claim(target_id, self.ctx.self_node.node_id))

    def release(self, target_id: str) -> bool:
        return self._send(make_release(target_id, self.ctx.self_node.node_id))

    def heartbeat(self, target_id: str) -> bool:
        return self._send(make_heartbeat(target_id, self.ctx.self_node.node_id))

    def request_target(self, target_id: str) -> bool:
        if self.router is None:
            return self.claim(target_id)

        if self._requested_target_id == target_id:
            if self.router.get_target_state() == "pending":
                logging.info(
                    "[COORDINATOR CLIENT] pending target=%s 재-claim",
                    target_id,
                )
                return self.claim(target_id)
            return True

        previous_target = self._requested_target_id
        self._requested_target_id = target_id

        if previous_target and previous_target != target_id:
            self.release(previous_target)

        self.router.set_pending_target(target_id)
        return self.claim(target_id)

    def clear_target(self) -> None:
        target_id = self._requested_target_id
        self._requested_target_id = None
        if target_id:
            self.release(target_id)
        if self.router is not None:
            self.router.clear_target(reason="coordinator-clear")

    def _control_loop(self):
        heartbeat_deadline = 0.0
        while not self._stop.wait(self.CONTROL_POLL_INTERVAL_SEC):
            coordinator_node = self.coordinator_resolver()
            coordinator_id = None if coordinator_node is None else coordinator_node.node_id
            if coordinator_id != self._last_coordinator_id:
                self._on_coordinator_changed(coordinator_id)

            if self.router is None:
                continue

            target_id = self.router.get_selected_target()
            state = self.router.get_target_state()
            if not target_id:
                continue

            if state == "pending":
                self.claim(target_id)
                continue

            if state == "active":
                heartbeat_deadline += self.CONTROL_POLL_INTERVAL_SEC
                if heartbeat_deadline >= self.HEARTBEAT_INTERVAL_SEC:
                    heartbeat_deadline = 0.0
                    self.heartbeat(target_id)
            else:
                heartbeat_deadline = 0.0

    def _on_coordinator_changed(self, coordinator_id):
        previous = self._last_coordinator_id
        self._last_coordinator_id = coordinator_id
        logging.info(
            "[COORDINATOR CLIENT] coordinator %s -> %s",
            previous,
            coordinator_id,
        )

        if self.sink is not None:
            # 새 coordinator가 현재 lease 보유자를 다시 확인해 줄 때까지
            # 예전 authorization 상태를 비워 둔다.
            self.sink.set_authorized_controller(None)

        if self.router is None:
            return

        target_id = self.router.get_selected_target()
        if not target_id:
            return

        state = self.router.get_target_state()
        if state == "pending":
            self.claim(target_id)
        elif state == "active":
            self.heartbeat(target_id)

    def _on_grant(self, peer_id, frame):
        target_id = frame.get("target_id")
        controller_id = frame.get("controller_id")
        lease_ttl_ms = frame.get("lease_ttl_ms", DEFAULT_LEASE_TTL_MS)
        if controller_id != self.ctx.self_node.node_id or not target_id:
            return

        if self._requested_target_id and target_id != self._requested_target_id:
            logging.info(
                "[COORDINATOR CLIENT] stale GRANT target=%s requested=%s",
                target_id,
                self._requested_target_id,
            )
            self.release(target_id)
            return

        logging.info(
            "[COORDINATOR CLIENT] GRANT target=%s ttl_ms=%s",
            target_id,
            lease_ttl_ms,
        )
        self._requested_target_id = target_id
        if self.router is not None:
            self.router.activate_target(target_id)

    def _on_deny(self, peer_id, frame):
        target_id = frame.get("target_id")
        controller_id = frame.get("controller_id")
        reason = frame.get("reason")
        if controller_id != self.ctx.self_node.node_id or not target_id:
            return

        logging.info("[COORDINATOR CLIENT] DENY target=%s reason=%s", target_id, reason)
        if target_id != self._requested_target_id:
            return

        self._requested_target_id = None
        if self.router is not None:
            self.router.clear_target(reason=f"deny:{reason}")

    def _on_lease_update(self, peer_id, frame):
        target_id = frame.get("target_id")
        controller_id = frame.get("controller_id")
        coordinator_node = self.coordinator_resolver()
        coordinator_id = None if coordinator_node is None else coordinator_node.node_id
        if (
            self.sink is not None
            and target_id == self.ctx.self_node.node_id
            and peer_id == coordinator_id
        ):
            self.sink.set_authorized_controller(controller_id)
