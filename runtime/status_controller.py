"""Qt-aware status polling and diff emission."""

from __future__ import annotations

from collections import deque
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal

from runtime.state_watcher import RuntimeState, describe_state_changes
from runtime.status_view import build_status_view


def _fingerprint_summary(view):
    return (
        tuple((card.title, card.value, card.detail, card.tone) for card in view.summary_cards),
        view.monitor_alert,
        view.monitor_alert_tone,
        view.self_id,
        view.coordinator_id,
        view.selected_target,
        view.authorized_controller,
        view.config_path,
    )


def _fingerprint_targets(view):
    return tuple(
        (
            target.node_id,
            target.online,
            target.selected,
            target.state,
            target.subtitle,
            tuple((badge.text, badge.tone) for badge in target.badges),
            target.layout_summary,
            target.display_count,
        )
        for target in view.targets
    )


def _fingerprint_peers(view):
    return tuple(
        (
            peer.node_id,
            peer.online,
            peer.is_coordinator,
            peer.is_authorized_controller,
            peer.layout_summary,
            peer.display_count,
            tuple((badge.text, badge.tone) for badge in peer.badges),
            peer.last_seen,
            peer.detection_summary,
            peer.freshness_label,
            peer.freshness_tone,
            peer.diff_summary,
            peer.has_monitor_diff,
        )
        for peer in view.peers
    )


def _fingerprint_layout(view, layout_edit_state=None):
    return (
        view.selected_target,
        view.coordinator_id,
        view.authorized_controller,
        layout_edit_state,
        tuple(
            (
                node.node_id,
                node.subtitle,
                tuple((badge.text, badge.tone) for badge in node.badges),
                tuple((field.label, field.value) for field in node.fields),
            )
            for node in view.node_details
        ),
    )


def _fingerprint_detail(detail):
    return (
        detail.node_id,
        detail.title,
        detail.subtitle,
        tuple((badge.text, badge.tone) for badge in detail.badges),
        tuple((field.label, field.value) for field in detail.fields),
        detail.action_label,
    )


