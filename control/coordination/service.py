"""각 노드에서 상시 대기하는 coordinator service."""

import logging
import threading
import time
from uuid import uuid4

from control.coordination.election import DEFAULT_COORDINATOR_PRIORITY, pick_coordinator
from control.coordination.protocol import (
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
    make_update_check_command,
    make_update_check_state,
    make_update_download_command,
    make_update_download_state,
    make_remote_update_command,
    make_remote_update_status,
    make_lease_update,
)
from app.update.group_update import (
    GROUP_UPDATE_QUERY_CACHE_TTL_SEC,
    GROUP_UPDATE_SHARE_TTL_SEC,
    build_update_cache_key,
)
from model.display.monitor_inventory import deserialize_monitor_inventory_snapshot, serialize_monitor_inventory_snapshot
from app.logging.app_logging import log_detail
from model.display.layouts import (
    build_layout_config,
    find_overlapping_nodes,
    replace_auto_switch_settings,
    serialize_layout_config,
)


class CoordinatorService:
    DEFAULT_LEASE_TTL_MS = DEFAULT_LEASE_TTL_MS
    EXPIRY_POLL_INTERVAL = 0.25
    UPDATE_CHECK_CANDIDATE_TIMEOUT_SEC = 20.0
    UPDATE_DOWNLOAD_CANDIDATE_TIMEOUT_SEC = 25.0 * 60.0

    def __init__(self, ctx, registry, dispatcher, config_reloader=None, coordinator_resolver=None):
        self.ctx = ctx
        self.registry = registry
        self.dispatcher = dispatcher
        self.config_reloader = config_reloader
        self.coordinator_resolver = coordinator_resolver

        self._lock = threading.Lock()
        self._leases = {}  # target_id -> {"controller_id": str, "expires_at": float}
        self._layout_editor_id = None
        self._layout_revision = 0
        self._node_list_revision = 0
        self._monitor_inventories = {}
        self._update_check_cache = None
        self._update_check_inflight = None
        self._update_download_cache = {}
        self._update_download_jobs = {}
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
        dispatcher.register_control_handler(
            "ctrl.remote_update_status",
            self._on_remote_update_status,
        )
        dispatcher.register_control_handler(
            "ctrl.update_check_request",
            self._on_update_check_request,
        )
        dispatcher.register_control_handler(
            "ctrl.update_check_result",
            self._on_update_check_result,
        )
        dispatcher.register_control_handler(
            "ctrl.update_download_request",
            self._on_update_download_request,
        )
        dispatcher.register_control_handler(
            "ctrl.update_download_result",
            self._on_update_download_result,
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
            "[COORDINATOR] started on self=%s ttl_ms=%s",
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
        frame = self._make_lease_update_frame(target_id, controller_id)
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

    def _make_lease_update_frame(self, target_id, controller_id):
        return make_lease_update(
            target_id=target_id,
            controller_id=controller_id,
            coordinator_epoch=self._coordinator_epoch,
            lease_ttl_ms=self.DEFAULT_LEASE_TTL_MS,
        )

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
                "node_id": node.node_id,
                "name": node.name,
                "ip": node.ip,
                "port": node.port,
                "note": getattr(node, "note", "") or "",
                "priority": getattr(node, "priority", DEFAULT_COORDINATOR_PRIORITY),
            }
            for node in self.ctx.nodes
        ]

    def _update_candidate_ids(self) -> list[str]:
        ordered_nodes = sorted(
            self.ctx.nodes,
            key=lambda node: (
                0 if node.node_id == self.ctx.self_node.node_id else 1,
                1_000_000_000 if int(getattr(node, "priority", 0) or 0) <= 0 else int(getattr(node, "priority", 0)),
                node.node_id,
            ),
        )
        return [
            node.node_id
            for node in ordered_nodes
            if self._target_is_online(node.node_id)
        ]

    def _update_download_candidate_ids(self) -> list[str]:
        return self._update_candidate_ids()

    def _cached_update_check_state(self):
        cached = self._update_check_cache
        if not isinstance(cached, dict):
            return None
        fetched_at = float(cached.get("fetched_at", 0.0))
        if (time.monotonic() - fetched_at) > GROUP_UPDATE_QUERY_CACHE_TTL_SEC:
            return None
        result = cached.get("result")
        if not isinstance(result, dict):
            return None
        return {
            "status": "success",
            "detail": "",
            "result": dict(result),
            "source_id": str(cached.get("source_id") or ""),
        }

    def _cache_update_download_entry(self, cache_key: str, payload: dict) -> None:
        self._update_download_cache[str(cache_key)] = {
            "source_id": str(payload.get("source_id") or ""),
            "share_port": int(payload.get("share_port") or 0),
            "share_id": str(payload.get("share_id") or ""),
            "share_token": str(payload.get("share_token") or ""),
            "sha256": str(payload.get("sha256") or ""),
            "size_bytes": int(payload.get("size_bytes") or 0),
            "updated_at": time.monotonic(),
        }

    def _resolve_update_download_cache(self, cache_key: str) -> dict | None:
        payload = self._update_download_cache.get(str(cache_key))
        if not isinstance(payload, dict):
            return None
        updated_at = float(payload.get("updated_at", 0.0))
        if (time.monotonic() - updated_at) > GROUP_UPDATE_SHARE_TTL_SEC:
            return None
        source_id = str(payload.get("source_id") or "")
        if not source_id or not self._target_is_online(source_id):
            return None
        return dict(payload)

    def _reply_update_check_state(self, requester_id: str, request_id: str, payload: dict) -> None:
        self._reply(
            requester_id,
            make_update_check_state(
                requester_id=requester_id,
                request_id=request_id,
                status=str(payload.get("status") or ""),
                detail=str(payload.get("detail") or ""),
                result=payload.get("result") if isinstance(payload.get("result"), dict) else None,
                source_id=str(payload.get("source_id") or ""),
                coordinator_epoch=self._coordinator_epoch,
            ),
        )

    def _reply_update_download_state(self, requester_id: str, request_id: str, payload: dict) -> None:
        self._reply(
            requester_id,
            make_update_download_state(
                requester_id=requester_id,
                request_id=request_id,
                status=str(payload.get("status") or ""),
                detail=str(payload.get("detail") or ""),
                source_id=str(payload.get("source_id") or ""),
                share_port=int(payload.get("share_port") or 0),
                share_id=str(payload.get("share_id") or ""),
                share_token=str(payload.get("share_token") or ""),
                sha256=str(payload.get("sha256") or ""),
                size_bytes=int(payload.get("size_bytes") or 0),
                coordinator_epoch=self._coordinator_epoch,
            ),
        )

    def _dispatch_next_update_check_candidate(self) -> None:
        inflight = self._update_check_inflight
        if not isinstance(inflight, dict):
            return
        candidates = list(inflight.get("candidates") or ())
        while candidates:
            candidate_id = str(candidates.pop(0) or "")
            if not candidate_id or not self._target_is_online(candidate_id):
                continue
            inflight["candidates"] = candidates
            inflight["active_candidate_id"] = candidate_id
            self._reply(
                candidate_id,
                make_update_check_command(
                    job_id=str(inflight["job_id"]),
                    coordinator_epoch=self._coordinator_epoch,
                ),
            )
            return
        failure = {
            "status": "failed",
            "detail": "?낅뜲?댄듃 ?뺤씤??寃곌낵瑜?媛吏멸퀬 ?덈뒗 ?몃뱶瑜?李얠쓣 ???놁뒿?덈떎.",
            "result": None,
            "source_id": "",
        }
        for requester_id, request_id in inflight.get("requesters", ()):
            self._reply_update_check_state(requester_id, request_id, failure)
        self._update_check_inflight = None

    def _dispatch_next_update_download_candidate(self, cache_key: str) -> None:
        job = self._update_download_jobs.get(str(cache_key))
        if not isinstance(job, dict):
            return
        candidates = list(job.get("candidates") or ())
        while candidates:
            candidate_id = str(candidates.pop(0) or "")
            if not candidate_id or not self._target_is_online(candidate_id):
                continue
            job["candidates"] = candidates
            job["active_candidate_id"] = candidate_id
            self._reply(
                candidate_id,
                make_update_download_command(
                    job_id=str(job["job_id"]),
                    tag_name=str(job.get("tag_name") or ""),
                    installer_url=str(job.get("installer_url") or ""),
                    coordinator_epoch=self._coordinator_epoch,
                ),
            )
            return
        failure = {
            "status": "failed",
            "detail": "?ㅼ튂 ?뚯씪??諛쏆쓣 ???덈뒗 ?몃뱶瑜?李얠쓣 ???놁뒿?덈떎.",
            "source_id": "",
            "share_port": 0,
            "share_id": "",
            "share_token": "",
            "sha256": "",
            "size_bytes": 0,
        }
        for requester_id, request_id in job.get("requesters", ()):
            self._reply_update_download_state(requester_id, request_id, failure)
        self._update_download_jobs.pop(str(cache_key), None)

    def _broadcast_node_list_snapshot(self, only_peer_id=None):
        self._broadcast(
            make_node_list_state(
                nodes=self._node_payloads(),
                revision=self._node_list_revision,
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
        if callable(self.coordinator_resolver):
            coordinator = self.coordinator_resolver()
        else:
            coordinator = pick_coordinator(self.ctx, self.registry)
        return coordinator is not None and coordinator.node_id == self.ctx.self_node.node_id

    def _was_coordinator_before_bound(self, joining_node_id: str) -> bool:
        coordinator = pick_coordinator(
            self.ctx,
            self.registry,
            excluding_node_id=joining_node_id,
        )
        return coordinator is not None and coordinator.node_id == self.ctx.self_node.node_id

    def _target_is_online(self, target_id: str) -> bool:
        if target_id == self.ctx.self_node.node_id:
            return True
        conn = self.registry.get(target_id)
        return conn is not None and not conn.closed

    def _on_registry_event(self, event, node_id):
        if event == "bound":
            lease_frame = None
            bootstrap_frame = None
            node_list_frame = None
            layout_state_frame = None
            layout_snapshot_frame = None
            monitor_frames = []
            node = self.ctx.get_node(node_id)
            with self._lock:
                if node is not None:
                    lease = self._leases.get(node_id)
                    controller_id = None if lease is None else lease["controller_id"]
                    lease_frame = self._make_lease_update_frame(node_id, controller_id)
                effective_coordinator = self._is_effective_coordinator()
                if (
                    self.ctx.layout is not None
                    and self._was_coordinator_before_bound(node_id)
                    and not effective_coordinator
                ):
                    bootstrap_frame = make_layout_update(
                        layout=serialize_layout_config(self.ctx.layout),
                        editor_id=self._layout_editor_id or "",
                        coordinator_epoch=self._coordinator_epoch,
                        revision=self._layout_revision,
                        persist=True,
                        bootstrap=True,
                    )
                if effective_coordinator:
                    node_list_frame = make_node_list_state(
                        nodes=self._node_payloads(),
                        revision=self._node_list_revision,
                        coordinator_epoch=self._coordinator_epoch,
                    )
                    layout_state_frame = make_layout_state(
                        self._layout_editor_id,
                        self._coordinator_epoch,
                    )
                    if self.ctx.layout is not None:
                        layout_snapshot_frame = make_layout_update(
                            layout=serialize_layout_config(self.ctx.layout),
                            editor_id=self._layout_editor_id or "",
                            coordinator_epoch=self._coordinator_epoch,
                            revision=self._layout_revision,
                            persist=True,
                        )
                    monitor_frames = [
                        make_monitor_inventory_state(
                            snapshot=serialize_monitor_inventory_snapshot(snapshot),
                            coordinator_epoch=self._coordinator_epoch,
                        )
                        for snapshot in self._monitor_inventories.values()
                    ]
            if lease_frame is not None:
                self._reply(node_id, lease_frame)
            if bootstrap_frame is not None:
                self._reply(node_id, bootstrap_frame)
            if node_list_frame is not None:
                self._reply(node_id, node_list_frame)
            if layout_state_frame is not None:
                self._reply(node_id, layout_state_frame)
            if layout_snapshot_frame is not None:
                self._reply(node_id, layout_snapshot_frame)
            for frame in monitor_frames:
                self._reply(node_id, frame)
            return

        if event == "unbound":
            released_targets = []
            lease_clear_targets = []
            broadcast_layout_state = False
            retry_update_check = False
            retry_download_keys = []
            with self._lock:
                if node_id == self._layout_editor_id:
                    logging.info("[COORDINATOR] layout editor released due to disconnect: %s", node_id)
                    self._layout_editor_id = None
                    broadcast_layout_state = True
                for target_id, lease in list(self._leases.items()):
                    controller_id = lease["controller_id"]
                    if target_id == node_id:
                        released_targets.append((target_id, controller_id))
                        del self._leases[target_id]
                        lease_clear_targets.append(target_id)
                if (
                    isinstance(self._update_check_inflight, dict)
                    and str(self._update_check_inflight.get("active_candidate_id") or "") == node_id
                ):
                    self._update_check_inflight["active_candidate_id"] = ""
                    self._update_check_inflight["candidate_started_at"] = 0.0
                    retry_update_check = True
                stale_download_keys = [
                    key
                    for key, payload in self._update_download_cache.items()
                    if str(payload.get("source_id") or "") == node_id
                ]
                for key in stale_download_keys:
                    self._update_download_cache.pop(key, None)
                retry_download_keys = [
                    key
                    for key, payload in self._update_download_jobs.items()
                    if str(payload.get("active_candidate_id") or "") == node_id
                ]
                for key in retry_download_keys:
                    self._update_download_jobs[key]["active_candidate_id"] = ""
                    self._update_download_jobs[key]["candidate_started_at"] = 0.0
            if broadcast_layout_state:
                self._broadcast_layout_state()
            for target_id in lease_clear_targets:
                self._send_lease_update(target_id, None)
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
            if retry_update_check:
                self._dispatch_next_update_check_candidate()
            for cache_key in retry_download_keys:
                self._dispatch_next_update_download_candidate(cache_key)
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
            else:
                granted = False

        if granted:
            self._send_lease_update(target_id, controller_id)
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

        released = False
        with self._lock:
            holder = self._leases.get(target_id)
            holder_id = None if holder is None else holder["controller_id"]
            if holder_id == controller_id:
                del self._leases[target_id]
                released = True

        if released:
            self._send_lease_update(target_id, None)
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

        self._send_lease_update(target_id, None)
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
            cleared = False
            with self._lock:
                holder = self._leases.get(target_id)
                holder_id = None if holder is None else holder["controller_id"]
                if holder_id == controller_id:
                    del self._leases[target_id]
                    cleared = True
            if cleared:
                self._send_lease_update(target_id, None)
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
            restored = holder_id != controller_id
        if restored:
            self._send_lease_update(target_id, controller_id)
            logging.info(
                "[COORDINATOR] HEARTBEAT restored target=%s holder=%s",
                target_id,
                controller_id,
            )

    def _on_layout_edit_begin(self, peer_id, frame):
        editor_id = frame.get("editor_id") or peer_id
        current_editor_id = None
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
        released = False
        with self._lock:
            if self._layout_editor_id != editor_id:
                return
            self._layout_editor_id = None
            released = True
        if released:
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

        log_detail(
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
        requester_id = str(frame.get("requester_id") or peer_id)
        request_id = str(frame.get("request_id") or "").strip()

        next_layout = replace_auto_switch_settings(self.ctx.layout, enabled=enabled)
        self.ctx.replace_layout(next_layout)

        with self._lock:
            self._layout_revision += 1
            revision = self._layout_revision

        logging.info(
            "[COORDINATOR] auto switch update requester=%s enabled=%s revision=%s",
            requester_id,
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
                change_kind="auto_switch_toggle",
                requester_id=requester_id,
                request_id=request_id,
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
        request_id = str(frame.get("request_id") or "").strip()
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
                    request_id=request_id,
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
                    request_id=request_id,
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
                    request_id=request_id,
                ),
            )
            return

        conn.send_frame(
            {
                "kind": "ctrl.monitor_inventory_refresh_request",
                "node_id": target_id,
                "requester_id": requester_id,
                "request_id": request_id,
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
                request_id=request_id,
            ),
        )

    def _on_remote_update_request(self, peer_id, frame):
        target_id = frame.get("target_id")
        requester_id = frame.get("requester_id") or peer_id
        request_id = str(frame.get("request_id") or "").strip()
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
                request_id=request_id,
            ),
        )

    def _on_remote_update_status(self, peer_id, frame):
        target_id = frame.get("target_id")
        requester_id = frame.get("requester_id")
        status = frame.get("status")
        detail = frame.get("detail", "")
        reason = frame.get("reason", "")
        request_id = str(frame.get("request_id") or "").strip()
        event_id = frame.get("event_id", "")
        session_id = frame.get("session_id", "")
        current_version = frame.get("current_version", "")
        latest_version = frame.get("latest_version", "")
        if (
            not target_id
            or not requester_id
            or not isinstance(status, str)
            or not isinstance(detail, str)
        ):
            return
        if peer_id != target_id:
            return
        self._reply(
            requester_id,
            make_remote_update_status(
                target_id=target_id,
                requester_id=requester_id,
                status=status,
                detail=detail,
                reason=reason,
                request_id=request_id,
                coordinator_epoch=self._coordinator_epoch,
                event_id=str(event_id or ""),
                session_id=str(session_id or ""),
                current_version=str(current_version or ""),
                latest_version=str(latest_version or ""),
            ),
        )

    def _on_update_check_request(self, peer_id, frame):
        requester_id = str(frame.get("requester_id") or peer_id)
        request_id = str(frame.get("request_id") or "").strip()
        if not requester_id or not request_id:
            return
        cached = self._cached_update_check_state()
        if cached is not None:
            self._reply_update_check_state(requester_id, request_id, cached)
            return
        if isinstance(self._update_check_inflight, dict):
            requesters = list(self._update_check_inflight.get("requesters") or ())
            requesters.append((requester_id, request_id))
            self._update_check_inflight["requesters"] = requesters
            return
        self._update_check_inflight = {
            "job_id": uuid4().hex,
            "requesters": [(requester_id, request_id)],
            "candidates": self._update_candidate_ids(),
            "active_candidate_id": "",
        }
        self._dispatch_next_update_check_candidate()

    def _on_update_check_result(self, peer_id, frame):
        job_id = str(frame.get("job_id") or "").strip()
        status = str(frame.get("status") or "").strip()
        detail = str(frame.get("detail") or "")
        inflight = self._update_check_inflight
        if not job_id or not isinstance(inflight, dict):
            return
        if job_id != str(inflight.get("job_id") or ""):
            return
        if peer_id != str(inflight.get("active_candidate_id") or ""):
            return
        result_payload = frame.get("result") if isinstance(frame.get("result"), dict) else None
        source_id = str(frame.get("source_id") or peer_id)
        if status == "success" and result_payload is not None:
            self._update_check_cache = {
                "fetched_at": time.monotonic(),
                "result": dict(result_payload),
                "source_id": source_id,
            }
            payload = {
                "status": "success",
                "detail": detail,
                "result": dict(result_payload),
                "source_id": source_id,
            }
            for requester_id, request_id in inflight.get("requesters", ()):
                self._reply_update_check_state(requester_id, request_id, payload)
            self._update_check_inflight = None
            return
        inflight["active_candidate_id"] = ""
        self._dispatch_next_update_check_candidate()

    def _on_update_download_request(self, peer_id, frame):
        requester_id = str(frame.get("requester_id") or peer_id)
        request_id = str(frame.get("request_id") or "").strip()
        tag_name = str(frame.get("tag_name") or "").strip()
        installer_url = str(frame.get("installer_url") or "").strip()
        if not requester_id or not request_id or not tag_name or not installer_url:
            return
        cache_key = build_update_cache_key(tag_name=tag_name, installer_url=installer_url)
        cached = self._resolve_update_download_cache(cache_key)
        if cached is not None:
            payload = {"status": "ready", "detail": "", **cached}
            self._reply_update_download_state(requester_id, request_id, payload)
            return
        if cache_key in self._update_download_jobs:
            requesters = list(self._update_download_jobs[cache_key].get("requesters") or ())
            requesters.append((requester_id, request_id))
            self._update_download_jobs[cache_key]["requesters"] = requesters
            return
        self._update_download_jobs[cache_key] = {
            "job_id": uuid4().hex,
            "requesters": [(requester_id, request_id)],
            "candidates": self._update_download_candidate_ids(),
            "active_candidate_id": "",
            "tag_name": tag_name,
            "installer_url": installer_url,
        }
        self._dispatch_next_update_download_candidate(cache_key)

    def _on_update_download_result(self, peer_id, frame):
        job_id = str(frame.get("job_id") or "").strip()
        if not job_id:
            return
        matched_key = None
        matched_job = None
        for cache_key, payload in self._update_download_jobs.items():
            if str(payload.get("job_id") or "") == job_id:
                matched_key = cache_key
                matched_job = payload
                break
        if matched_key is None or not isinstance(matched_job, dict):
            return
        if peer_id != str(matched_job.get("active_candidate_id") or ""):
            return
        status = str(frame.get("status") or "").strip()
        if status == "ready":
            payload = {
                "source_id": str(frame.get("source_id") or peer_id),
                "share_port": int(frame.get("share_port") or 0),
                "share_id": str(frame.get("share_id") or ""),
                "share_token": str(frame.get("share_token") or ""),
                "sha256": str(frame.get("sha256") or ""),
                "size_bytes": int(frame.get("size_bytes") or 0),
            }
            self._cache_update_download_entry(matched_key, payload)
            reply_payload = {"status": "ready", "detail": "", **payload}
            for requester_id, request_id in matched_job.get("requesters", ()):
                self._reply_update_download_state(requester_id, request_id, reply_payload)
            self._update_download_jobs.pop(matched_key, None)
            return
        matched_job["active_candidate_id"] = ""
        self._dispatch_next_update_download_candidate(matched_key)

    def _dispatch_next_update_check_candidate(self) -> None:
        command_frame = None
        command_target_id = ""
        requesters = []
        with self._lock:
            inflight = self._update_check_inflight
            if not isinstance(inflight, dict):
                return
            candidates = list(inflight.get("candidates") or ())
            while candidates:
                candidate_id = str(candidates.pop(0) or "")
                if not candidate_id or not self._target_is_online(candidate_id):
                    continue
                inflight["candidates"] = candidates
                inflight["active_candidate_id"] = candidate_id
                inflight["candidate_started_at"] = self._now()
                command_target_id = candidate_id
                command_frame = make_update_check_command(
                    job_id=str(inflight["job_id"]),
                    coordinator_epoch=self._coordinator_epoch,
                )
                break
            if command_frame is None:
                requesters = list(inflight.get("requesters", ()))
                self._update_check_inflight = None
        if command_frame is not None:
            self._reply(command_target_id, command_frame)
            return
        failure = {
            "status": "failed",
            "detail": "그룹 내에서 업데이트 확인을 수행할 수 있는 노드를 찾지 못했습니다.",
            "result": None,
            "source_id": "",
        }
        for requester_id, request_id in requesters:
            self._reply_update_check_state(requester_id, request_id, failure)

    def _dispatch_next_update_download_candidate(self, cache_key: str) -> None:
        command_frame = None
        command_target_id = ""
        requesters = []
        with self._lock:
            job = self._update_download_jobs.get(str(cache_key))
            if not isinstance(job, dict):
                return
            candidates = list(job.get("candidates") or ())
            while candidates:
                candidate_id = str(candidates.pop(0) or "")
                if not candidate_id or not self._target_is_online(candidate_id):
                    continue
                job["candidates"] = candidates
                job["active_candidate_id"] = candidate_id
                job["candidate_started_at"] = self._now()
                command_target_id = candidate_id
                command_frame = make_update_download_command(
                    job_id=str(job["job_id"]),
                    tag_name=str(job.get("tag_name") or ""),
                    installer_url=str(job.get("installer_url") or ""),
                    coordinator_epoch=self._coordinator_epoch,
                )
                break
            if command_frame is None:
                requesters = list(job.get("requesters", ()))
                self._update_download_jobs.pop(str(cache_key), None)
        if command_frame is not None:
            self._reply(command_target_id, command_frame)
            return
        failure = {
            "status": "failed",
            "detail": "그룹 내에서 설치 파일을 준비할 수 있는 노드를 찾지 못했습니다.",
            "source_id": "",
            "share_port": 0,
            "share_id": "",
            "share_token": "",
            "sha256": "",
            "size_bytes": 0,
        }
        for requester_id, request_id in requesters:
            self._reply_update_download_state(requester_id, request_id, failure)

    def _on_update_check_request(self, peer_id, frame):
        requester_id = str(frame.get("requester_id") or peer_id)
        request_id = str(frame.get("request_id") or "").strip()
        if not requester_id or not request_id:
            return
        cached = None
        dispatch_needed = False
        with self._lock:
            cached = self._cached_update_check_state()
            if cached is None:
                if isinstance(self._update_check_inflight, dict):
                    requesters = list(self._update_check_inflight.get("requesters") or ())
                    requesters.append((requester_id, request_id))
                    self._update_check_inflight["requesters"] = requesters
                else:
                    self._update_check_inflight = {
                        "job_id": uuid4().hex,
                        "requesters": [(requester_id, request_id)],
                        "candidates": self._update_candidate_ids(),
                        "active_candidate_id": "",
                        "candidate_started_at": 0.0,
                    }
                    dispatch_needed = True
        if cached is not None:
            self._reply_update_check_state(requester_id, request_id, cached)
            return
        if dispatch_needed:
            self._dispatch_next_update_check_candidate()

    def _on_update_check_result(self, peer_id, frame):
        job_id = str(frame.get("job_id") or "").strip()
        status = str(frame.get("status") or "").strip()
        detail = str(frame.get("detail") or "")
        if not job_id:
            return
        reply_requesters = []
        reply_payload = None
        retry_dispatch = False
        with self._lock:
            inflight = self._update_check_inflight
            if not isinstance(inflight, dict):
                return
            if job_id != str(inflight.get("job_id") or ""):
                return
            if peer_id != str(inflight.get("active_candidate_id") or ""):
                return
            result_payload = frame.get("result") if isinstance(frame.get("result"), dict) else None
            source_id = str(frame.get("source_id") or peer_id)
            if status == "success" and result_payload is not None:
                self._update_check_cache = {
                    "fetched_at": time.monotonic(),
                    "result": dict(result_payload),
                    "source_id": source_id,
                }
                reply_payload = {
                    "status": "success",
                    "detail": detail,
                    "result": dict(result_payload),
                    "source_id": source_id,
                }
                reply_requesters = list(inflight.get("requesters", ()))
                self._update_check_inflight = None
            else:
                inflight["active_candidate_id"] = ""
                inflight["candidate_started_at"] = 0.0
                retry_dispatch = True
        if reply_payload is not None:
            for requester_id, request_id in reply_requesters:
                self._reply_update_check_state(requester_id, request_id, reply_payload)
            return
        if retry_dispatch:
            self._dispatch_next_update_check_candidate()

    def _on_update_download_request(self, peer_id, frame):
        requester_id = str(frame.get("requester_id") or peer_id)
        request_id = str(frame.get("request_id") or "").strip()
        tag_name = str(frame.get("tag_name") or "").strip()
        installer_url = str(frame.get("installer_url") or "").strip()
        if not requester_id or not request_id or not tag_name or not installer_url:
            return
        cache_key = build_update_cache_key(tag_name=tag_name, installer_url=installer_url)
        cached = None
        dispatch_needed = False
        with self._lock:
            cached = self._resolve_update_download_cache(cache_key)
            if cached is None:
                if cache_key in self._update_download_jobs:
                    requesters = list(self._update_download_jobs[cache_key].get("requesters") or ())
                    requesters.append((requester_id, request_id))
                    self._update_download_jobs[cache_key]["requesters"] = requesters
                else:
                    self._update_download_jobs[cache_key] = {
                        "job_id": uuid4().hex,
                        "requesters": [(requester_id, request_id)],
                        "candidates": self._update_download_candidate_ids(),
                        "active_candidate_id": "",
                        "candidate_started_at": 0.0,
                        "tag_name": tag_name,
                        "installer_url": installer_url,
                    }
                    dispatch_needed = True
        if cached is not None:
            payload = {"status": "ready", "detail": "", **cached}
            self._reply_update_download_state(requester_id, request_id, payload)
            return
        if dispatch_needed:
            self._dispatch_next_update_download_candidate(cache_key)

    def _on_update_download_result(self, peer_id, frame):
        job_id = str(frame.get("job_id") or "").strip()
        if not job_id:
            return
        matched_key = None
        reply_requesters = []
        reply_payload = None
        retry_dispatch = False
        with self._lock:
            matched_job = None
            for cache_key, payload in self._update_download_jobs.items():
                if str(payload.get("job_id") or "") == job_id:
                    matched_key = cache_key
                    matched_job = payload
                    break
            if matched_key is None or not isinstance(matched_job, dict):
                return
            if peer_id != str(matched_job.get("active_candidate_id") or ""):
                return
            status = str(frame.get("status") or "").strip()
            if status == "ready":
                payload = {
                    "source_id": str(frame.get("source_id") or peer_id),
                    "share_port": int(frame.get("share_port") or 0),
                    "share_id": str(frame.get("share_id") or ""),
                    "share_token": str(frame.get("share_token") or ""),
                    "sha256": str(frame.get("sha256") or ""),
                    "size_bytes": int(frame.get("size_bytes") or 0),
                }
                self._cache_update_download_entry(matched_key, payload)
                reply_payload = {"status": "ready", "detail": "", **payload}
                reply_requesters = list(matched_job.get("requesters", ()))
                self._update_download_jobs.pop(matched_key, None)
            else:
                matched_job["active_candidate_id"] = ""
                matched_job["candidate_started_at"] = 0.0
                retry_dispatch = True
        if reply_payload is not None:
            for requester_id, request_id in reply_requesters:
                self._reply_update_download_state(requester_id, request_id, reply_payload)
            return
        if retry_dispatch and matched_key is not None:
            self._dispatch_next_update_download_candidate(matched_key)

    def _on_node_list_update_request(self, peer_id, frame):
        raw_nodes = frame.get("nodes")
        rename_map = frame.get("rename_map") or {}
        base_revision = frame.get("base_revision", 0)
        request_id = str(frame.get("request_id") or "").strip()
        if not isinstance(raw_nodes, list) or not isinstance(rename_map, dict):
            return
        try:
            base_revision = int(base_revision)
        except (TypeError, ValueError):
            return
        if self.config_reloader is None or not hasattr(self.config_reloader, "apply_nodes_state"):
            logging.warning("[COORDINATOR] ignore node list update without config reloader")
            return
        with self._lock:
            current_revision = self._node_list_revision
        if base_revision != current_revision:
            logging.info(
                "[COORDINATOR] reject stale node list update from %s base=%s current=%s",
                peer_id,
                base_revision,
                current_revision,
            )
            self._reply(
                peer_id,
                make_node_list_state(
                    nodes=self._node_payloads(),
                    revision=current_revision,
                    rename_map={},
                    reject_reason="stale_revision",
                    request_id=request_id,
                    coordinator_epoch=self._coordinator_epoch,
                ),
            )
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
        with self._lock:
            self._node_list_revision += 1
            revision = self._node_list_revision
        self._broadcast(
            make_node_list_state(
                nodes=self._node_payloads(),
                revision=revision,
                rename_map=rename_map,
                request_id=request_id,
                coordinator_epoch=self._coordinator_epoch,
            )
        )

    def _on_node_note_update_request(self, peer_id, frame):
        node_id = frame.get("node_id")
        note = frame.get("note", "")
        request_id = str(frame.get("request_id") or "").strip()
        if not node_id or not isinstance(note, str):
            return
        node = self.ctx.get_node(node_id)
        if node is None:
            return
        updated_nodes = []
        for current in self.ctx.nodes:
            if current.node_id == node_id:
                updated_nodes.append(
                    type(current)(
                        name=current.name,
                        ip=current.ip,
                        port=current.port,
                        note=note,
                        node_id=current.node_id,
                        priority=current.priority,
                    )
                )
            else:
                updated_nodes.append(current)
        self.ctx.replace_nodes(updated_nodes)
        self._broadcast(
                make_node_note_update_state(
                    node_id=node_id,
                    note=note,
                    coordinator_epoch=self._coordinator_epoch,
                    request_id=request_id,
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
        lease_clear_targets = []
        retry_update_check = False
        retry_download_keys = []
        now = self._now()
        with self._lock:
            for target_id, lease in list(self._leases.items()):
                if lease["expires_at"] <= now:
                    expired.append((target_id, lease["controller_id"]))
                    del self._leases[target_id]
                    lease_clear_targets.append(target_id)
            inflight = self._update_check_inflight
            if isinstance(inflight, dict):
                candidate_id = str(inflight.get("active_candidate_id") or "")
                candidate_started_at = float(inflight.get("candidate_started_at") or 0.0)
                if (
                    candidate_id
                    and candidate_started_at > 0.0
                    and (now - candidate_started_at) >= self.UPDATE_CHECK_CANDIDATE_TIMEOUT_SEC
                ):
                    logging.warning(
                        "[COORDINATOR] group update check candidate timed out candidate=%s job=%s",
                        candidate_id,
                        inflight.get("job_id"),
                    )
                    inflight["active_candidate_id"] = ""
                    inflight["candidate_started_at"] = 0.0
                    retry_update_check = True
            for cache_key, job in list(self._update_download_jobs.items()):
                candidate_id = str(job.get("active_candidate_id") or "")
                candidate_started_at = float(job.get("candidate_started_at") or 0.0)
                if (
                    candidate_id
                    and candidate_started_at > 0.0
                    and (now - candidate_started_at) >= self.UPDATE_DOWNLOAD_CANDIDATE_TIMEOUT_SEC
                ):
                    logging.warning(
                        "[COORDINATOR] group update download candidate timed out candidate=%s job=%s cache_key=%s",
                        candidate_id,
                        job.get("job_id"),
                        cache_key,
                    )
                    job["active_candidate_id"] = ""
                    job["candidate_started_at"] = 0.0
                    retry_download_keys.append(str(cache_key))
        for target_id in lease_clear_targets:
            self._send_lease_update(target_id, None)
        if retry_update_check:
            self._dispatch_next_update_check_candidate()
        for cache_key in retry_download_keys:
            self._dispatch_next_update_download_candidate(cache_key)
        return expired
