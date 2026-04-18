"""Controller와 target 양쪽에서 쓰는 coordinator client."""

import logging
import threading
import time

from coordinator.protocol import (
    DEFAULT_LEASE_TTL_MS,
    make_auto_switch_update_request,
    make_claim,
    make_heartbeat,
    make_layout_edit_begin,
    make_layout_edit_end,
    make_monitor_inventory_refresh_request,
    make_monitor_inventory_publish,
    make_local_input_override,
    make_layout_update_request,
    make_node_list_update_request,
    make_node_note_update_request,
    make_remote_update_request,
    make_remote_update_status,
    make_release,
)
from runtime.context import NodeInfo
from runtime.app_logging import log_detail
from runtime.monitor_inventory import (
    MonitorInventorySnapshot,
    deserialize_monitor_inventory_snapshot,
    serialize_monitor_inventory_snapshot,
)
from runtime.layouts import build_layout_config, serialize_layout_config


class CoordinatorClient:
    HEARTBEAT_INTERVAL_SEC = 1.0
    CONTROL_POLL_INTERVAL_SEC = 0.5
    LAYOUT_EDIT_RETRY_INTERVAL_SEC = 1.0

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
        self._layout_edit_requested_at = 0.0
        self._layout_last_deny_reason = None
        self._layout_last_update_revision = -1
        self._latest_monitor_inventory = None
        self._monitor_inventory_manager = None
        self._monitor_inventory_refresh_states = {}
        self._local_override_pending_controller_id = None
        self._requested_target_source = None
        self._target_result_listeners = []
        self._remote_update_handler = None
        self._remote_update_status_handler = None
        self._auto_switch_change_handler = None
        self._node_list_change_listeners = []
        self._stop = threading.Event()
        self._thread = None

        dispatcher.register_control_handler("ctrl.grant", self._on_grant)
        dispatcher.register_control_handler("ctrl.deny", self._on_deny)
        dispatcher.register_control_handler("ctrl.lease_update", self._on_lease_update)
        dispatcher.register_control_handler("ctrl.layout_edit_grant", self._on_layout_edit_grant)
        dispatcher.register_control_handler("ctrl.layout_edit_deny", self._on_layout_edit_deny)
        dispatcher.register_control_handler("ctrl.layout_state", self._on_layout_state)
        dispatcher.register_control_handler("ctrl.layout_update", self._on_layout_update)
        dispatcher.register_control_handler("ctrl.monitor_inventory_state", self._on_monitor_inventory_state)
        dispatcher.register_control_handler(
            "ctrl.monitor_inventory_refresh_request",
            self._on_monitor_inventory_refresh_request,
        )
        dispatcher.register_control_handler(
            "ctrl.monitor_inventory_refresh_status",
            self._on_monitor_inventory_refresh_status,
        )
        dispatcher.register_control_handler(
            "ctrl.node_list_state",
            self._on_node_list_state,
        )
        dispatcher.register_control_handler(
            "ctrl.node_note_update_state",
            self._on_node_note_update_state,
        )
        dispatcher.register_control_handler(
            "ctrl.remote_update_command",
            self._on_remote_update_command,
        )
        dispatcher.register_control_handler(
            "ctrl.remote_update_status",
            self._on_remote_update_status,
        )
        if hasattr(registry, "add_unbind_listener"):
            registry.add_unbind_listener(self._on_peer_unbound)

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

    def set_monitor_inventory_manager(self, manager):
        self._monitor_inventory_manager = manager

    def add_target_result_listener(self, listener):
        self._target_result_listeners.append(listener)

    def set_remote_update_handler(self, handler):
        self._remote_update_handler = handler

    def set_remote_update_status_handler(self, handler):
        self._remote_update_status_handler = handler

    def set_auto_switch_change_handler(self, handler):
        self._auto_switch_change_handler = handler

    def add_node_list_change_listener(self, listener):
        self._node_list_change_listeners.append(listener)

    def _router_requested_target(self):
        if self.router is None:
            return None
        if hasattr(self.router, "get_requested_target"):
            return self.router.get_requested_target()
        return self.router.get_selected_target()

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

    def _on_peer_unbound(self, node_id: str) -> None:
        if self.router is None:
            return
        target_id = self._router_requested_target()
        if not target_id or target_id != node_id:
            return
        logging.info("[COORDINATOR CLIENT] clearing disconnected target=%s", node_id)
        self._requested_target_id = None
        source = self._requested_target_source
        self._requested_target_source = None
        self.router.clear_target(reason="target-offline")
        self._notify_target_result("failed", node_id, "target_offline", source)

    def claim(self, target_id: str) -> bool:
        return self._send(make_claim(target_id, self.ctx.self_node.node_id))

    def release(self, target_id: str) -> bool:
        return self._send(make_release(target_id, self.ctx.self_node.node_id))

    def heartbeat(self, target_id: str) -> bool:
        return self._send(make_heartbeat(target_id, self.ctx.self_node.node_id))

    def request_target(self, target_id: str, *, source: str | None = None) -> bool:
        if self.router is None:
            started = self.claim(target_id)
            if not started:
                self._notify_target_result("failed", target_id, "coordinator_unreachable", source)
            return started

        if self._requested_target_id == target_id:
            if source is not None:
                self._requested_target_source = source
            if self.router.get_target_state() == "pending":
                logging.info(
                    "[COORDINATOR CLIENT] pending target=%s 재-claim",
                    target_id,
                )
                started = self.claim(target_id)
                if not started:
                    self._notify_target_result(
                        "failed",
                        target_id,
                        "coordinator_unreachable",
                        self._requested_target_source,
                    )
                return started
            return True

        previous_target = self._requested_target_id
        self._requested_target_id = target_id
        self._requested_target_source = source

        if previous_target and previous_target != target_id:
            self.release(previous_target)

        self.router.set_pending_target(target_id)
        started = self.claim(target_id)
        if not started:
            self._requested_target_id = None
            failed_source = self._requested_target_source
            self._requested_target_source = None
            if self._router_requested_target() == target_id:
                self.router.clear_target(reason="claim-send-failed")
            self._notify_target_result("failed", target_id, "coordinator_unreachable", failed_source)
        return started

    def clear_target(self) -> None:
        target_id = self._requested_target_id
        self._requested_target_id = None
        self._requested_target_source = None
        if target_id:
            self.release(target_id)
        if self.router is not None:
            self.router.clear_target(reason="coordinator-clear")

    def request_auto_switch_enabled(self, enabled: bool) -> bool:
        return self._send(
            make_auto_switch_update_request(
                enabled=bool(enabled),
                requester_id=self.ctx.self_node.node_id,
            )
        )

    def request_remote_update(self, target_id: str) -> bool:
        return self._send(
            make_remote_update_request(
                target_id=target_id,
                requester_id=self.ctx.self_node.node_id,
            )
        )

    def report_remote_update_status(
        self,
        *,
        target_id: str,
        requester_id: str,
        status: str,
        detail: str = "",
        event_id: str = "",
        session_id: str = "",
        current_version: str = "",
        latest_version: str = "",
    ) -> bool:
        if not target_id or not requester_id or not status:
            return False
        return self._send(
            make_remote_update_status(
                target_id=target_id,
                requester_id=requester_id,
                status=status,
                detail=detail,
                coordinator_epoch=str(self._coordinator_epoch or ""),
                event_id=event_id,
                session_id=session_id,
                current_version=current_version,
                latest_version=latest_version,
            )
        )

    def request_node_note_update(self, node_id: str, note: str) -> bool:
        return self._send(
            make_node_note_update_request(
                node_id=node_id,
                note=note,
                requester_id=self.ctx.self_node.node_id,
            )
        )

    def request_node_list_update(
        self,
        nodes: list[dict],
        *,
        rename_map: dict[str, str] | None = None,
    ) -> bool:
        return self._send(
            make_node_list_update_request(
                nodes=nodes,
                requester_id=self.ctx.self_node.node_id,
                rename_map=rename_map,
            )
        )

    def request_layout_edit(self) -> bool:
        if self.is_layout_editor():
            self._layout_edit_requested = True
            self._layout_edit_requested_at = time.monotonic()
            self._layout_last_deny_reason = None
            return True
        self._layout_edit_requested = True
        self._layout_edit_requested_at = time.monotonic()
        self._layout_last_deny_reason = None
        return self._send(make_layout_edit_begin(self.ctx.self_node.node_id))

    def end_layout_edit(self) -> bool:
        self._layout_edit_requested = False
        self._layout_edit_requested_at = 0.0
        self._layout_last_deny_reason = None
        if self._layout_editor_id != self.ctx.self_node.node_id:
            return True
        self._layout_editor_id = None
        return self._send(make_layout_edit_end(self.ctx.self_node.node_id))

    def publish_layout(self, layout, persist: bool = True) -> bool:
        if not self.is_layout_editor():
            logging.info("[COORDINATOR CLIENT] ignore layout publish without edit lock")
            return False
        return self._send(
            make_layout_update_request(
                layout=serialize_layout_config(layout),
                editor_id=self.ctx.self_node.node_id,
                persist=persist,
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

    def publish_monitor_inventory(self, snapshot: MonitorInventorySnapshot) -> bool:
        self._latest_monitor_inventory = snapshot
        self.ctx.replace_monitor_inventory(snapshot)
        return self._send(
            make_monitor_inventory_publish(serialize_monitor_inventory_snapshot(snapshot))
        )

    def notify_local_input_override(self) -> bool:
        if self.sink is None:
            return False
        controller_id = self.sink.get_authorized_controller()
        if not controller_id or controller_id == self.ctx.self_node.node_id:
            return False
        if self._local_override_pending_controller_id == controller_id:
            return True
        sent = self._send(
            make_local_input_override(
                target_id=self.ctx.self_node.node_id,
                controller_id=controller_id,
            )
        )
        if sent:
            self._local_override_pending_controller_id = controller_id
        return sent

    def request_monitor_inventory_refresh(self, node_id: str) -> bool:
        if not node_id:
            return False
        self._monitor_inventory_refresh_states[node_id] = {
            "status": "pending",
            "detail": "재감지 요청을 보내는 중입니다.",
        }
        if node_id == self.ctx.self_node.node_id:
            if self._monitor_inventory_manager is None:
                self._monitor_inventory_refresh_states[node_id] = {
                    "status": "error",
                    "detail": "로컬 모니터 감지 관리자가 없습니다.",
                }
                return False
            started = self._monitor_inventory_manager.refresh_async()
            self._monitor_inventory_refresh_states[node_id] = {
                "status": "requested" if started else "pending",
                "detail": "로컬 모니터를 다시 감지하는 중입니다."
                if started
                else "이미 로컬 모니터 재감지가 진행 중입니다.",
            }
            return started
        sent = self._send(
            make_monitor_inventory_refresh_request(
                node_id=node_id,
                requester_id=self.ctx.self_node.node_id,
            )
        )
        if not sent:
            self._monitor_inventory_refresh_states[node_id] = {
                "status": "error",
                "detail": "재감지 요청을 coordinator로 보내지 못했습니다.",
            }
        return sent

    def get_monitor_inventory_refresh_state(self, node_id: str) -> dict | None:
        if not node_id:
            return None
        return self._monitor_inventory_refresh_states.get(node_id)

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

        if self._layout_edit_requested and not self.is_layout_editor():
            now = time.monotonic()
            if now - self._layout_edit_requested_at >= self.LAYOUT_EDIT_RETRY_INTERVAL_SEC:
                if self._send(make_layout_edit_begin(self.ctx.self_node.node_id)):
                    self._layout_edit_requested_at = now

        if self.router is None:
            return 0.0, None

        target_id = self._router_requested_target()
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
        self._layout_edit_requested_at = 0.0
        self._local_override_pending_controller_id = None
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
        if self._latest_monitor_inventory is not None:
            self._send(
                make_monitor_inventory_publish(
                    serialize_monitor_inventory_snapshot(self._latest_monitor_inventory)
                )
            )

        if self.router is None:
            return

        target_id = self._router_requested_target()
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
        source = self._requested_target_source
        if self.router is not None:
            self.router.activate_target(target_id)
        self._notify_target_result("active", target_id, None, source)

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
        source = self._requested_target_source
        self._requested_target_source = None
        if self.router is not None:
            self.router.clear_target(reason=f"deny:{reason}")
        self._notify_target_result("failed", target_id, reason, source)

    def _on_lease_update(self, peer_id, frame):
        target_id = frame.get("target_id")
        controller_id = frame.get("controller_id")
        if (
            self.sink is not None
            and target_id == self.ctx.self_node.node_id
            and self._accept_coordinator_frame(peer_id, frame.get("coordinator_epoch"))
        ):
            if controller_id != self._local_override_pending_controller_id:
                self._local_override_pending_controller_id = None
            self.sink.set_authorized_controller(controller_id)

    def _on_layout_edit_grant(self, peer_id, frame):
        editor_id = frame.get("editor_id")
        if (
            editor_id != self.ctx.self_node.node_id
            or not self._accept_coordinator_frame(peer_id, frame.get("coordinator_epoch"))
        ):
            return

        self._layout_editor_id = editor_id
        self._layout_edit_requested_at = 0.0
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
        self._layout_edit_requested_at = 0.0
        self._layout_editor_id = frame.get("current_editor_id")
        self._layout_last_deny_reason = reason
        logging.info("[COORDINATOR CLIENT] layout edit denied reason=%s", reason)

    def _on_layout_state(self, peer_id, frame):
        if not self._accept_coordinator_frame(peer_id, frame.get("coordinator_epoch")):
            return
        self._layout_editor_id = frame.get("editor_id")
        if self._layout_editor_id == self.ctx.self_node.node_id:
            self._layout_last_deny_reason = None
        elif self._layout_editor_id is not None and self._layout_edit_requested:
            self._layout_edit_requested = False

    def _on_layout_update(self, peer_id, frame):
        bootstrap = bool(frame.get("bootstrap"))
        if not bootstrap and not self._accept_coordinator_frame(peer_id, frame.get("coordinator_epoch")):
            return

        raw_layout = frame.get("layout")
        revision = frame.get("revision")
        persist = bool(frame.get("persist", True))
        change_kind = str(frame.get("change_kind") or "").strip()
        requester_id = str(frame.get("requester_id") or "").strip()
        if not isinstance(raw_layout, dict):
            return
        if not isinstance(revision, int):
            return
        if not bootstrap and revision < self._layout_last_update_revision:
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
                persist=persist,
                debounce_persist=False,
            )
        else:
            self.ctx.replace_layout(layout)

        self._layout_last_update_revision = revision
        self._layout_editor_id = frame.get("editor_id") or None
        log_detail(
            "[COORDINATOR CLIENT] applied layout revision=%s editor=%s persist=%s bootstrap=%s",
            revision,
            self._layout_editor_id,
            persist,
            bootstrap,
        )
        if (
            change_kind == "auto_switch_toggle"
            and requester_id
            and requester_id != self.ctx.self_node.node_id
            and callable(self._auto_switch_change_handler)
        ):
            self._auto_switch_change_handler(
                {
                    "enabled": bool(layout.auto_switch.enabled),
                    "requester_id": requester_id,
                    "coordinator_epoch": frame.get("coordinator_epoch"),
                }
            )

    def _on_monitor_inventory_state(self, peer_id, frame):
        if not self._accept_coordinator_frame(peer_id, frame.get("coordinator_epoch")):
            return
        raw_snapshot = frame.get("snapshot")
        if not isinstance(raw_snapshot, dict):
            return
        snapshot = deserialize_monitor_inventory_snapshot(raw_snapshot)
        if not snapshot.node_id:
            return
        self._monitor_inventory_refresh_states[snapshot.node_id] = {
            "status": "updated",
            "detail": "최신 모니터 감지 정보가 도착했습니다.",
        }
        if self.config_reloader is not None:
            self.config_reloader.apply_monitor_inventory(snapshot, persist=True)
        else:
            self.ctx.replace_monitor_inventory(snapshot)

    def _on_monitor_inventory_refresh_request(self, _peer_id, frame):
        node_id = frame.get("node_id")
        if node_id != self.ctx.self_node.node_id or self._monitor_inventory_manager is None:
            return
        self._monitor_inventory_manager.refresh_async()

    def _on_monitor_inventory_refresh_status(self, peer_id, frame):
        requester_id = frame.get("requester_id")
        node_id = frame.get("node_id")
        status = frame.get("status")
        detail = frame.get("detail")
        if (
            requester_id != self.ctx.self_node.node_id
            or not node_id
            or not isinstance(status, str)
            or not self._accept_coordinator_frame(peer_id, frame.get("coordinator_epoch"))
        ):
            return
        self._monitor_inventory_refresh_states[node_id] = {
            "status": status,
            "detail": detail or "",
        }

    def _on_node_note_update_state(self, peer_id, frame):
        node_id = frame.get("node_id")
        note = frame.get("note", "")
        if not node_id or not isinstance(note, str):
            return
        if not self._accept_coordinator_frame(peer_id, frame.get("coordinator_epoch")):
            return
        if self.config_reloader is not None:
            self.config_reloader.apply_node_note(str(node_id), note, persist=True)
            return
        node = self.ctx.get_node(str(node_id))
        if node is None:
            return
        updated = []
        for current in self.ctx.nodes:
            if current.node_id == str(node_id):
                updated.append(type(current)(name=current.name, ip=current.ip, port=current.port, note=note))
            else:
                updated.append(current)
        self.ctx.replace_nodes(updated)

    def _on_node_list_state(self, peer_id, frame):
        raw_nodes = frame.get("nodes")
        rename_map = frame.get("rename_map") or {}
        if not isinstance(raw_nodes, list) or not isinstance(rename_map, dict):
            return
        if not self._accept_coordinator_frame(peer_id, frame.get("coordinator_epoch")):
            return
        previous_nodes = {
            node.node_id: node
            for node in getattr(self.ctx, "nodes", ())
        }
        next_payloads = [node for node in raw_nodes if isinstance(node, dict)]
        next_node_ids = {
            str(node.get("name") or "").strip()
            for node in next_payloads
            if str(node.get("name") or "").strip()
        }
        added_node_ids = tuple(
            node_id
            for node_id in sorted(next_node_ids)
            if node_id not in previous_nodes and node_id != self.ctx.self_node.node_id
        )
        if self.config_reloader is not None and hasattr(self.config_reloader, "apply_nodes_state"):
            self.config_reloader.apply_nodes_state(
                raw_nodes,
                rename_map=rename_map,
                persist=True,
                apply_runtime=True,
            )
        else:
            self.ctx.replace_nodes([NodeInfo.from_dict(node) for node in raw_nodes if isinstance(node, dict)])
        if added_node_ids:
            payload = {
                "added_node_ids": added_node_ids,
                "coordinator_epoch": frame.get("coordinator_epoch"),
            }
            for listener in list(self._node_list_change_listeners):
                try:
                    listener(dict(payload))
                except Exception as exc:
                    logging.warning("[COORDINATOR CLIENT] node list change listener failed: %s", exc)

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

    def _notify_target_result(
        self,
        status: str,
        target_id: str,
        reason: str | None,
        source: str | None = None,
    ) -> None:
        for listener in list(self._target_result_listeners):
            try:
                listener(status, target_id, reason, source)
            except Exception as exc:
                logging.warning("[COORDINATOR CLIENT] target result listener failed: %s", exc)

    def _on_remote_update_command(self, _peer_id, frame):
        if frame.get("target_id") != self.ctx.self_node.node_id:
            return
        if callable(self._remote_update_handler):
            self._remote_update_handler(
                {
                    "target_id": frame.get("target_id"),
                    "requester_id": frame.get("requester_id"),
                    "coordinator_epoch": frame.get("coordinator_epoch"),
                }
            )

    def _on_remote_update_status(self, peer_id, frame):
        if frame.get("requester_id") != self.ctx.self_node.node_id:
            return
        if not self._accept_remote_update_status_frame(peer_id, frame):
            return
        if callable(self._remote_update_status_handler):
            self._remote_update_status_handler(
                {
                    "target_id": frame.get("target_id"),
                    "requester_id": frame.get("requester_id"),
                    "status": frame.get("status"),
                    "detail": frame.get("detail", ""),
                    "event_id": frame.get("event_id", ""),
                    "session_id": frame.get("session_id", ""),
                    "current_version": frame.get("current_version", ""),
                    "latest_version": frame.get("latest_version", ""),
                    "coordinator_epoch": frame.get("coordinator_epoch"),
                }
            )

    def _accept_remote_update_status_frame(self, peer_id, frame) -> bool:
        coordinator_node = self.coordinator_resolver()
        coordinator_id = None if coordinator_node is None else coordinator_node.node_id
        target_id = str(frame.get("target_id") or "").strip()
        if target_id and peer_id == target_id and coordinator_id == self.ctx.self_node.node_id:
            coordinator_epoch = frame.get("coordinator_epoch")
            if coordinator_epoch and self._coordinator_epoch is None:
                self._coordinator_epoch = coordinator_epoch
            return True
        return self._accept_coordinator_frame(peer_id, frame.get("coordinator_epoch"))