class StatusController(QObject):
    summaryChanged = Signal(object)
    targetsChanged = Signal(object)
    peersChanged = Signal(object)
    selectedNodeChanged = Signal(object)
    layoutChanged = Signal(object)
    monitorInventoryChanged = Signal(object)
    advancedChanged = Signal(object)
    messageChanged = Signal(str, str)
    busyChanged = Signal(bool)

    def __init__(
        self,
        ctx,
        registry,
        coordinator_resolver,
        *,
        router=None,
        sink=None,
        coord_client=None,
        refresh_ms: int = 250,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.ctx = ctx
        self.registry = registry
        self.coordinator_resolver = coordinator_resolver
        self.router = router
        self.sink = sink
        self.coord_client = coord_client
        self.refresh_ms = refresh_ms
        self.selected_node_id = ctx.self_node.node_id
        self._last_seen: dict[str, datetime] = {}
        self._events = deque(maxlen=40)
        self._previous_runtime_state: RuntimeState | None = None
        self._current_view = None
        self._current_message = ("", "neutral")
        self._busy = False
        self._summary_signature = None
        self._targets_signature = None
        self._peers_signature = None
        self._layout_signature = None
        self._monitor_signature = None
        self._detail_signature = None
        self._advanced_signature = None
        self._timer = QTimer(self)
        self._timer.setInterval(refresh_ms)
        self._timer.timeout.connect(self.refresh_now)

    @property
    def current_view(self):
        return self._current_view

    @property
    def events(self) -> tuple[str, ...]:
        return tuple(self._events)

    def start(self) -> None:
        self.refresh_now()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def set_selected_node(self, node_id: str | None) -> None:
        selected = node_id or self.ctx.self_node.node_id
        if self.selected_node_id == selected:
            return
        self.selected_node_id = selected
        self._emit_selected_detail()

    def set_message(self, message: str, tone: str = "neutral") -> None:
        payload = (message, tone)
        if payload == self._current_message:
            return
        self._current_message = payload
        self.messageChanged.emit(message, tone)

    def set_busy(self, busy: bool) -> None:
        if self._busy == busy:
            return
        self._busy = busy
        self.busyChanged.emit(busy)

    def refresh_now(self) -> None:
        now = datetime.now()
        for node_id, conn in self.registry.all():
            if conn and not conn.closed:
                self._last_seen[node_id] = now
        self._last_seen.setdefault(self.ctx.self_node.node_id, now)

        view = build_status_view(
            self.ctx,
            self.registry,
            self.coordinator_resolver,
            router=self.router,
            sink=self.sink,
            last_seen=self._last_seen,
        )
        self._current_view = view

        self._emit_section("summary", _fingerprint_summary(view), self.summaryChanged, view)
        self._emit_section("targets", _fingerprint_targets(view), self.targetsChanged, view.targets)
        self._emit_section("peers", _fingerprint_peers(view), self.peersChanged, view.peers)
        self._emit_section(
            "layout",
            _fingerprint_layout(view, self._layout_edit_state_signature()),
            self.layoutChanged,
            view,
        )
        self._emit_section(
            "monitor",
            tuple((peer.node_id, peer.freshness_label, peer.diff_summary) for peer in view.peers),
            self.monitorInventoryChanged,
            view,
        )
        self._emit_selected_detail()
        self._emit_advanced(view)

    def _emit_section(self, name: str, signature, signal: Signal, payload) -> None:
        attribute = f"_{name}_signature"
        if getattr(self, attribute) == signature:
            return
        setattr(self, attribute, signature)
        signal.emit(payload)

    def _selected_detail(self):
        view = self._current_view
        if view is None:
            return None
        selected = self.selected_node_id or view.self_id
        for detail in view.node_details:
            if detail.node_id == selected:
                return detail
        return view.selected_detail

    def _emit_selected_detail(self) -> None:
        detail = self._selected_detail()
        if detail is None:
            return
        signature = _fingerprint_detail(detail)
        if signature == self._detail_signature:
            return
        self._detail_signature = signature
        self.selectedNodeChanged.emit(detail)

    def _layout_edit_state_signature(self):
        if self.coord_client is None:
            return None
        editor_id = None
        if hasattr(self.coord_client, "get_layout_editor"):
            editor_id = self.coord_client.get_layout_editor()
        deny_reason = None
        if hasattr(self.coord_client, "get_layout_edit_denial"):
            deny_reason = self.coord_client.get_layout_edit_denial()
        return (
            bool(getattr(self.coord_client, "is_layout_editor", lambda: False)()),
            bool(getattr(self.coord_client, "is_layout_edit_pending", lambda: False)()),
            editor_id,
            deny_reason,
        )

    def _emit_advanced(self, view) -> None:
        current_runtime_state = RuntimeState(
            coordinator_id=view.coordinator_id,
            online_peers=view.online_peers,
            router_state=None if self.router is None else self.router.get_target_state(),
            requested_target=(
                None
                if self.router is None
                else (
                    self.router.get_requested_target()
                    if hasattr(self.router, "get_requested_target")
                    else self.router.get_selected_target()
                )
            ),
            active_target=(
                None
                if self.router is None
                else (
                    self.router.get_active_target()
                    if hasattr(self.router, "get_active_target")
                    else (
                        self.router.get_selected_target()
                        if self.router.get_target_state() == "active"
                        else None
                    )
                )
            ),
            authorized_controller=view.authorized_controller,
            monitor_alert=view.monitor_alert,
        )
        for message in describe_state_changes(self._previous_runtime_state, current_runtime_state):
            self._events.appendleft(message)
        self._previous_runtime_state = current_runtime_state
        payload = {
            "runtime": {
                "self_id": view.self_id,
                "coordinator_id": view.coordinator_id or "-",
                "selected_target": view.selected_target or "-",
                "router_state": view.router_state or "-",
                "authorized_controller": view.authorized_controller or "-",
                "connected_peers": f"{view.connected_peer_count}/{view.total_peer_count}",
                "config_path": view.config_path or "-",
            },
            "events": tuple(self._events),
            "busy": self._busy,
        }
        signature = (
            tuple(payload["runtime"].items()),
            payload["events"],
            payload["busy"],
        )
        if signature == self._advanced_signature:
            return
        self._advanced_signature = signature
        self.advancedChanged.emit(payload)
