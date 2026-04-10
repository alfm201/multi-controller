"""Controller와 target 양쪽에서 쓰는 coordinator client."""

import logging
import threading

from coordinator.protocol import (
    DEFAULT_LEASE_TTL_MS,
    make_claim,
    make_heartbeat,
    make_layout_edit_begin,
    make_layout_edit_end,
    make_layout_update_request,
    make_release,
)
from runtime.layouts import build_layout_config, serialize_layout_config


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
        config_reloader=None,
    ):
        self.ctx = ctx
        self.registry = registry
        self.dispatcher = dispatcher
        self.coordinator_resolver = coordinator_resolver
        self.router = router
        self.sink = sink
        self.config_reloader = config_reloader

        self._requested_target_id = None
        self._last_coordinator_id = None
        self._coordinator_epoch = None
        self._layout_editor_id = None
        self._layout_edit_requested = False
        self._layout_last_deny_reason = None
        self._layout_last_update_revision = -1
        self._stop = threading.Event()
        self._thread = None

        dispatcher.register_control_handler("ctrl.grant", self._on_grant)
        dispatcher.register_control_handler("ctrl.deny", self._on_deny)
        dispatcher.register_control_handler("ctrl.lease_update", self._on_lease_update)
        dispatcher.register_control_handler("ctrl.layout_edit_grant", self._on_layout_edit_grant)
        dispatcher.register_control_handler("ctrl.layout_edit_deny", self._on_layout_edit_deny)
        dispatcher.register_control_handler("ctrl.layout_state", self._on_layout_state)
        dispatcher.register_control_handler("ctrl.layout_update", self._on_layout_update)

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

    def set_config_reloader(self, config_reloader):
        self.config_reloader = config_reloader

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

    def request_layout_edit(self) -> bool:
        self._layout_edit_requested = True
        self._layout_last_deny_reason = None
        return self._send(make_layout_edit_begin(self.ctx.self_node.node_id))

    def end_layout_edit(self) -> bool:
        self._layout_edit_requested = False
        self._layout_last_deny_reason = None
        if self._layout_editor_id != self.ctx.self_node.node_id:
            return True
        self._layout_editor_id = None
        return self._send(make_layout_edit_end(self.ctx.self_node.node_id))

    def publish_layout(self, layout) -> bool:
        if not self.is_layout_editor():
            logging.info("[COORDINATOR CLIENT] ignore layout publish without edit lock")
            return False
        return self._send(
            make_layout_update_request(
                layout=serialize_layout_config(layout),
                editor_id=self.ctx.self_node.node_id,
            )
        )

    def get_layout_editor(self) -> str | None:
        return self._layout_editor_id

    def is_layout_editor(self) -> bool:
        return self._layout_editor_id == self.ctx.self_node.node_id

    def is_layout_edit_pending(self) -> bool:
        return self._layout_edit_requested and not self.is_layout_editor()

    def get_layout_edit_denial(self) -> str | None:
        return self._layout_last_deny_reason

    def _control_loop(self):
        heartbeat_deadline = 0.0
        last_target_id = None
        while not self._stop.wait(self.CONTROL_POLL_INTERVAL_SEC):
            heartbeat_deadline, last_target_id = self._control_tick(
                heartbeat_deadline,
                last_target_id,
            )

    def _control_tick(self, heartbeat_deadline, last_target_id):
        """control loop 한 번 분량을 처리하고 다음 deadline 상태를 반환한다."""
        coordinator_node = self.coordinator_resolver()
        coordinator_id = None if coordinator_node is None else coordinator_node.node_id
        if coordinator_id != self._last_coordinator_id:
            self._on_coordinator_changed(coordinator_id)

        if self.router is None:
            return 0.0, None

        target_id = self.router.get_selected_target()
        state = self.router.get_target_state()

        if target_id != last_target_id:
            heartbeat_deadline = 0.0

        if not target_id:
            return 0.0, None

        if state == "pending":
            self.claim(target_id)
            return 0.0, target_id

        if state == "active":
            heartbeat_deadline += self.CONTROL_POLL_INTERVAL_SEC
            if heartbeat_deadline >= self.HEARTBEAT_INTERVAL_SEC:
                heartbeat_deadline = 0.0
                self.heartbeat(target_id)
            return heartbeat_deadline, target_id

        return 0.0, target_id

    def _on_coordinator_changed(self, coordinator_id):
        previous = self._last_coordinator_id
        self._last_coordinator_id = coordinator_id
        self._coordinator_epoch = None
        self._layout_editor_id = None
        self._layout_last_update_revision = -1
        logging.info(
            "[COORDINATOR CLIENT] coordinator %s -> %s",
            previous,
            coordinator_id,
        )

        if self.sink is not None:
            # 새 coordinator가 현재 lease 보유자를 다시 확인해 줄 때까지
            # 예전 authorization 상태를 비워 둔다.
            self.sink.set_authorized_controller(None)

        if self._layout_edit_requested:
            self.request_layout_edit()

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
        coordinator_epoch = frame.get("coordinator_epoch")
        lease_ttl_ms = frame.get("lease_ttl_ms", DEFAULT_LEASE_TTL_MS)
        if (
            controller_id != self.ctx.self_node.node_id
            or not target_id
            or not self._accept_coordinator_frame(peer_id, coordinator_epoch)
        ):
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
        coordinator_epoch = frame.get("coordinator_epoch")
        reason = frame.get("reason")
        if (
            controller_id != self.ctx.self_node.node_id
            or not target_id
            or not self._accept_coordinator_frame(peer_id, coordinator_epoch)
        ):
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
        if (
            self.sink is not None
            and target_id == self.ctx.self_node.node_id
            and self._accept_coordinator_frame(peer_id, frame.get("coordinator_epoch"))
        ):
            self.sink.set_authorized_controller(controller_id)

    def _on_layout_edit_grant(self, peer_id, frame):
        editor_id = frame.get("editor_id")
        if (
            editor_id != self.ctx.self_node.node_id
            or not self._accept_coordinator_frame(peer_id, frame.get("coordinator_epoch"))
        ):
            return

        self._layout_editor_id = editor_id
        self._layout_last_deny_reason = None
        logging.info("[COORDINATOR CLIENT] layout edit granted editor=%s", editor_id)

        if not self._layout_edit_requested:
            self.end_layout_edit()

    def _on_layout_edit_deny(self, peer_id, frame):
        editor_id = frame.get("editor_id")
        reason = frame.get("reason")
        if (
            editor_id != self.ctx.self_node.node_id
            or not self._accept_coordinator_frame(peer_id, frame.get("coordinator_epoch"))
        ):
            return

        self._layout_edit_requested = False
        self._layout_editor_id = frame.get("current_editor_id")
        self._layout_last_deny_reason = reason
        logging.info("[COORDINATOR CLIENT] layout edit denied reason=%s", reason)

    def _on_layout_state(self, peer_id, frame):
        if not self._accept_coordinator_frame(peer_id, frame.get("coordinator_epoch")):
            return
        self._layout_editor_id = frame.get("editor_id")

    def _on_layout_update(self, peer_id, frame):
        if not self._accept_coordinator_frame(peer_id, frame.get("coordinator_epoch")):
            return

        raw_layout = frame.get("layout")
        revision = frame.get("revision")
        if not isinstance(raw_layout, dict):
            return
        if not isinstance(revision, int):
            return
        if revision < self._layout_last_update_revision:
            logging.debug(
                "[COORDINATOR CLIENT] ignore stale layout revision=%s current=%s",
                revision,
                self._layout_last_update_revision,
            )
            return

        try:
            layout = build_layout_config({"layout": raw_layout}, self.ctx.nodes)
        except Exception as exc:
            logging.warning("[COORDINATOR CLIENT] invalid layout update: %s", exc)
            return

        if self.config_reloader is not None:
            self.config_reloader.apply_layout(
                layout,
                persist=True,
                debounce_persist=True,
            )
        else:
            self.ctx.replace_layout(layout)

        self._layout_last_update_revision = revision
        self._layout_editor_id = frame.get("editor_id") or None
        logging.info(
            "[COORDINATOR CLIENT] applied layout revision=%s editor=%s",
            revision,
            self._layout_editor_id,
        )

    def _accept_coordinator_frame(self, peer_id, coordinator_epoch) -> bool:
        coordinator_node = self.coordinator_resolver()
        coordinator_id = None if coordinator_node is None else coordinator_node.node_id
        if peer_id != coordinator_id:
            logging.debug(
                "[COORDINATOR CLIENT] ignore frame from stale coordinator %s (current=%s)",
                peer_id,
                coordinator_id,
            )
            return False
        if not coordinator_epoch:
            logging.debug("[COORDINATOR CLIENT] ignore frame without coordinator_epoch")
            return False
        if self._coordinator_epoch is None:
            self._coordinator_epoch = coordinator_epoch
            return True
        compare = self._compare_epoch_tokens(coordinator_epoch, self._coordinator_epoch)
        if compare < 0:
            logging.debug(
                "[COORDINATOR CLIENT] ignore stale epoch %s < %s",
                coordinator_epoch,
                self._coordinator_epoch,
            )
            return False
        if compare > 0:
            logging.info(
                "[COORDINATOR CLIENT] coordinator epoch %s -> %s",
                self._coordinator_epoch,
                coordinator_epoch,
            )
            self._coordinator_epoch = coordinator_epoch
            self._layout_editor_id = None
            self._layout_last_update_revision = -1
            if self.sink is not None:
                self.sink.set_authorized_controller(None)
        return True

    def _compare_epoch_tokens(self, new_epoch, current_epoch) -> int:
        if new_epoch == current_epoch:
            return 0
        try:
            new_node, new_counter = new_epoch.split(":", 1)
            current_node, current_counter = current_epoch.split(":", 1)
            if new_node == current_node:
                return (int(new_counter) > int(current_counter)) - (
                    int(new_counter) < int(current_counter)
                )
        except Exception:
            pass
        return 1
