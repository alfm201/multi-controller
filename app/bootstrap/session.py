"""런타임 조립과 실행 수명주기를 관리한다."""

from __future__ import annotations

import logging
import os
import queue
import socket
import sys
import threading
import time

from model.events import make_mouse_move_event, make_system_event
from control.coordination.client import CoordinatorClient
from control.coordination.election import CoordinatorElection
from control.coordination.service import CoordinatorService
from transport.peer.dispatcher import FrameDispatcher
from transport.peer.peer_dialer import PeerDialer
from transport.peer.peer_registry import PeerRegistry
from transport.peer.peer_server import PeerServer
from control.routing.router import InputRouter
from control.routing.sink import InputSink
from app.logging.app_logging import (
    TAG_CONFIG,
    TAG_CURSOR,
    TAG_EXIT,
    TAG_GUI,
    TAG_HOTKEY,
    TAG_INJECT,
    TAG_SHUTDOWN,
    tag_message,
)
from app.config.app_settings import hotkey_to_matcher_parts
from msp_platform.windows.clip_recovery import spawn_clip_watchdog
from app.config.config_reloader import RuntimeConfigReloader
from model.display.display import enrich_pointer_event, get_virtual_screen_bounds
from app.config.group_join import build_group_join_state, merge_group_join_nodes
from model.display.layouts import replace_auto_switch_settings, serialize_layout_config
from msp_platform.windows.local_cursor import LocalCursorController
from control.state.monitor_inventory_manager import MonitorInventoryManager
from app.ui.qt_app import QtRuntimeApp
from control.state.state_watcher import StateWatcher
from control.state.status_reporter import StatusReporter
from msp_platform.windows.synthetic_input import SyntheticInputGuard
from app.bootstrap.helpers import (
    AsyncHotkeyAction,
    build_target_primary_center_anchor,
    format_peer_reject_notice,
    install_capture_hotkey_fallbacks,
    install_cursor_cleanup_hooks,
    notify_runtime_message,
    park_local_cursor_for_active_target,
    restore_local_cursor_after_target_exit,
    start_local_input_services,
    start_local_input_services_async,
)


