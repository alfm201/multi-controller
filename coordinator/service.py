"""각 노드에서 상시 대기하는 coordinator service."""

import logging
import threading
import time

from coordinator.protocol import (
    DEFAULT_LEASE_TTL_MS,
    make_deny,
    make_grant,
    make_layout_edit_deny,
    make_layout_edit_grant,
    make_layout_state,
    make_layout_update,
    make_monitor_inventory_refresh_status,
    make_monitor_inventory_state,
    make_node_list_state,
    make_node_note_update_state,
    make_remote_update_command,
    make_lease_update,
)
from runtime.monitor_inventory import deserialize_monitor_inventory_snapshot, serialize_monitor_inventory_snapshot
from runtime.layouts import (
    build_layout_config,
    find_overlapping_nodes,
    replace_auto_switch_settings,
    serialize_layout_config,
)


class CoordinatorService:
    DEFAULT_LEASE_TTL_MS = DEFAULT_LEASE_TTL_MS
    EXPIRY_POLL_INTERVAL = 0.25

    def __init__(self, ctx, registry, dispatcher, config_reloader=None):
        self.ctx = ctx
        self.registry = registry
        self.dispatcher = dispatcher
        self.config_reloader = config_reloader

        self._lock = threading.Lock()
        self._leases = {}  # target_id -> {"controller_id": str, "expires_at": float}
        self._layout_editor_id = None
        self._layout_revision = 0
        self._monitor_inventories = {}
        self._coordinator_epoch = f"{self.ctx.self_node.node_id}:{time.time_ns()}"
        self._stop = threading.Event()
        self._thread = None

        dispatcher.register_control_handler("ctrl.claim", self._on_claim)
        dispatcher.register_control_handler("ctrl.release", self._on_release)
        dispatcher.register_control_handler("ctrl.local_input_override", self._on_local_input_override)
        dispatcher.register_control_handler("ctrl.heartbeat", self._on_heartbeat)
        dispatcher.register_control_handler("ctrl.layout_edit_begin", self._on_layout_edit_begin)
        dispatcher.register_control_handler("ctrl.layout_edit_end", self._on_layout_edit_end)
        dispatcher.register_control_handler("ctrl.layout_update_request", self._on_layout_update)
        dispatcher.register_control_handler(
            "ctrl.auto_switch_update_request",
            self._on_auto_switch_update_request,
        )
        dispatcher.register_control_handler("ctrl.monitor_inventory_publish", self._on_monitor_inventory_publish)
        dispatcher.register_control_handler(
            "ctrl.monitor_inventory_refresh_request",
            self._on_monitor_inventory_refresh_request,
        )
        dispatcher.register_control_handler(
            "ctrl.node_list_update_request",
            self._on_node_list_update_request,
        )
        dispatcher.register_control_handler(
            "ctrl.node_note_update_request",
            self._on_node_note_update_request,
        )
        dispatcher.register_control_handler(
            "ctrl.remote_update_request",
            self._on_remote_update_request,
        )
        registry.add_listener(self._on_registry_event)

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._expire_loop,
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

    def set_config_reloader(self, config_reloader) -> None:
        self.config_reloader = config_reloader

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

    def _broadcast(self, frame, include_self=True, only_peer_id=None):
        if only_peer_id is not None:
            return self._reply(only_peer_id, frame)

        if include_self:
            self.dispatcher.dispatch(self.ctx.self_node.node_id, frame)
        pairs = self.registry.all() if hasattr(self.registry, "all") else []
        for peer_id, conn in pairs:
            if conn is None or conn.closed:
                continue
            conn.send_frame(frame)
        return True

    def _send_lease_update(self, target_id, controller_id):
        frame = make_lease_update(
            target_id=target_id,
            controller_id=controller_id,
            coordinator_epoch=self._coordinator_epoch,
            lease_ttl_ms=self.DEFAULT_LEASE_TTL_MS,
        )
        if target_id == self.ctx.self_node.node_id:
            self.dispatcher.dispatch(target_id, frame)
            return True
        conn = self.registry.get(target_id)
        if conn is None:
            logging.debug("[COORDINATOR] target %s is not connected; skip lease_update", target_id)
            return False
        return conn.send_frame(frame)

    def _notify_target_locked(self, target_id):
        lease = self._leases.get(target_id)
        controller_id = None if lease is None else lease["controller_id"]
        self._send_lease_update(target_id, controller_id)

    def _broadcast_layout_state(self, only_peer_id=None):
        self._broadcast(
            make_layout_state(self._layout_editor_id, self._coordinator_epoch),
            include_self=only_peer_id is None,
            only_peer_id=only_peer_id,
        )

    def _broadcast_layout_snapshot(self, only_peer_id=None):
        if self.ctx.layout is None:
            return
        self._broadcast(
            make_layout_update(
                layout=serialize_layout_config(self.ctx.layout),
                editor_id=self._layout_editor_id or "",
                coordinator_epoch=self._coordinator_epoch,
                revision=self._layout_revision,
                persist=True,
            ),
            include_self=only_peer_id is None,
            only_peer_id=only_peer_id,
        )

    def _broadcast_layout_bootstrap(self, only_peer_id):
        if self.ctx.layout is None or only_peer_id is None:
            return
        self._broadcast(
            make_layout_update(
                layout=serialize_layout_config(self.ctx.layout),
                editor_id=self._layout_editor_id or "",
                coordinator_epoch=self._coordinator_epoch,
                revision=self._layout_revision,
                persist=True,
                bootstrap=True,
            ),
            include_self=False,
            only_peer_id=only_peer_id,
        )

    def _broadcast_monitor_inventory_snapshot(self, snapshot, only_peer_id=None):
        self._broadcast(
            make_monitor_inventory_state(
                snapshot=serialize_monitor_inventory_snapshot(snapshot),
                coordinator_epoch=self._coordinator_epoch,
            ),
            include_self=only_peer_id is None,
            only_peer_id=only_peer_id,
        )

    def _node_payloads(self) -> list[dict]:
        return [
            {
                "name": node.node_id,
                "ip": node.ip,
                "port": node.port,
                "note": getattr(node, "note", "") or "",
            }
            for node in self.ctx.nodes
        ]

    def _broadcast_node_list_snapshot(self, only_peer_id=None):
        self._broadcast(
            make_node_list_state(
                nodes=self._node_payloads(),
                coordinator_epoch=self._coordinator_epoch,
            ),
            include_self=only_peer_id is None,
            only_peer_id=only_peer_id,
        )

    def _validate_target(self, target_id):
        node = self.ctx.get_node(target_id)
        if node is None:
            return None, "unknown_target"
        return node, None

    def _is_effective_coordinator(self) -> bool:
        online_ids = {self.ctx.self_node.node_id}
        for peer_id, conn in self.registry.all():
            if conn is not None and not conn.closed and self.ctx.get_node(peer_id) is not None:
                online_ids.add(peer_id)
        return self.ctx.self_node.node_id == min(online_ids)

    def _was_coordinator_before_bound(self, joining_node_id: str) -> bool:
        online_ids = {self.ctx.self_node.node_id}
        for peer_id, conn in self.registry.all():
            if peer_id == joining_node_id:
                continue
            if conn is not None and not conn.closed and self.ctx.get_node(peer_id) is not None:
                online_ids.add(peer_id)
        return self.ctx.self_node.node_id == min(online_ids)

    def _target_is_online(self, target_id: str) -> bool:
        if target_id == self.ctx.self_node.node_id:
            return True
        conn = self.registry.get(target_id)
        return conn is not None and not conn.closed

    def _on_registry_event(self, event, node_id):
        if event == "bound":
            node = self.ctx.get_node(node_id)
            if node is not None:
                with self._lock:
                    self._notify_target_locked(node_id)
            with self._lock:
                effective_coordinator = self._is_effective_coordinator()
                if self._was_coordinator_before_bound(node_id) and not effective_coordinator:
                    self._broadcast_layout_bootstrap(only_peer_id=node_id)
                if effective_coordinator:
                    self._broadcast_node_list_snapshot(only_peer_id=node_id)
                    self._broadcast_layout_state(only_peer_id=node_id)
                    self._broadcast_layout_snapshot(only_peer_id=node_id)
                    for snapshot in self._monitor_inventories.values():
                        self._broadcast_monitor_inventory_snapshot(snapshot, only_peer_id=node_id)
            return

        if event == "unbound":
            released_targets = []
            with self._lock:
                if node_id == self._layout_editor_id:
                    logging.info("[COORDINATOR] layout editor released due to disconnect: %s", node_id)
                    self._layout_editor_id = None
                    self._broadcast_layout_state()
                for target_id, lease in list(self._leases.items()):
                    controller_id = lease["controller_id"]
                    if target_id == node_id:
                        released_targets.append((target_id, controller_id))
                        del self._leases[target_id]
                        self._notify_target_locked(target_id)
            for target_id, controller_id in released_targets:
                logging.info(
                    "[COORDINATOR] target disconnected; clearing lease target=%s controller=%s",
                    target_id,
                    controller_id,
                )
                self._reply(
                    controller_id,
                    make_deny(
                        target_id,
                        controller_id,
                        "target_offline",
                        coordinator_epoch=self._coordinator_epoch,
                    ),
                )
            return

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
            self._reply(
                peer_id,
                make_deny(
                    target_id,
                    controller_id,
                    error,
                    coordinator_epoch=self._coordinator_epoch,
                ),
            )
            return
        if not self._target_is_online(target_id):
            logging.info(
                "[COORDINATOR] DENY target=%s to %s (target_offline)",
                target_id,
                controller_id,
            )
            self._reply(
                peer_id,
                make_deny(
                    target_id,
                    controller_id,
                    "target_offline",
                    coordinator_epoch=self._coordinator_epoch,
                ),
            )
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
                    coordinator_epoch=self._coordinator_epoch,
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
            self._reply(
                peer_id,
                make_deny(
                    target_id,
                    controller_id,
                    "held_by_other",
                    coordinator_epoch=self._coordinator_epoch,
                ),
            )

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

    def _on_local_input_override(self, peer_id, frame):
        target_id = frame.get("target_id")
        controller_id = frame.get("controller_id")
        if not target_id or not controller_id:
            return
        if target_id != peer_id:
            logging.warning(
                "[COORDINATOR] ignore local_input_override target=%s from non-target peer=%s",
                target_id,
                peer_id,
            )
            return

        with self._lock:
            holder = self._leases.get(target_id)
            holder_id = None if holder is None else holder["controller_id"]
            if holder_id != controller_id:
                logging.info(
                    "[COORDINATOR] ignore local_input_override target=%s controller=%s holder=%s",
                    target_id,
                    controller_id,
                    holder_id,
                )
                return
            del self._leases[target_id]
            self._notify_target_locked(target_id)

        logging.info(
            "[COORDINATOR] LOCAL INPUT override target=%s controller=%s",
            target_id,
            controller_id,
        )
        self._reply(
            controller_id,
            make_deny(
                target_id,
                controller_id,
                "local_activity",
                coordinator_epoch=self._coordinator_epoch,
            ),
        )

    def _on_heartbeat(self, peer_id, frame):
        target_id = frame.get("target_id")
        controller_id = frame.get("controller_id") or peer_id
        if not target_id:
            return

        _, error = self._validate_target(target_id)
        if error is not None:
            return
        if not self._target_is_online(target_id):
            with self._lock:
                holder = self._leases.get(target_id)
                holder_id = None if holder is None else holder["controller_id"]
                if holder_id == controller_id:
                    del self._leases[target_id]
                    self._notify_target_locked(target_id)
            self._reply(
                controller_id,
                make_deny(
                    target_id,
                    controller_id,
                    "target_offline",
                    coordinator_epoch=self._coordinator_epoch,
                ),
            )
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

    def _on_layout_edit_begin(self, peer_id, frame):
        editor_id = frame.get("editor_id") or peer_id
        with self._lock:
            if self._layout_editor_id not in (None, editor_id):
                current_editor_id = self._layout_editor_id
                granted = False
            else:
                self._layout_editor_id = editor_id
                current_editor_id = self._layout_editor_id
                granted = True

        if granted:
            logging.info("[COORDINATOR] layout edit grant editor=%s", editor_id)
            self._reply(peer_id, make_layout_edit_grant(editor_id, self._coordinator_epoch))
            self._broadcast_layout_state()
        else:
            logging.info(
                "[COORDINATOR] layout edit deny editor=%s current=%s",
                editor_id,
                current_editor_id,
            )
            self._reply(
                peer_id,
                make_layout_edit_deny(
                    editor_id,
                    "held_by_other",
                    coordinator_epoch=self._coordinator_epoch,
                    current_editor_id=current_editor_id,
                ),
            )

    def _on_layout_edit_end(self, peer_id, frame):
        editor_id = frame.get("editor_id") or peer_id
        with self._lock:
            if self._layout_editor_id != editor_id:
                return
            self._layout_editor_id = None
            logging.info("[COORDINATOR] layout edit end editor=%s", editor_id)
            self._broadcast_layout_state()

    def _on_layout_update(self, peer_id, frame):
        editor_id = frame.get("editor_id") or peer_id
        raw_layout = frame.get("layout")
        persist = bool(frame.get("persist", True))
        if not isinstance(raw_layout, dict):
            logging.info("[COORDINATOR] ignore layout update without payload from %s", editor_id)
            return

        with self._lock:
            if self._layout_editor_id != editor_id:
                logging.info(
                    "[COORDINATOR] ignore layout update from non-editor %s current=%s",
                    editor_id,
                    self._layout_editor_id,
                )
                return

        try:
            layout = build_layout_config({"layout": raw_layout}, self.ctx.nodes)
        except Exception as exc:
            logging.warning("[COORDINATOR] invalid layout update from %s: %s", editor_id, exc)
            return
        overlaps = find_overlapping_nodes(layout)
        if overlaps:
            logging.warning("[COORDINATOR] ignore overlapping layout from %s: %s", editor_id, overlaps)
            return

        with self._lock:
            self._layout_revision += 1
            revision = self._layout_revision

        logging.info(
            "[COORDINATOR] layout update editor=%s revision=%s persist=%s",
            editor_id,
            revision,
            persist,
        )
        self._broadcast(
            make_layout_update(
                layout=serialize_layout_config(layout),
                editor_id=editor_id,
                coordinator_epoch=self._coordinator_epoch,
                revision=revision,
                persist=persist,
            )
        )

    def _on_auto_switch_update_request(self, peer_id, frame):
        enabled = frame.get("enabled")
        if not isinstance(enabled, bool):
            return
        if self.ctx.layout is None:
            return

        next_layout = replace_auto_switch_settings(self.ctx.layout, enabled=enabled)
        self.ctx.replace_layout(next_layout)

        with self._lock:
            self._layout_revision += 1
            revision = self._layout_revision

        logging.info(
            "[COORDINATOR] auto switch update requester=%s enabled=%s revision=%s",
            frame.get("requester_id") or peer_id,
            enabled,
            revision,
        )
        self._broadcast(
            make_layout_update(
                layout=serialize_layout_config(next_layout),
                editor_id="",
                coordinator_epoch=self._coordinator_epoch,
                revision=revision,
                persist=True,
            )
        )

    def _on_monitor_inventory_publish(self, peer_id, frame):
        raw_snapshot = frame.get("snapshot")
        if not isinstance(raw_snapshot, dict):
            return
        snapshot = deserialize_monitor_inventory_snapshot(raw_snapshot)
        if not snapshot.node_id:
            return
        with self._lock:
            self._monitor_inventories[snapshot.node_id] = snapshot
            self.ctx.replace_monitor_inventory(snapshot)
            self._broadcast_monitor_inventory_snapshot(snapshot)

    def _on_monitor_inventory_refresh_request(self, peer_id, frame):
        target_id = frame.get("node_id")
        requester_id = frame.get("requester_id") or peer_id
        if not target_id:
            return

        target = self.ctx.get_node(target_id)
        if target is None:
            self._reply(
                requester_id,
                make_monitor_inventory_refresh_status(
                    node_id=target_id,
                    requester_id=requester_id,
                    status="unknown",
                    detail="알 수 없는 노드라서 재감지를 요청할 수 없습니다.",
                    coordinator_epoch=self._coordinator_epoch,
                ),
            )
            return

        if target_id == self.ctx.self_node.node_id:
            self.dispatcher.dispatch(
                target_id,
                {
                    "kind": "ctrl.monitor_inventory_refresh_request",
                    "node_id": target_id,
                    "requester_id": requester_id,
                },
            )
            self._reply(
                requester_id,
                make_monitor_inventory_refresh_status(
                    node_id=target_id,
                    requester_id=requester_id,
                    status="requested",
                    detail="로컬 coordinator가 모니터 재감지를 시작했습니다.",
                    coordinator_epoch=self._coordinator_epoch,
                ),
            )
            return

        conn = self.registry.get(target_id)
        if conn is None or conn.closed:
            self._reply(
                requester_id,
                make_monitor_inventory_refresh_status(
                    node_id=target_id,
                    requester_id=requester_id,
                    status="offline",
                    detail="현재 오프라인이라 원격 모니터 재감지를 요청할 수 없습니다.",
                    coordinator_epoch=self._coordinator_epoch,
                ),
            )
            return

        conn.send_frame(
            {
                "kind": "ctrl.monitor_inventory_refresh_request",
                "node_id": target_id,
                "requester_id": requester_id,
            }
        )
        self._reply(
            requester_id,
            make_monitor_inventory_refresh_status(
                node_id=target_id,
                requester_id=requester_id,
                status="requested",
                detail="원격 PC에 모니터 재감지를 요청했습니다.",
                coordinator_epoch=self._coordinator_epoch,
            ),
        )

    def _on_remote_update_request(self, peer_id, frame):
        target_id = frame.get("target_id")
        requester_id = frame.get("requester_id") or peer_id
        if not target_id:
            return
        target = self.ctx.get_node(target_id)
        if target is None:
            return
        if not self._target_is_online(target_id):
            return
        self._reply(
            target_id,
            make_remote_update_command(
                target_id=target_id,
                requester_id=requester_id,
                coordinator_epoch=self._coordinator_epoch,
            ),
        )

    def _on_node_list_update_request(self, peer_id, frame):
        raw_nodes = frame.get("nodes")
        rename_map = frame.get("rename_map") or {}
        if not isinstance(raw_nodes, list) or not isinstance(rename_map, dict):
            return
        if self.config_reloader is None or not hasattr(self.config_reloader, "apply_nodes_state"):
            logging.warning("[COORDINATOR] ignore node list update without config reloader")
            return
        try:
            self.config_reloader.apply_nodes_state(
                raw_nodes,
                rename_map=rename_map,
                persist=True,
                apply_runtime=True,
            )
        except Exception as exc:
            logging.warning("[COORDINATOR] failed node list update from %s: %s", peer_id, exc)
            return
        self._broadcast(
            make_node_list_state(
                nodes=self._node_payloads(),
                rename_map=rename_map,
                coordinator_epoch=self._coordinator_epoch,
            )
        )

    def _on_node_note_update_request(self, peer_id, frame):
        node_id = frame.get("node_id")
        note = frame.get("note", "")
        if not node_id or not isinstance(note, str):
            return
        node = self.ctx.get_node(node_id)
        if node is None:
            return
        updated_nodes = []
        for current in self.ctx.nodes:
            if current.node_id == node_id:
                updated_nodes.append(type(current)(name=current.name, ip=current.ip, port=current.port, note=note))
            else:
                updated_nodes.append(current)
        self.ctx.replace_nodes(updated_nodes)
        self._broadcast(
            make_node_note_update_state(
                node_id=node_id,
                note=note,
                coordinator_epoch=self._coordinator_epoch,
            )
        )

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
