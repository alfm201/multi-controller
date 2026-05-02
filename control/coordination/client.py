"""Controller와 target 양쪽에서 쓰는 coordinator client."""

import logging
import threading
import time
from uuid import uuid4

from control.coordination.protocol import (
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
    make_update_check_request,
    make_update_check_result,
    make_update_download_request,
    make_update_download_result,
    make_remote_update_request,
    make_remote_update_status,
    make_release,
)
from app.update.update_domain import (
    UPDATE_REASON_TIMEOUT,
    UPDATE_STAGE_FAILED,
)
from control.state.context import NodeInfo
from app.logging.app_logging import log_detail
from model.display.monitor_inventory import (
    MonitorInventorySnapshot,
    deserialize_monitor_inventory_snapshot,
    serialize_monitor_inventory_snapshot,
)
from model.display.layouts import build_layout_config, serialize_layout_config


class CoordinatorClient:
    HEARTBEAT_INTERVAL_SEC = 1.0
    CONTROL_POLL_INTERVAL_SEC = 0.5
    LAYOUT_EDIT_RETRY_INTERVAL_SEC = 1.0
    ONE_SHOT_TIMEOUT_SEC = 4.0
    REMOTE_UPDATE_REQUEST_TIMEOUT_SEC = 10.0
    UPDATE_CHECK_TIMEOUT_SEC = 45.0
    UPDATE_DOWNLOAD_TIMEOUT_SEC = 30.0 * 60.0

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
        self._node_list_revision = 0
        self._latest_monitor_inventory = None
        self._monitor_inventory_manager = None
        self._monitor_inventory_refresh_states = {}
        self._local_override_pending_controller_id = None
        self._requested_target_source = None
        self._target_result_listeners = []
        self._remote_update_handler = None
        self._remote_update_status_handler = None
        self._update_check_handler = None
        self._update_check_status_handler = None
        self._update_download_handler = None
        self._update_download_status_handler = None
        self._auto_switch_change_handler = None
        self._node_list_change_listeners = []
        self._one_shot_timeout_handler = None
        self._pending_one_shot_requests = {}
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
        dispatcher.register_control_handler(
            "ctrl.update_check_command",
            self._on_update_check_command,
        )
        dispatcher.register_control_handler(
            "ctrl.update_check_state",
            self._on_update_check_state,
        )
        dispatcher.register_control_handler(
            "ctrl.update_download_command",
            self._on_update_download_command,
        )
        dispatcher.register_control_handler(
            "ctrl.update_download_state",
            self._on_update_download_state,
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

    def set_update_check_handler(self, handler):
        self._update_check_handler = handler

    def set_update_check_status_handler(self, handler):
        self._update_check_status_handler = handler

    def set_update_download_handler(self, handler):
        self._update_download_handler = handler

    def set_update_download_status_handler(self, handler):
        self._update_download_status_handler = handler

    def set_auto_switch_change_handler(self, handler):
        self._auto_switch_change_handler = handler

    def add_node_list_change_listener(self, listener):
        self._node_list_change_listeners.append(listener)

    def set_one_shot_timeout_handler(self, handler):
        self._one_shot_timeout_handler = handler

    @staticmethod
    def _new_request_id() -> str:
        return uuid4().hex

    def _track_one_shot_request(self, *, request_id: str, kind: str, target_id: str = "") -> None:
        if not request_id:
            return
        self._pending_one_shot_requests[request_id] = {
            "kind": kind,
            "target_id": str(target_id or ""),
            "started_at": time.monotonic(),
        }

    def _resolve_one_shot_request(self, request_id: str | None) -> None:
        request_id = str(request_id or "").strip()
        if not request_id:
            return
        self._pending_one_shot_requests.pop(request_id, None)

    def _resolve_matching_one_shot_request(self, *, kind: str, target_id: str = "") -> None:
        kind = str(kind or "").strip()
        target_id = str(target_id or "").strip()
        if not kind:
            return
        for request_id, payload in list(self._pending_one_shot_requests.items()):
            if str(payload.get("kind") or "") != kind:
                continue
            if target_id and str(payload.get("target_id") or "") != target_id:
                continue
            self._pending_one_shot_requests.pop(request_id, None)
            return

    def _expire_pending_one_shot_requests(self, now: float | None = None) -> None:
        current_time = time.monotonic() if now is None else float(now)
        expired = []
        for request_id, payload in list(self._pending_one_shot_requests.items()):
            if (current_time - float(payload["started_at"])) < self._one_shot_timeout_for_kind(
                str(payload.get("kind") or "")
            ):
                continue
            expired.append((request_id, dict(payload)))
            self._pending_one_shot_requests.pop(request_id, None)
        for request_id, payload in expired:
            self._handle_one_shot_timeout(request_id, payload)

    def _one_shot_timeout_for_kind(self, kind: str) -> float:
        if kind == "remote_update":
            return self.REMOTE_UPDATE_REQUEST_TIMEOUT_SEC
        if kind == "update_check":
            return self.UPDATE_CHECK_TIMEOUT_SEC
        if kind == "update_download":
            return self.UPDATE_DOWNLOAD_TIMEOUT_SEC
        return self.ONE_SHOT_TIMEOUT_SEC

    def _handle_one_shot_timeout(self, request_id: str, payload: dict) -> None:
        kind = str(payload.get("kind") or "").strip()
        target_id = str(payload.get("target_id") or "").strip()
        if kind == "node_list":
            timeout_payload = {
                "added_node_ids": (),
                "revision": self._node_list_revision,
                "coordinator_epoch": self._coordinator_epoch,
                "reject_reason": "timeout",
                "request_id": request_id,
            }
            for listener in list(self._node_list_change_listeners):
                try:
                    listener(dict(timeout_payload))
                except Exception as exc:
                    logging.warning("[COORDINATOR CLIENT] node list change listener failed: %s", exc)
            return
        if kind == "remote_update":
            if callable(self._remote_update_status_handler):
                self._remote_update_status_handler(
                    {
                        "target_id": target_id,
                        "requester_id": self.ctx.self_node.node_id,
                        "status": UPDATE_STAGE_FAILED,
                        "reason": UPDATE_REASON_TIMEOUT,
                        "detail": "응답 시간 초과",
                        "request_id": request_id,
                        "event_id": "",
                        "session_id": "",
                        "current_version": "",
                        "latest_version": "",
                        "coordinator_epoch": self._coordinator_epoch,
                    }
                )
            return
        if kind == "update_check":
            if callable(self._update_check_status_handler):
                self._update_check_status_handler(
                    {
                        "status": UPDATE_STAGE_FAILED,
                        "reason": UPDATE_REASON_TIMEOUT,
                        "detail": "?묐떟 ?쒓컙 珥덇낵",
                        "request_id": request_id,
                        "result": None,
                        "source_id": "",
                        "coordinator_epoch": self._coordinator_epoch,
                    }
                )
            return
        if kind == "update_download":
            if callable(self._update_download_status_handler):
                self._update_download_status_handler(
                    {
                        "status": UPDATE_STAGE_FAILED,
                        "reason": UPDATE_REASON_TIMEOUT,
                        "detail": "?묐떟 ?쒓컙 珥덇낵",
                        "request_id": request_id,
                        "source_id": "",
                        "share_port": 0,
                        "share_id": "",
                        "share_token": "",
                        "sha256": "",
                        "size_bytes": 0,
                        "coordinator_epoch": self._coordinator_epoch,
                    }
                )
            return
        if not callable(self._one_shot_timeout_handler):
            return
        self._one_shot_timeout_handler(self._one_shot_timeout_message(kind, target_id), "warning")

    def _one_shot_timeout_message(self, kind: str, target_id: str) -> str:
        label = self._node_label(target_id)
        if kind == "auto_switch":
            return "자동 경계 전환 변경 요청이 시간 안에 확인되지 않았습니다. 다시 시도해 주세요."
        if kind == "node_note":
            return f"{label} 비고 변경 요청이 시간 안에 확인되지 않았습니다. 다시 시도해 주세요."
        if kind == "node_list":
            return "노드 목록 변경 요청이 시간 안에 확인되지 않았습니다. 다시 시도해 주세요."
        if kind == "monitor_refresh":
            return f"{label} 모니터 재감지 요청이 시간 안에 확인되지 않았습니다. 다시 시도해 주세요."
        return "요청이 시간 안에 확인되지 않았습니다. 다시 시도해 주세요."

    def _node_label(self, node_id: str) -> str:
        node = self.ctx.get_node(str(node_id or ""))
        if node is None:
            return "알 수 없는 노드"
        return node.display_label()

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
        logging.info("[COORDINATOR CLIENT] clearing disconnected target=%s", self._node_label(node_id))
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
        request_id = self._new_request_id()
        sent = self._send(
            make_auto_switch_update_request(
                enabled=bool(enabled),
                requester_id=self.ctx.self_node.node_id,
                request_id=request_id,
            )
        )
        if sent:
            self._track_one_shot_request(request_id=request_id, kind="auto_switch")
        return sent

    def request_remote_update(self, target_id: str) -> bool:
        request_id = self._new_request_id()
        sent = self._send(
            make_remote_update_request(
                target_id=target_id,
                requester_id=self.ctx.self_node.node_id,
                request_id=request_id,
            )
        )
        if sent:
            self._track_one_shot_request(request_id=request_id, kind="remote_update", target_id=target_id)
        return sent

    def request_group_update_check(self) -> str | None:
        request_id = self._new_request_id()
        sent = self._send(
            make_update_check_request(
                requester_id=self.ctx.self_node.node_id,
                request_id=request_id,
            )
        )
        if sent:
            self._track_one_shot_request(request_id=request_id, kind="update_check")
            return request_id
        return None

    def report_group_update_check_result(
        self,
        *,
        job_id: str,
        status: str,
        detail: str = "",
        result: dict | None = None,
    ) -> bool:
        if not job_id or not status:
            return False
        return self._send(
            make_update_check_result(
                job_id=job_id,
                status=status,
                detail=detail,
                result=result,
                source_id=self.ctx.self_node.node_id,
                coordinator_epoch=str(self._coordinator_epoch or ""),
            )
        )

    def request_group_update_download(
        self,
        *,
        tag_name: str,
        installer_url: str,
        current_version: str = "",
        latest_version: str = "",
    ) -> str | None:
        request_id = self._new_request_id()
        sent = self._send(
            make_update_download_request(
                requester_id=self.ctx.self_node.node_id,
                request_id=request_id,
                tag_name=tag_name,
                installer_url=installer_url,
                current_version=current_version,
                latest_version=latest_version,
            )
        )
        if sent:
            self._track_one_shot_request(request_id=request_id, kind="update_download")
            return request_id
        return None

    def report_group_update_download_result(
        self,
        *,
        job_id: str,
        status: str,
        detail: str = "",
        share_port: int = 0,
        share_id: str = "",
        share_token: str = "",
        sha256: str = "",
        size_bytes: int = 0,
    ) -> bool:
        if not job_id or not status:
            return False
        return self._send(
            make_update_download_result(
                job_id=job_id,
                status=status,
                detail=detail,
                source_id=self.ctx.self_node.node_id,
                share_port=share_port,
                share_id=share_id,
                share_token=share_token,
                sha256=sha256,
                size_bytes=size_bytes,
                coordinator_epoch=str(self._coordinator_epoch or ""),
            )
        )

    def report_remote_update_status(
        self,
        *,
        target_id: str,
        requester_id: str,
        status: str,
        detail: str = "",
        reason: str = "",
        request_id: str = "",
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
                reason=reason,
                request_id=request_id,
                coordinator_epoch=str(self._coordinator_epoch or ""),
                event_id=event_id,
                session_id=session_id,
                current_version=current_version,
                latest_version=latest_version,
            )
        )

    def request_node_note_update(self, node_id: str, note: str) -> bool:
        request_id = self._new_request_id()
        sent = self._send(
            make_node_note_update_request(
                node_id=node_id,
                note=note,
                requester_id=self.ctx.self_node.node_id,
                request_id=request_id,
            )
        )
        if sent:
            self._track_one_shot_request(request_id=request_id, kind="node_note", target_id=node_id)
        return sent

    def request_node_list_update(
        self,
        nodes: list[dict],
        *,
        rename_map: dict[str, str] | None = None,
        request_id: str | None = None,
    ) -> bool:
        request_id = str(request_id or self._new_request_id())
        sent = self._send(
            make_node_list_update_request(
                nodes=nodes,
                requester_id=self.ctx.self_node.node_id,
                base_revision=self._node_list_revision,
                rename_map=rename_map,
                request_id=request_id,
            )
        )
        if sent and self._has_remote_peer():
            self._track_one_shot_request(request_id=request_id, kind="node_list")
        return sent

    def _has_remote_peer(self) -> bool:
        if self.registry is None or not hasattr(self.registry, "all"):
            return False
        for peer_id, conn in self.registry.all():
            if peer_id == self.ctx.self_node.node_id:
                continue
            if conn is not None and not getattr(conn, "closed", False):
                return True
        return False

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
        request_id = self._new_request_id()
        sent = self._send(
            make_monitor_inventory_refresh_request(
                node_id=node_id,
                requester_id=self.ctx.self_node.node_id,
                request_id=request_id,
            )
        )
        if sent:
            self._track_one_shot_request(request_id=request_id, kind="monitor_refresh", target_id=node_id)
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
        self._expire_pending_one_shot_requests()
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
        self._node_list_revision = 0
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

        logging.info("[COORDINATOR CLIENT] DENY target=%s reason=%s", self._node_label(target_id), reason)
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
        logging.info("[COORDINATOR CLIENT] layout edit granted editor=%s", self._node_label(editor_id))

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
        if change_kind == "auto_switch_toggle" and requester_id == self.ctx.self_node.node_id:
            request_id = str(frame.get("request_id") or "").strip()
            if request_id:
                self._resolve_one_shot_request(request_id)
            else:
                self._resolve_matching_one_shot_request(kind="auto_switch")
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
            try:
                self.config_reloader.apply_layout(
                    layout,
                    persist=persist,
                    debounce_persist=False,
                )
            except Exception as exc:
                logging.warning("[COORDINATOR CLIENT] failed to apply layout update: %s", exc)
                return
        else:
            self.ctx.replace_layout(layout)

        self._layout_last_update_revision = revision
        self._layout_editor_id = frame.get("editor_id") or None
        log_detail(
            "[COORDINATOR CLIENT] applied layout revision=%s editor=%s persist=%s bootstrap=%s",
            revision,
            self._node_label(self._layout_editor_id),
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
        try:
            if self.config_reloader is not None:
                self.config_reloader.apply_monitor_inventory(snapshot, persist=True)
            else:
                self.ctx.replace_monitor_inventory(snapshot)
        except Exception as exc:
            logging.warning("[COORDINATOR CLIENT] failed to apply monitor inventory: %s", exc)

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
        self._resolve_one_shot_request(frame.get("request_id"))

    def _on_node_note_update_state(self, peer_id, frame):
        node_id = frame.get("node_id")
        note = frame.get("note", "")
        if not node_id or not isinstance(note, str):
            return
        if not self._accept_coordinator_frame(peer_id, frame.get("coordinator_epoch")):
            return
        if self.config_reloader is not None:
            try:
                self.config_reloader.apply_node_note(str(node_id), note, persist=True)
            except Exception as exc:
                logging.warning("[COORDINATOR CLIENT] failed to apply node note: %s", exc)
            else:
                self._resolve_one_shot_request(frame.get("request_id"))
            return
        node = self.ctx.get_node(str(node_id))
        if node is None:
            return
        updated = []
        for current in self.ctx.nodes:
            if current.node_id == str(node_id):
                updated.append(
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
                updated.append(current)
        self.ctx.replace_nodes(updated)
        self._resolve_one_shot_request(frame.get("request_id"))

    def _on_node_list_state(self, peer_id, frame):
        raw_nodes = frame.get("nodes")
        rename_map = frame.get("rename_map") or {}
        raw_revision = frame.get("revision")
        reject_reason = str(frame.get("reject_reason") or "").strip()
        if not isinstance(raw_nodes, list) or not isinstance(rename_map, dict):
            return
        if not self._accept_coordinator_frame(peer_id, frame.get("coordinator_epoch")):
            return
        if raw_revision is None:
            revision = self._node_list_revision
        elif not isinstance(raw_revision, int):
            return
        else:
            revision = raw_revision
        if revision < self._node_list_revision:
            logging.debug(
                "[COORDINATOR CLIENT] ignore stale node list revision=%s current=%s",
                revision,
                self._node_list_revision,
            )
            return
        previous_nodes = {
            node.node_id: node
            for node in getattr(self.ctx, "nodes", ())
        }
        next_payloads = [node for node in raw_nodes if isinstance(node, dict)]
        next_node_ids = {
            str(node.get("node_id") or node.get("name") or "").strip()
            for node in next_payloads
            if str(node.get("node_id") or node.get("name") or "").strip()
        }
        added_node_ids = tuple(
            node_id
            for node_id in sorted(next_node_ids)
            if node_id not in previous_nodes and node_id != self.ctx.self_node.node_id
        )
        try:
            if self.config_reloader is not None and hasattr(self.config_reloader, "apply_nodes_state"):
                self.config_reloader.apply_nodes_state(
                    raw_nodes,
                    rename_map=rename_map,
                    persist=True,
                    apply_runtime=True,
                )
            else:
                self.ctx.replace_nodes([NodeInfo.from_dict(node) for node in raw_nodes if isinstance(node, dict)])
        except Exception as exc:
            logging.warning("[COORDINATOR CLIENT] failed to apply node list state: %s", exc)
            if frame.get("request_id"):
                payload = {
                    "request_id": str(frame.get("request_id")),
                    "revision": self._node_list_revision,
                    "coordinator_epoch": frame.get("coordinator_epoch"),
                    "reject_reason": "apply_failed",
                }
                for listener in list(self._node_list_change_listeners):
                    try:
                        listener(dict(payload))
                    except Exception as listener_exc:
                        logging.warning(
                            "[COORDINATOR CLIENT] node list change listener failed: %s",
                            listener_exc,
                        )
            return
        self._node_list_revision = revision
        self._resolve_one_shot_request(frame.get("request_id"))
        if added_node_ids or reject_reason or frame.get("request_id"):
            payload = {
                "added_node_ids": added_node_ids,
                "revision": revision,
                "coordinator_epoch": frame.get("coordinator_epoch"),
            }
            if frame.get("request_id"):
                payload["request_id"] = str(frame.get("request_id"))
            if reject_reason:
                payload["reject_reason"] = reject_reason
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
                    "request_id": frame.get("request_id", ""),
                    "coordinator_epoch": frame.get("coordinator_epoch"),
                }
            )

    def _on_remote_update_status(self, peer_id, frame):
        if frame.get("requester_id") != self.ctx.self_node.node_id:
            return
        if not self._accept_remote_update_status_frame(peer_id, frame):
            return
        request_id = str(frame.get("request_id") or "").strip()
        if request_id:
            self._resolve_one_shot_request(request_id)
        else:
            self._resolve_matching_one_shot_request(
                kind="remote_update",
                target_id=str(frame.get("target_id") or ""),
            )
        if callable(self._remote_update_status_handler):
            self._remote_update_status_handler(
                {
                    "target_id": frame.get("target_id"),
                    "requester_id": frame.get("requester_id"),
                    "status": frame.get("status"),
                    "detail": frame.get("detail", ""),
                    "reason": frame.get("reason", ""),
                    "request_id": frame.get("request_id", ""),
                    "event_id": frame.get("event_id", ""),
                    "session_id": frame.get("session_id", ""),
                    "current_version": frame.get("current_version", ""),
                    "latest_version": frame.get("latest_version", ""),
                    "coordinator_epoch": frame.get("coordinator_epoch"),
                }
            )

    def _on_update_check_command(self, _peer_id, frame):
        job_id = str(frame.get("job_id") or "").strip()
        if not job_id:
            return
        if callable(self._update_check_handler):
            self._update_check_handler(
                {
                    "job_id": job_id,
                    "coordinator_epoch": frame.get("coordinator_epoch"),
                }
            )

    def _on_update_check_state(self, peer_id, frame):
        if frame.get("requester_id") != self.ctx.self_node.node_id:
            return
        if not self._accept_coordinator_frame(peer_id, frame.get("coordinator_epoch")):
            return
        self._resolve_one_shot_request(frame.get("request_id"))
        if callable(self._update_check_status_handler):
            self._update_check_status_handler(
                {
                    "status": str(frame.get("status") or ""),
                    "detail": frame.get("detail", ""),
                    "request_id": str(frame.get("request_id") or ""),
                    "result": frame.get("result"),
                    "source_id": str(frame.get("source_id") or ""),
                    "coordinator_epoch": frame.get("coordinator_epoch"),
                }
            )

    def _on_update_download_command(self, _peer_id, frame):
        job_id = str(frame.get("job_id") or "").strip()
        installer_url = str(frame.get("installer_url") or "").strip()
        if not job_id or not installer_url:
            return
        if callable(self._update_download_handler):
            self._update_download_handler(
                {
                    "job_id": job_id,
                    "installer_url": installer_url,
                    "tag_name": str(frame.get("tag_name") or ""),
                    "coordinator_epoch": frame.get("coordinator_epoch"),
                }
            )

    def _on_update_download_state(self, peer_id, frame):
        if frame.get("requester_id") != self.ctx.self_node.node_id:
            return
        if not self._accept_coordinator_frame(peer_id, frame.get("coordinator_epoch")):
            return
        self._resolve_one_shot_request(frame.get("request_id"))
        if callable(self._update_download_status_handler):
            self._update_download_status_handler(
                {
                    "status": str(frame.get("status") or ""),
                    "detail": frame.get("detail", ""),
                    "request_id": str(frame.get("request_id") or ""),
                    "source_id": str(frame.get("source_id") or ""),
                    "share_port": int(frame.get("share_port") or 0),
                    "share_id": str(frame.get("share_id") or ""),
                    "share_token": str(frame.get("share_token") or ""),
                    "sha256": str(frame.get("sha256") or ""),
                    "size_bytes": int(frame.get("size_bytes") or 0),
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