class RuntimeSession:
    SELF_IP_SYNC_MAX_RETRIES = 3
    SELF_IP_PROBE_TIMEOUT_SEC = 1.0

    """서비스 조립, 시작, 종료를 한 객체에 모은다."""

    def __init__(
        self,
        ctx,
        *,
        active_target: str | None,
        status_interval: float,
        ui_mode: str,
        shutdown_evt: threading.Event,
        log_path=None,
    ) -> None:
        self.ctx = ctx
        self.active_target = active_target
        self.status_interval = status_interval
        self.ui_mode = ui_mode
        self.shutdown_evt = shutdown_evt
        self.log_path = log_path

        self.registry = None
        self.dispatcher = None
        self.synthetic_guard = None
        self.injector = None
        self.sink = None
        self.server = None
        self.dialer = None
        self.capture = None
        self.capture_queue = None
        self.router = None
        self.router_thread = None
        self.coord_service = None
        self.coord_client = None
        self.monitor_inventory_manager = None
        self.local_cursor = None
        self.auto_switcher = None
        self.status_reporter = None
        self.state_watcher = None
        self.qt_runtime_app = None
        self.global_hotkeys = None
        self.config_reloader = None
        self.startup_input_thread = None
        self.coordinator_resolver = None
        self.coordinator_election = None
        self.auto_switch_hotkey_action = None
        self.cycler = None
        self._self_ip_sync_lock = threading.RLock()
        self._pending_self_ip_sync = None

    def build(self):
        self.registry = PeerRegistry()
        self.dispatcher = FrameDispatcher()
        self.coordinator_election = CoordinatorElection(self.ctx, self.registry)

        def coordinator_resolver():
            return self.coordinator_election.pick()

        self.coordinator_resolver = coordinator_resolver
        self.synthetic_guard = SyntheticInputGuard()
        self.injector = self._create_injector()
        self.sink = InputSink(
            injector=self.injector,
            require_authorization=True,
        )
        self.dispatcher.set_input_handler(self.sink.handle)
        self.registry.add_unbind_listener(self.sink.release_peer)

        self.server = PeerServer(self.ctx, self.registry, self.dispatcher)
        self.dialer = PeerDialer(self.ctx, self.registry, self.dispatcher)

        from msp_platform.capture.input_capture import InputCapture
        from control.routing.auto_switch import AutoTargetSwitcher

        self.capture_queue = queue.Queue()
        self.capture = InputCapture(
            self.capture_queue,
            synthetic_guard=self.synthetic_guard,
            global_wheel_callback=lambda x, y, dx, dy: (
                self.qt_runtime_app is not None
                and self.qt_runtime_app.request_global_layout_wheel(x, y, dx, dy)
            ),
            mouse_block_predicate=lambda kind, event: (
                self.router is not None
                and self.router.get_target_state() == "active"
                and kind in {"mouse_move", "mouse_button", "mouse_wheel"}
            ),
            keyboard_block_predicate=lambda kind, event: (
                self.router is not None
                and self.router.get_target_state() == "active"
                and kind in {"key_down", "key_up"}
            ),
        )
        self.router = InputRouter(self.ctx, self.registry)
        self.router_thread = threading.Thread(
            target=self.router.run,
            args=(self.capture_queue,),
            daemon=True,
            name="input-router",
        )

        self.coord_service = CoordinatorService(
            self.ctx,
            self.registry,
            self.dispatcher,
            coordinator_resolver=self.coordinator_resolver,
        )
        self.coord_client = CoordinatorClient(
            self.ctx,
            self.registry,
            self.dispatcher,
            coordinator_resolver=self.coordinator_resolver,
            router=self.router,
            sink=self.sink,
        )
        self.monitor_inventory_manager = MonitorInventoryManager(
            self.ctx,
            coord_client=self.coord_client,
        )
        self.coord_client.set_monitor_inventory_manager(self.monitor_inventory_manager)

        self.local_cursor = LocalCursorController(synthetic_guard=self.synthetic_guard)
        self.router.add_state_listener(self._sync_local_cursor_visibility)
        install_cursor_cleanup_hooks(
            self.local_cursor.clear_clip,
            self.local_cursor.show_cursor,
            log_path=self.log_path,
        )
        spawn_clip_watchdog(os.getpid())

        self.auto_switcher = AutoTargetSwitcher(
            self.ctx,
            self.router,
            request_target=self.coord_client.request_target,
            clear_target=self.coord_client.clear_target,
            is_target_online=lambda node_id: (
                (conn := self.registry.get(node_id)) is not None and not conn.closed
            ),
            pointer_mover=self.local_cursor.move,
            pointer_clipper=self.local_cursor,
            actual_pointer_provider=self.local_cursor.position,
        )
        self.router.add_state_listener(self.auto_switcher.on_router_state_change)
        self.capture.move_processor = self.auto_switcher.process
        self.capture.pointer_state_refresher = self.auto_switcher.note_local_hold_risk
        self.capture.focus_transition_refresher = self.auto_switcher.note_local_hold_risk

        self.status_reporter = StatusReporter(
            self.ctx,
            self.registry,
            self.coordinator_resolver,
            router=self.router,
            sink=self.sink,
            interval_sec=self.status_interval,
        )
        self.state_watcher = StateWatcher(
            self.ctx,
            self.registry,
            self.coordinator_resolver,
            router=self.router,
            sink=self.sink,
            self_ip_change_callback=self._handle_self_ip_change,
        )
        self.config_reloader = RuntimeConfigReloader(
            self.ctx,
            dialer=self.dialer,
            router=self.router,
            coord_client=self.coord_client,
        )
        self.coord_service.set_config_reloader(self.config_reloader)
        self.coord_client.set_config_reloader(self.config_reloader)
        self.monitor_inventory_manager.config_reloader = self.config_reloader
        if hasattr(self.coord_client, "add_node_list_change_listener"):
            self.coord_client.add_node_list_change_listener(self._handle_self_ip_sync_node_list_change)
        self.config_reloader.start_periodic_backup_pruning()
        self._configure_group_join_bootstrap()

        if self.ui_mode in {"gui", "tray"}:
            self.qt_runtime_app = QtRuntimeApp(
                ctx=self.ctx,
                registry=self.registry,
                coordinator_resolver=self.coordinator_resolver,
                router=self.router,
                sink=self.sink,
                coord_client=self.coord_client,
                config_reloader=self.config_reloader,
                monitor_inventory_manager=self.monitor_inventory_manager,
                ui_mode=self.ui_mode,
                deferred_startup_callback=self._start_deferred_input_services,
            )

        self._configure_runtime_controls()
        self._configure_local_input_override()
        return self

    def start(self):
        self.server.start()
        self.dialer.start()
        self.coord_service.start()
        self.coord_client.start()
        self.monitor_inventory_manager.refresh_async()
        self.state_watcher.start()
        self.status_reporter.start()
        if self.router_thread is not None:
            self.router_thread.start()
        if self.capture is not None:
            if self.qt_runtime_app is None:
                start_local_input_services(
                    self.capture,
                    self.auto_switcher,
                    self.global_hotkeys,
                    self.shutdown_evt,
                )
        if self.active_target and self.router is not None:
            self.coord_client.request_target(self.active_target, source="startup")

    def run(self):
        if self.qt_runtime_app is not None and self.shutdown_evt.is_set():
            self.qt_runtime_app = None
        if self.qt_runtime_app is not None:
            try:
                self.qt_runtime_app.run(self.shutdown_evt.set)
            except Exception as exc:
                logging.warning(tag_message(TAG_GUI, "Qt runtime UI failed: %s"), exc)
                self.qt_runtime_app = None

        if self.qt_runtime_app is None and self.capture is not None:
            while not self.shutdown_evt.is_set() and self.capture.running:
                self.shutdown_evt.wait(timeout=0.2)
        elif self.qt_runtime_app is None:
            self.shutdown_evt.wait()

    def shutdown(self):
        logging.info(tag_message(TAG_SHUTDOWN, "stopping"))
        if self.local_cursor is not None:
            self.local_cursor.clear_clip()
            self.local_cursor.show_cursor()
        if self.config_reloader is not None:
            self.config_reloader.stop_periodic_backup_pruning()
            try:
                self.config_reloader.flush_pending_layout()
            except Exception as exc:
                logging.warning(tag_message(TAG_CONFIG, "failed to flush pending layout on shutdown: %s"), exc)
        if self.global_hotkeys is not None:
            self.global_hotkeys.stop()
        if self.capture is not None:
            self.capture.stop()
        if self.router is not None:
            self.router.stop()
        if self.capture_queue is not None:
            self.capture_queue.put({"kind": "system", "message": "shutdown"})
        if self.status_reporter is not None:
            self.status_reporter.stop()
        if self.state_watcher is not None:
            self.state_watcher.stop()
        if self.coord_client is not None:
            self.coord_client.stop()
        if self.coord_service is not None:
            self.coord_service.stop()
        if self.dialer is not None:
            self.dialer.stop()
        if self.server is not None:
            self.server.stop()
        if self.registry is not None:
            self.registry.close_all()
        if self.global_hotkeys is not None:
            self.global_hotkeys.join(timeout=1.0)
        if self.startup_input_thread is not None:
            self.startup_input_thread.join(timeout=1.0)
        time.sleep(0.1)
        logging.info(tag_message(TAG_EXIT, "main stopped"))

    def run_forever(self):
        try:
            self.build()
            self.start()
            self.run()
        finally:
            self.shutdown()

    def _start_deferred_input_services(self) -> None:
        if self.capture is None or self.qt_runtime_app is None or self.shutdown_evt.is_set():
            return
        if self.startup_input_thread is not None:
            return
        self.startup_input_thread = start_local_input_services_async(
            self.capture,
            self.auto_switcher,
            self.global_hotkeys,
            self.shutdown_evt,
        )

    def _create_injector(self):
        try:
            from msp_platform.injection.os_injector import PynputOSInjector

            injector = PynputOSInjector(synthetic_guard=self.synthetic_guard)
            logging.info(tag_message(TAG_INJECT, "pynput OS injection enabled"))
            return injector
        except Exception as exc:
            from msp_platform.injection.os_injector import LoggingOSInjector

            logging.warning(
                tag_message(TAG_INJECT, "pynput unavailable (%s); using logging injector"),
                exc,
            )
            return LoggingOSInjector()

    def _sync_local_cursor_visibility(self, state, node_id):
        node_label = self.ctx.get_node(node_id).display_label() if self.ctx.get_node(node_id) is not None else node_id
        if state == "active":
            if not park_local_cursor_for_active_target(self.local_cursor, self.ctx):
                logging.debug(tag_message(TAG_CURSOR, "failed to park local cursor for active target=%s"), node_label)
            if not self.local_cursor.hide_cursor():
                logging.debug(tag_message(TAG_CURSOR, "failed to hide local cursor for active target=%s"), node_label)
            return
        if not restore_local_cursor_after_target_exit(self.router, self.local_cursor, self.ctx):
            logging.debug(tag_message(TAG_CURSOR, "failed to restore local cursor position for state=%s"), state)
        if not self.local_cursor.show_cursor():
            logging.debug(tag_message(TAG_CURSOR, "failed to show local cursor for state=%s"), state)

    def _configure_group_join_bootstrap(self):
        if not hasattr(self.server, "set_bootstrap_handler"):
            return

        def _handle_group_join_bootstrap(peer_hello, addr):
            merged_nodes = merge_group_join_nodes(
                [
                    {
                        "node_id": node.node_id,
                        "name": node.name,
                        "ip": node.ip,
                        "port": node.port,
                        "note": getattr(node, "note", "") or "",
                    }
                    for node in self.ctx.nodes
                ],
                requester_node_id=peer_hello.node_id,
                requester_ip=str(addr[0]),
            )
            detail = "현재 노드 그룹 정보를 전달했습니다."
            if hasattr(self.coord_client, "request_node_list_update"):
                sent = self.coord_client.request_node_list_update(merged_nodes, rename_map={})
                if sent:
                    detail = "노드 그룹에 참여할 수 있도록 현재 목록을 동기화했습니다."
                else:
                    detail = "현재 노드 그룹 정보를 전달했지만 코디네이터 동기화는 아직 대기 중입니다."
            return build_group_join_state(
                merged_nodes,
                detail=detail,
                accepted=True,
                layout=None if self.ctx.layout is None else serialize_layout_config(self.ctx.layout),
            )

        self.server.set_bootstrap_handler(_handle_group_join_bootstrap)

    def _notify_status(self, message: str, tone: str = "neutral") -> None:
        if self.qt_runtime_app is not None:
            self.qt_runtime_app.request_status_message(message, tone)

    def _notify_self_ip_sync_failure(self, message: str) -> None:
        if self.qt_runtime_app is not None and hasattr(self.qt_runtime_app, "request_notification"):
            self.qt_runtime_app.request_notification(message, "warning")
            return
        self._notify_status(message, "warning")

    def _current_node_payloads(self, *, override_self_ip: str | None = None) -> list[dict]:
        payloads = []
        for node in self.ctx.nodes:
            payloads.append(
                {
                    "node_id": node.node_id,
                    "name": node.name,
                    "ip": (
                        override_self_ip
                        if override_self_ip and node.node_id == self.ctx.self_node.node_id
                        else node.ip
                    ),
                    "port": node.port,
                    "note": getattr(node, "note", "") or "",
                    "priority": getattr(node, "priority", 0),
                }
            )
        return payloads

    def _apply_self_ip_locally(self, new_ip: str) -> bool:
        if not new_ip:
            return False
        if self.ctx.self_node.ip == new_ip:
            return True
        node_payloads = self._current_node_payloads(override_self_ip=new_ip)
        if self.config_reloader is not None:
            self.config_reloader.apply_nodes_state(
                node_payloads,
                rename_map={},
                persist=True,
                apply_runtime=True,
            )
        else:
            updated_nodes = [
                type(node)(
                    name=node.name,
                    ip=new_ip if node.node_id == self.ctx.self_node.node_id else node.ip,
                    port=node.port,
                    note=node.note,
                    node_id=node.node_id,
                    priority=node.priority,
                )
                for node in self.ctx.nodes
            ]
            self.ctx.replace_nodes(updated_nodes)
            if self.dialer is not None and hasattr(self.dialer, "refresh_peers"):
                self.dialer.refresh_peers()
        return self.ctx.self_node.ip == new_ip

    def _request_self_ip_sync(self) -> bool:
        if self.coord_client is None or not hasattr(self.coord_client, "request_node_list_update"):
            return False
        request_id_factory = getattr(self.coord_client, "_new_request_id", None)
        request_id = request_id_factory() if callable(request_id_factory) else f"self-ip-{time.time_ns()}"
        with self._self_ip_sync_lock:
            pending = self._pending_self_ip_sync
            if isinstance(pending, dict):
                pending["request_id"] = request_id
        return bool(
            self.coord_client.request_node_list_update(
                self._current_node_payloads(),
                rename_map={},
                request_id=request_id,
            )
        )

    def _self_ip_probe_candidates(self, details: dict | None = None) -> list:
        details = details if isinstance(details, dict) else {}
        state = details.get("state")
        coordinator_id = ""
        online_peer_ids = ()
        if state is not None:
            coordinator_id = str(getattr(state, "coordinator_id", "") or "").strip()
            online_peer_ids = tuple(getattr(state, "online_peers", ()) or ())
        candidates = []
        seen = set()
        for node_id in (coordinator_id, *online_peer_ids):
            normalized = str(node_id or "").strip()
            if not normalized or normalized == self.ctx.self_node.node_id or normalized in seen:
                continue
            node = self.ctx.get_node(normalized)
            if node is None:
                continue
            candidates.append(node)
            seen.add(normalized)
        return candidates

    def _probe_local_ip_via_peer(self, node) -> str:
        try:
            with socket.create_connection((node.ip, int(node.port)), timeout=self.SELF_IP_PROBE_TIMEOUT_SEC) as sock:
                local_ip = str(sock.getsockname()[0] or "").strip()
        except OSError as exc:
            logging.debug(tag_message(TAG_CONFIG, "self ip probe failed target=%s reason=%s"), node.display_label(), exc)
            return ""
        if not local_ip or local_ip == "127.0.0.1":
            return ""
        return local_ip

    def _resolve_self_ip_from_known_peers(self, previous_ip: str, details: dict | None = None) -> str:
        details = details if isinstance(details, dict) else {}
        local_ips = {str(ip).strip() for ip in details.get("local_ips", ()) if str(ip).strip()}
        for node in self._self_ip_probe_candidates(details):
            local_ip = self._probe_local_ip_via_peer(node)
            if not local_ip:
                continue
            if local_ips and local_ip not in local_ips:
                continue
            if local_ip == previous_ip:
                continue
            return local_ip
        return ""

    def _sync_self_ip_change(self, new_ip: str, *, announce: bool) -> bool:
        try:
            applied = self._apply_self_ip_locally(new_ip)
        except Exception as exc:
            logging.warning(tag_message(TAG_CONFIG, "failed to apply self ip change locally: %s"), exc)
            self._notify_self_ip_sync_failure("내 PC IP 변경을 반영하지 못했습니다.")
            with self._self_ip_sync_lock:
                self._pending_self_ip_sync = None
            return False
        if not applied:
            with self._self_ip_sync_lock:
                self._pending_self_ip_sync = None
            return False
        sent = self._request_self_ip_sync()
        if not sent:
            with self._self_ip_sync_lock:
                self._pending_self_ip_sync = None
            self._notify_self_ip_sync_failure(
                "내 PC IP는 반영됐지만 다른 PC에 동기화 요청을 보내지 못했습니다.",
            )
            return False
        if announce:
            self._notify_status("내 PC IP가 변경되어 구성원 정보를 다시 동기화하고 있습니다.", "accent")
        return True

    def _handle_self_ip_change(self, previous_ip: str, current_ip: str, details: dict | None = None) -> None:
        previous_ip = str(previous_ip or "").strip()
        current_ip = str(current_ip or "").strip()
        if current_ip == previous_ip:
            return
        if not current_ip:
            current_ip = self._resolve_self_ip_from_known_peers(previous_ip, details)
            if not current_ip:
                self._notify_self_ip_sync_failure(
                    "내 PC IP 변경은 감지됐지만 새 연결 경로를 확인하지 못해 자동 전환하지 않았습니다.",
                )
                return
        with self._self_ip_sync_lock:
            pending = self._pending_self_ip_sync
            if isinstance(pending, dict) and pending.get("desired_ip") == current_ip:
                return
            self._pending_self_ip_sync = {"desired_ip": current_ip, "retry_count": 0}
        logging.info(tag_message(TAG_CONFIG, "self ip changed %s -> %s"), previous_ip or "-", current_ip)
        self._sync_self_ip_change(current_ip, announce=True)

    def _handle_self_ip_sync_node_list_change(self, payload: dict | None = None) -> None:
        payload = payload if isinstance(payload, dict) else {}
        reject_reason = str(payload.get("reject_reason") or "").strip()
        request_id = str(payload.get("request_id") or "").strip()
        retry_ip = ""
        exhausted = False
        with self._self_ip_sync_lock:
            pending = self._pending_self_ip_sync
            if not isinstance(pending, dict):
                return
            desired_ip = str(pending.get("desired_ip") or "").strip()
            pending_request_id = str(pending.get("request_id") or "").strip()
            if not desired_ip:
                self._pending_self_ip_sync = None
                return
            if pending_request_id and request_id != pending_request_id:
                return
            if reject_reason == "stale_revision":
                retry_count = int(pending.get("retry_count") or 0)
                if retry_count >= self.SELF_IP_SYNC_MAX_RETRIES:
                    self._pending_self_ip_sync = None
                    exhausted = True
                else:
                    pending["retry_count"] = retry_count + 1
                    pending.pop("request_id", None)
                    retry_ip = desired_ip
            elif reject_reason == "timeout":
                self._pending_self_ip_sync = None
                exhausted = True
            elif pending_request_id and self.ctx.self_node.ip == desired_ip:
                self._pending_self_ip_sync = None
                return
            else:
                return
        if exhausted:
            self._notify_self_ip_sync_failure(
                "내 PC IP 변경을 다른 PC에 동기화하지 못했습니다. 잠시 후 다시 시도해 주세요.",
            )
            return
        self._sync_self_ip_change(retry_ip, announce=False)

    def _announce_hotkey(self, message: str, *, tone: str = "neutral") -> None:
        notify_runtime_message(self.qt_runtime_app, message, tone)

    def _handle_peer_reject(self, peer_id: str, reject) -> None:
        message = format_peer_reject_notice(self.ctx, peer_id, reject.reason, reject.detail)
        notify_runtime_message(self.qt_runtime_app, message, "warning")

    def _handle_target_result(
        self,
        status: str,
        target_id: str,
        reason: str | None,
        source: str | None,
    ) -> None:
        if source not in {"hotkey", "ui", "tray"}:
            return
        if status == "active":
            if source == "hotkey":
                self._announce_hotkey(f"PC 전환: {target_id}", tone="accent")
            else:
                self._notify_status(f"PC 전환 완료: {target_id}", tone="accent")
            return
        if status != "failed":
            return
        reason_text = {
            "target_offline": "대상 PC가 오프라인입니다.",
            "held_by_other": "다른 사용자가 현재 제어 중입니다.",
            "local_activity": "대상 PC에서 로컬 입력이 감지되었습니다.",
            "coordinator_unreachable": "코디네이터에 연결할 수 없습니다.",
        }.get(reason, "전환을 완료하지 못했습니다.")
        message = f"PC 전환 실패: {target_id} | {reason_text}"
        if source == "hotkey":
            self._announce_hotkey(message, tone="warning")
        else:
            self._notify_status(message, tone="warning")

    def _prepare_pointer_handoff(self, target_id: str) -> None:
        if not hasattr(self.router, "prepare_pointer_handoff"):
            return
        anchor_event = build_target_primary_center_anchor(self.ctx, target_id)
        if anchor_event is None:
            current_pos = self.local_cursor.position()
            if current_pos is None:
                return
            anchor_event = enrich_pointer_event(
                make_mouse_move_event(int(current_pos[0]), int(current_pos[1])),
                get_virtual_screen_bounds(),
            )
        self.router.prepare_pointer_handoff(anchor_event)

    def _online_target_ids(self):
        online_ids = {
            node_id
            for node_id, conn in self.registry.all()
            if conn is not None and not conn.closed
        }
        ordered = []
        for node in self.ctx.nodes:
            if node.node_id in online_ids:
                ordered.append(node.node_id)
        if ordered:
            ordered.insert(0, self.ctx.self_node.node_id)
        return ordered

    def _cycle_previous(self):
        current = self.router.get_requested_target()
        next_id = self.cycler.previous()
        if next_id == self.ctx.self_node.node_id:
            self.auto_switcher.sync_self_pointer_state()
        if next_id is None:
            self._announce_hotkey("PC 전환: 가능한 온라인 PC 없음", tone="warning")
        elif next_id == current:
            self._announce_hotkey(f"PC 전환: {next_id} 이미 선택됨")
        elif next_id == self.ctx.self_node.node_id:
            self._announce_hotkey("PC 전환: 내 PC", tone="accent")

    def _cycle_next(self):
        current = self.router.get_requested_target()
        next_id = self.cycler.next()
        if next_id == self.ctx.self_node.node_id:
            self.auto_switcher.sync_self_pointer_state()
        if next_id is None:
            self._announce_hotkey("PC 전환: 가능한 온라인 PC 없음", tone="warning")
        elif next_id == current:
            self._announce_hotkey(f"PC 전환: {next_id} 이미 선택됨")
        elif next_id == self.ctx.self_node.node_id:
            self._announce_hotkey("PC 전환: 내 PC", tone="accent")

    def _apply_toggle_auto_switch(self):
        if self.ctx.layout is None:
            return
        enabled = not self.ctx.layout.auto_switch.enabled
        applied_globally = False
        if self.coord_client is not None:
            try:
                applied_globally = self.coord_client.request_auto_switch_enabled(enabled)
            except Exception as exc:
                logging.warning(tag_message(TAG_HOTKEY, "failed to request shared auto switch toggle: %s"), exc)
                applied_globally = False
        if not applied_globally:
            next_layout = replace_auto_switch_settings(self.ctx.layout, enabled=enabled)
            self.ctx.replace_layout(next_layout)
            if self.config_reloader is not None:
                try:
                    self.config_reloader.apply_layout(next_layout, persist=True, debounce_persist=False)
                except Exception as exc:
                    logging.warning(tag_message(TAG_HOTKEY, "failed to persist auto switch toggle: %s"), exc)
        logging.info(
            tag_message(TAG_HOTKEY, "%s %s auto boundary switching"),
            self.ctx.settings.hotkeys.toggle_auto_switch,
            "enabled" if enabled else "disabled",
        )
        self.capture.put_event(
            make_system_event(
                f"{self.ctx.settings.hotkeys.toggle_auto_switch} toggled auto boundary switching "
                f"{'on' if enabled else 'off'}"
            )
        )
        self._announce_hotkey(
            f"자동 경계 전환: {'ON' if enabled else 'OFF'}",
            tone="success" if enabled else "neutral",
        )

    def _toggle_auto_switch(self):
        self.auto_switch_hotkey_action.trigger(
            self._apply_toggle_auto_switch,
            on_busy=lambda: self._announce_hotkey("자동 경계 전환 변경을 적용하는 중입니다."),
        )

    def _quit_application(self):
        logging.info(tag_message(TAG_HOTKEY, "%s quitting application"), self.ctx.settings.hotkeys.quit_app)
        self.capture.put_event(
            make_system_event(f"{self.ctx.settings.hotkeys.quit_app} input detected, quitting app")
        )
        self._announce_hotkey("앱 종료")
        self.shutdown_evt.set()
        if self.qt_runtime_app is not None:
            self.qt_runtime_app.request_quit()
        else:
            self.capture.stop()

    def _create_windows_global_hotkeys(self):
        if not sys.platform.startswith("win"):
            return None
        try:
            from app.config.app_settings import hotkey_to_windows_binding
            from msp_platform.windows.windows_global_hotkeys import WindowsGlobalHotkeyManager

            windows_hotkeys = {
                "cycle-target-prev": (self.ctx.settings.hotkeys.previous_target, self._cycle_previous),
                "cycle-target-next": (self.ctx.settings.hotkeys.next_target, self._cycle_next),
                "toggle-auto-switch": (self.ctx.settings.hotkeys.toggle_auto_switch, self._toggle_auto_switch),
                "quit-application": (self.ctx.settings.hotkeys.quit_app, self._quit_application),
            }
            bindings = []
            for binding_name, (hotkey_value, callback) in windows_hotkeys.items():
                modifiers, vk_code = hotkey_to_windows_binding(hotkey_value)
                bindings.append(
                    {
                        "name": binding_name,
                        "modifiers": modifiers,
                        "vk": vk_code,
                        "callback": callback,
                    }
                )
            return WindowsGlobalHotkeyManager(bindings)
        except Exception as exc:
            logging.warning(tag_message(TAG_HOTKEY, "Windows global hotkey registration unavailable: %s"), exc)
            return None

    def _configure_runtime_controls(self):
        if self.capture is None or self.router is None:
            return

        from msp_platform.capture.hotkey import HotkeyMatcher, TargetCycler

        self.dialer.reject_callback = self._handle_peer_reject
        self.coord_client.add_target_result_listener(self._handle_target_result)
        self.auto_switch_hotkey_action = AsyncHotkeyAction("toggle-auto-switch")
        self.cycler = TargetCycler(
            self.ctx,
            self.router,
            coord_client=self.coord_client,
            targets_provider=self._online_target_ids,
            before_select=self._prepare_pointer_handoff,
        )
        previous_modifiers, previous_trigger = hotkey_to_matcher_parts(
            self.ctx.settings.hotkeys.previous_target
        )
        next_modifiers, next_trigger = hotkey_to_matcher_parts(self.ctx.settings.hotkeys.next_target)
        toggle_modifiers, toggle_trigger = hotkey_to_matcher_parts(
            self.ctx.settings.hotkeys.toggle_auto_switch
        )
        quit_modifiers, quit_trigger = hotkey_to_matcher_parts(self.ctx.settings.hotkeys.quit_app)

        self.global_hotkeys = self._create_windows_global_hotkeys()

        install_capture_hotkey_fallbacks(
            self.capture,
            HotkeyMatcher,
            (
                {
                    "binding_name": "cycle-target-prev",
                    "modifier_groups": previous_modifiers,
                    "trigger": previous_trigger,
                    "callback": self._cycle_previous,
                    "matcher_name": "cycle-target-prev",
                },
                {
                    "binding_name": "cycle-target-next",
                    "modifier_groups": next_modifiers,
                    "trigger": next_trigger,
                    "callback": self._cycle_next,
                    "matcher_name": "cycle-target-next",
                },
                {
                    "binding_name": "toggle-auto-switch",
                    "modifier_groups": toggle_modifiers,
                    "trigger": toggle_trigger,
                    "callback": self._toggle_auto_switch,
                    "matcher_name": "toggle-auto-switch",
                },
                {
                    "binding_name": "quit-application",
                    "modifier_groups": quit_modifiers,
                    "trigger": quit_trigger,
                    "callback": self._quit_application,
                    "matcher_name": "quit-application",
                },
            ),
            registered_global_hotkeys=(),
        )
        logging.info(tag_message(TAG_HOTKEY, "%s selects previous target"), self.ctx.settings.hotkeys.previous_target)
        logging.info(tag_message(TAG_HOTKEY, "%s selects next target"), self.ctx.settings.hotkeys.next_target)
        logging.info(
            tag_message(TAG_HOTKEY, "%s toggles auto boundary switching"),
            self.ctx.settings.hotkeys.toggle_auto_switch,
        )
        logging.info(tag_message(TAG_HOTKEY, "%s quits the application"), self.ctx.settings.hotkeys.quit_app)

    def _configure_local_input_override(self):
        if self.capture is None or self.sink is None:
            return

        def _local_input_override():
            controller_id = self.sink.get_authorized_controller()
            if not controller_id or controller_id == self.ctx.self_node.node_id:
                return
            if hasattr(self.sink, "remote_input_recent") and self.sink.remote_input_recent():
                return
            self.coord_client.notify_local_input_override()

        self.capture.local_activity_callback = _local_input_override
