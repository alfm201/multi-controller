"""User-facing status view models and text helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.update.app_version import (
    build_version_compatibility_report,
    get_current_compatibility_version,
    get_current_version,
)
from model.display.layouts import LayoutNode, monitor_topology_to_rows
from model.display.monitor_inventory import (
    compare_detected_and_physical_rows,
    describe_monitor_freshness,
    snapshot_to_logical_rows,
    summarize_monitor_diff,
)


@dataclass(frozen=True)
class BadgeView:
    text: str
    tone: str = "neutral"


@dataclass(frozen=True)
class SummaryCardView:
    title: str
    value: str
    detail: str
    tone: str = "neutral"


@dataclass(frozen=True)
class InspectorFieldView:
    label: str
    value: str


@dataclass(frozen=True)
class NodeDetailView:
    node_id: str
    label: str
    title: str
    subtitle: str
    badges: tuple[BadgeView, ...]
    fields: tuple[InspectorFieldView, ...]
    action_label: str


@dataclass(frozen=True)
class TargetView:
    node_id: str
    label: str
    online: bool
    selected: bool
    state: str | None
    subtitle: str
    badges: tuple[BadgeView, ...]
    layout_summary: str
    display_count: int


@dataclass(frozen=True)
class PeerView:
    node_id: str
    name: str
    ip: str
    label: str
    online: bool
    is_coordinator: bool
    is_authorized_controller: bool
    layout_summary: str
    display_count: int
    badges: tuple[BadgeView, ...]
    last_seen: str
    current_version_label: str
    compatibility_version_label: str
    version_status: str
    version_status_label: str
    is_version_compatible: bool
    version_tooltip: str
    detection_summary: str
    freshness_label: str
    freshness_tone: str
    diff_summary: str
    has_monitor_diff: bool


@dataclass(frozen=True)
class StatusView:
    self_id: str
    self_label: str
    self_ip: str
    coordinator_id: str | None
    coordinator_label: str | None
    coordinator_ip: str | None
    online_peers: tuple[str, ...]
    connected_peer_count: int
    total_peer_count: int
    router_state: str | None
    selected_target: str | None
    selected_target_label: str | None
    selected_target_ip: str | None
    authorized_controller: str | None
    authorized_controller_label: str | None
    config_path: str | None
    self_current_version_label: str
    self_compatibility_version_label: str
    self_version_tooltip: str
    peers: tuple[PeerView, ...]
    targets: tuple[TargetView, ...]
    summary_cards: tuple[SummaryCardView, ...]
    node_details: tuple[NodeDetailView, ...]
    selected_detail: NodeDetailView
    monitor_alert: str | None = None
    monitor_alert_tone: str = "neutral"


def build_status_view(
    ctx,
    registry,
    coordinator_resolver,
    router=None,
    sink=None,
    last_seen: dict[str, datetime] | None = None,
    version_cache: dict[str, tuple[str | None, str | None]] | None = None,
):
    coordinator = coordinator_resolver()
    coordinator_id = None if coordinator is None else coordinator.node_id
    node_labels = {node.node_id: node.display_label() for node in ctx.nodes}
    node_names = {node.node_id: node.name for node in ctx.nodes}
    node_ips = {node.node_id: node.ip for node in ctx.nodes}
    live_connections = {
        node_id: conn
        for node_id, conn in registry.all()
        if conn is not None and not conn.closed
    }
    online_peers = tuple(sorted(live_connections))
    active_target = None
    if router is not None:
        if hasattr(router, "get_active_target"):
            active_target = router.get_active_target()
        elif getattr(router, "get_target_state", lambda: None)() == "active":
            active_target = router.get_selected_target()
    active_target_online = (
        active_target is not None
        and (
            active_target == ctx.self_node.node_id
            or active_target in online_peers
        )
    )
    router_state = "active" if active_target_online else None
    selected_target = active_target if active_target_online else None
    authorized_controller = None if sink is None else sink.get_authorized_controller()
    layout = ctx.layout
    last_seen = {} if last_seen is None else dict(last_seen)
    version_cache = {} if version_cache is None else dict(version_cache)
    now = datetime.now()
    local_compatibility_version = get_current_compatibility_version()
    self_version_report = build_version_compatibility_report(
        current_version=get_current_version(),
        compatibility_version=local_compatibility_version,
        local_compatibility_version=local_compatibility_version,
    )

    node_details = []
    peers = []
    targets = []
    diff_node_ids = []
    stale_node_ids = []

    for node in ctx.peers:
        conn = live_connections.get(node.node_id)
        layout_node = None if layout is None else layout.get_node(node.node_id)
        snapshot = ctx.get_monitor_inventory(node.node_id)
        online = node.node_id in online_peers
        freshness = describe_monitor_freshness(
            snapshot,
            online=online,
            now=now,
            last_seen_at=last_seen.get(node.node_id),
        )
        diff_summary, has_monitor_diff = _monitor_diff_summary(layout_node, snapshot)
        cached_current_version, cached_compatibility_version = version_cache.get(
            node.node_id,
            (None, None),
        )
        version_report = build_version_compatibility_report(
            current_version=(
                getattr(conn, "peer_app_version", None)
                if conn is not None
                else cached_current_version
            ),
            compatibility_version=(
                getattr(conn, "peer_compatibility_version", None)
                if conn is not None
                else cached_compatibility_version
            ),
            local_compatibility_version=local_compatibility_version,
        )
        if online and freshness.is_stale:
            stale_node_ids.append(node.node_id)
        if online and not freshness.is_stale and has_monitor_diff:
            diff_node_ids.append(node.node_id)

        last_seen_text = _format_relative_last_seen(
            last_seen.get(node.node_id),
            now,
            online=online,
        )

        detail = _build_node_detail_view(
            node_id=node.node_id,
            node_label=node_labels.get(node.node_id, node.node_id),
            online=online,
            is_coordinator=node.node_id == coordinator_id,
            is_authorized_controller=node.node_id == authorized_controller,
            is_selected_target=node.node_id == selected_target,
            router_state=router_state,
            layout_node=layout_node,
            snapshot=snapshot,
            last_seen=last_seen_text,
            is_self=False,
            freshness=freshness,
            diff_summary=diff_summary,
            has_monitor_diff=has_monitor_diff,
            current_version_label=version_report.current_version_label,
            compatibility_version_label=version_report.compatibility_version_label,
            version_status=version_report.status,
            version_status_label=version_report.status_label,
            is_version_compatible=version_report.is_compatible,
        )
        node_details.append(detail)
        peers.append(
            PeerView(
                node_id=node.node_id,
                name=node_names.get(node.node_id, node.node_id),
                ip=node_ips.get(node.node_id, ""),
                label=node_labels.get(node.node_id, node.node_id),
                online=online,
                is_coordinator=node.node_id == coordinator_id,
                is_authorized_controller=node.node_id == authorized_controller,
                layout_summary=_layout_summary(layout_node),
                display_count=_display_count(layout_node, snapshot),
                badges=detail.badges,
                last_seen=last_seen_text,
                current_version_label=version_report.current_version_label,
                compatibility_version_label=version_report.compatibility_version_label,
                version_status=version_report.status,
                version_status_label=version_report.status_label,
                is_version_compatible=version_report.is_compatible,
                version_tooltip=version_report.tooltip,
                detection_summary=_detection_summary(layout_node, snapshot),
                freshness_label=freshness.label,
                freshness_tone=freshness.tone,
                diff_summary=diff_summary,
                has_monitor_diff=has_monitor_diff,
            )
        )
        targets.append(
            TargetView(
                node_id=node.node_id,
                label=node_labels.get(node.node_id, node.node_id),
                online=online,
                selected=node.node_id == selected_target,
                state=router_state if node.node_id == selected_target else None,
                subtitle=_target_subtitle(
                    online=online,
                    selected=node.node_id == selected_target,
                    state=router_state,
                ),
                badges=_target_badges(
                    online=online,
                    selected=node.node_id == selected_target,
                    state=router_state,
                ),
                layout_summary=_layout_summary(layout_node),
                display_count=_display_count(layout_node, snapshot),
            )
        )

    self_layout_node = None if layout is None else layout.get_node(ctx.self_node.node_id)
    self_snapshot = ctx.get_monitor_inventory(ctx.self_node.node_id)
    self_freshness = describe_monitor_freshness(
        self_snapshot,
        online=True,
        now=now,
        last_seen_at=last_seen.get(ctx.self_node.node_id),
    )
    self_diff_summary, self_has_monitor_diff = _monitor_diff_summary(
        self_layout_node,
        self_snapshot,
    )
    if not self_freshness.is_stale and self_has_monitor_diff:
        diff_node_ids.insert(0, ctx.self_node.node_id)

    self_last_seen_text = _format_relative_last_seen(
        last_seen.get(ctx.self_node.node_id),
        now,
        online=True,
    )

    self_detail = _build_node_detail_view(
        node_id=ctx.self_node.node_id,
        node_label=node_labels.get(ctx.self_node.node_id, ctx.self_node.node_id),
        online=True,
        is_coordinator=ctx.self_node.node_id == coordinator_id,
        is_authorized_controller=ctx.self_node.node_id == authorized_controller,
        is_selected_target=selected_target is None,
        router_state=router_state,
        layout_node=self_layout_node,
        snapshot=self_snapshot,
        last_seen=self_last_seen_text,
        is_self=True,
        freshness=self_freshness,
        diff_summary=self_diff_summary,
        has_monitor_diff=self_has_monitor_diff,
        current_version_label=self_version_report.current_version_label,
        compatibility_version_label=self_version_report.compatibility_version_label,
        version_status=self_version_report.status,
        version_status_label=self_version_report.status_label,
        is_version_compatible=self_version_report.is_compatible,
    )
    node_details.insert(0, self_detail)
    detail_by_id = {detail.node_id: detail for detail in node_details}
    selected_detail = detail_by_id.get(selected_target or ctx.self_node.node_id, self_detail)

    summary_cards = _build_summary_cards(
        selected_target=selected_target,
        selected_target_label=None if selected_target is None else node_labels.get(selected_target, selected_target),
        selected_target_name=None if selected_target is None else node_names.get(selected_target, selected_target),
        router_state=router_state,
        connected_peer_count=len(online_peers) + 1,
        total_peer_count=len(ctx.peers) + 1,
        coordinator_id=coordinator_id,
        coordinator_label=None if coordinator_id is None else node_labels.get(coordinator_id, coordinator_id),
        coordinator_name=None if coordinator_id is None else node_names.get(coordinator_id, coordinator_id),
        local_detected_count=0 if self_snapshot is None else len(self_snapshot.monitors),
        local_freshness=self_freshness,
        diff_node_ids=tuple(diff_node_ids),
        stale_node_ids=tuple(stale_node_ids),
    )
    monitor_alert, monitor_alert_tone = _build_monitor_alert(
        diff_node_ids=tuple(diff_node_ids),
        stale_node_ids=tuple(stale_node_ids),
    )

    view = StatusView(
        self_id=ctx.self_node.node_id,
        self_label=node_labels.get(ctx.self_node.node_id, ctx.self_node.node_id),
        self_ip=node_ips.get(ctx.self_node.node_id, ctx.self_node.ip),
        coordinator_id=coordinator_id,
        coordinator_label=None if coordinator_id is None else node_labels.get(coordinator_id, coordinator_id),
        coordinator_ip=None if coordinator_id is None else node_ips.get(coordinator_id),
        online_peers=online_peers,
        connected_peer_count=len(online_peers) + 1,
        total_peer_count=len(ctx.peers) + 1,
        router_state=router_state,
        selected_target=selected_target,
        selected_target_label=None if selected_target is None else node_labels.get(selected_target, selected_target),
        selected_target_ip=None if selected_target is None else node_ips.get(selected_target),
        authorized_controller=authorized_controller,
        authorized_controller_label=(
            None
            if authorized_controller is None
            else node_labels.get(authorized_controller, authorized_controller)
        ),
        config_path=None if ctx.config_path is None else str(ctx.config_path),
        self_current_version_label=self_version_report.current_version_label,
        self_compatibility_version_label=self_version_report.compatibility_version_label,
        self_version_tooltip=self_version_report.tooltip,
        peers=tuple(peers),
        targets=tuple(targets),
        summary_cards=summary_cards,
        node_details=tuple(node_details),
        selected_detail=selected_detail,
        monitor_alert=monitor_alert,
        monitor_alert_tone=monitor_alert_tone,
    )
    return view


def build_primary_status_text(view: StatusView) -> str:
    if view.selected_target and view.router_state == "active":
        return f"{view.selected_target_label or view.selected_target} PC가 현재 제어 대상입니다."
    if view.total_peer_count <= 1:
        return "설정된 다른 PC가 없습니다."
    if view.connected_peer_count <= 1:
        return "다른 PC 연결을 기다리는 중입니다."
    return ""


def build_connection_summary_text(view: StatusView) -> str:
    return f"연결된 PC {view.connected_peer_count} / {view.total_peer_count}"


def build_selection_hint_text(view: StatusView) -> str:
    return ""


def build_peer_summary_text(peer: PeerView) -> str:
    parts = [peer.label, "연결됨" if peer.online else "오프라인"]
    if peer.is_authorized_controller:
        parts.append("제어권 보유")
    return " | ".join(parts)


def build_target_button_text(target: TargetView) -> str:
    status = "연결됨" if target.online else "오프라인"
    if target.selected and target.state == "active":
        detail = "사용 중"
    else:
        detail = "준비됨" if target.online else "대기 중"
    return f"{target.label} | {status} | {detail}"


def build_advanced_peer_text(peer: PeerView) -> str:
    detection_summary = getattr(peer, "detection_summary", None)
    if detection_summary is None:
        detection_summary = "모니터 기준 정보 없음"
    parts = [peer.label, "연결됨" if peer.online else "연결 끊김", detection_summary]
    if peer.is_coordinator:
        parts.append("코디네이터")
    if peer.is_authorized_controller:
        parts.append("제어권 보유")
    return " | ".join(parts)


def build_layout_editor_hint(
    editing_enabled: bool,
    editor_id: str | None,
    self_id: str,
    pending: bool = False,
) -> str:
    if pending and editor_id != self_id:
        mode_text = "편집 모드: 대기 중"
    elif editing_enabled and editor_id == self_id:
        mode_text = "편집 모드: 켜짐"
    elif editor_id and editor_id != self_id:
        mode_text = f"편집 모드: {editor_id} PC가 사용 중"
    else:
        mode_text = "편집 모드: 꺼짐"
    if editor_id == self_id:
        detail_text = "빈 공간 또는 오른쪽 버튼 드래그로 화면을 이동하세요"
    elif editor_id:
        detail_text = f"{editor_id} PC가 현재 편집 중입니다"
    else:
        detail_text = "선택한 PC의 모니터 맵을 수정하세요"
    return " | ".join((mode_text, detail_text))


def build_layout_lock_text(
    editor_id: str | None,
    self_id: str,
    pending: bool = False,
) -> str:
    if pending and editor_id != self_id:
        return "편집 잠금: 대기 중"
    if editor_id == self_id:
        return "편집 잠금: 내 편집"
    if editor_id:
        return f"편집 잠금: {editor_id} 사용 중"
    return "편집 잠금: 비어 있음"


def build_layout_node_label(
    node_label: str,
    *,
    is_self: bool,
    is_online: bool,
    is_selected: bool,
    state: str | None,
) -> str:
    lines = [node_label]
    if is_self:
        lines.append("내 PC")
    elif is_selected and state == "active":
        lines.append("사용 중")
    elif is_online:
        lines.append("연결됨")
    else:
        lines.append("오프라인")
    return "\n".join(lines)


def build_layout_node_colors(
    *,
    is_self: bool,
    is_online: bool,
    is_selected: bool,
    state: str | None,
) -> tuple[str, str]:
    if is_self:
        return ("#dcefd8", "#2f6b3b")
    if not is_online:
        return ("#e5e7eb", "#6b7280")
    if is_selected and state == "active":
        return ("#d9eefc", "#176087")
    return ("#f4f4f5", "#4b5563")


def build_selected_node_text(node: LayoutNode | None, *, node_label: str | None = None) -> str:
    if node is None:
        return "선택된 PC: -"
    label = node_label or node.node_id
    if node.monitor_source == "fallback":
        return f"선택된 PC: {label} | 모니터 감지 대기"
    return f"선택된 PC: {label} | 모니터 {len(node.monitors().physical)}개"


def build_layout_inspector_detail(
    node: LayoutNode | None,
    *,
    node_id: str | None,
    node_label: str | None = None,
    is_self: bool,
    is_online: bool,
    state: str | None,
    can_edit: bool,
) -> NodeDetailView:
    if node is None or node_id is None:
        return NodeDetailView(
            node_id="-",
            label="-",
            title="선택된 PC 없음",
            subtitle="레이아웃 캔버스나 상태 탭에서 PC를 선택해 주세요.",
            badges=(BadgeView("대기", "neutral"),),
            fields=(
                InspectorFieldView("물리 크기", "-"),
                InspectorFieldView("논리 크기", "-"),
                InspectorFieldView("모니터 수", "-"),
                InspectorFieldView("편집", "대상 선택 필요"),
            ),
            action_label="선택한 PC의 모니터 맵을 수정하세요",
        )

    logical = monitor_topology_to_rows(node.monitors(), logical=True)
    physical = monitor_topology_to_rows(node.monitors(), logical=False)
    badges = [BadgeView("내 PC", "accent")] if is_self else []
    badges.append(BadgeView("연결됨" if is_online else "오프라인", "success" if is_online else "danger"))
    if state == "active":
        badges.append(BadgeView("현재 대상", "accent"))
    if node.monitor_source.startswith("detected"):
        badges.append(BadgeView("실제 모니터 기준", "success"))
    elif node.monitor_source == "fallback":
        badges.append(BadgeView("감지 대기", "warning"))

    fields = (
        InspectorFieldView("물리 크기", _rows_size_text(physical)),
        InspectorFieldView("논리 크기", _rows_size_text(logical)),
        InspectorFieldView(
            "모니터 수",
            str(len(node.monitors().physical)) if node.monitor_source != "fallback" else "0",
        ),
        InspectorFieldView(
            "편집",
            "가능" if can_edit and node.monitor_source != "fallback" else "읽기 전용",
        ),
    )
    if node.monitor_source == "fallback":
        subtitle = "실제 모니터 감지 정보가 아직 없어 모니터 맵을 편집할 수 없습니다."
    elif can_edit:
        subtitle = "이 PC의 모니터 맵을 확인하거나 수정할 수 있습니다."
    else:
        subtitle = "이 PC를 변경하려면 편집 모드로 들어가야 합니다."
    return NodeDetailView(
        node_id=node_id,
        label=node_label or node_id,
        title=f"{node_label or node_id} PC",
        subtitle=subtitle,
        badges=tuple(badges),
        fields=fields,
        action_label="선택한 PC의 모니터 맵을 수정하세요",
    )


def build_viewport_summary(zoom: float, pan_x: float, pan_y: float) -> str:
    return f"보기: {int(round(zoom * 100))}% | 이동 ({int(round(pan_x))}, {int(round(pan_y))})"


def _format_relative_last_seen(seen_at: datetime | None, now: datetime, *, online: bool) -> str:
    del online
    if seen_at is None:
        return "-"
    delta_seconds = max(0, int((now - seen_at).total_seconds()))
    if delta_seconds < 60:
        return f"{delta_seconds}초 전"
    delta_minutes = delta_seconds // 60
    if delta_minutes < 60:
        return f"{delta_minutes}분 전"
    delta_hours = delta_minutes // 60
    if delta_hours < 24:
        return f"{delta_hours}시간 전"
    delta_days = delta_hours // 24
    return f"{delta_days}일 전"


def _build_summary_cards(
    *,
    selected_target: str | None,
    selected_target_label: str | None,
    router_state: str | None,
    connected_peer_count: int,
    total_peer_count: int,
    coordinator_id: str | None,
    coordinator_label: str | None,
    coordinator_name: str | None,
    selected_target_name: str | None,
    local_detected_count: int,
    local_freshness,
    diff_node_ids: tuple[str, ...],
    stale_node_ids: tuple[str, ...],
) -> tuple[SummaryCardView, ...]:
    if selected_target and router_state == "active":
        target_detail = "현재 제어 중인 대상 PC입니다."
        target_tone = "accent"
    else:
        target_detail = "아직 제어 중인 대상이 없습니다."
        target_tone = "neutral"
    return (
        SummaryCardView("현재 대상", selected_target_name or "-", target_detail, target_tone),
        SummaryCardView(
            "연결 상태",
            f"{connected_peer_count} / {total_peer_count}",
            "현재 노드 그룹에 연결된 PC 수입니다.",
            "success" if connected_peer_count else "danger",
        ),
        SummaryCardView(
            "코디네이터",
            coordinator_name or "-",
            "현재 노드 그룹에서 입력 전환과 상태 동기화를 조율하는 PC입니다.",
            "accent" if coordinator_id else "neutral",
        ),
    )


def _build_node_detail_view(
    *,
    node_id: str,
    node_label: str,
    online: bool,
    is_coordinator: bool,
    is_authorized_controller: bool,
    is_selected_target: bool,
    router_state: str | None,
    layout_node: LayoutNode | None,
    snapshot,
    last_seen: str,
    is_self: bool,
    freshness,
    diff_summary: str,
    has_monitor_diff: bool,
    current_version_label: str,
    compatibility_version_label: str,
    version_status: str,
    version_status_label: str,
    is_version_compatible: bool,
) -> NodeDetailView:
    badges = [BadgeView("내 PC", "accent")] if is_self else []
    badges.append(BadgeView("연결됨" if online else "오프라인", "success" if online else "danger"))
    if is_coordinator:
        badges.append(BadgeView("코디네이터", "neutral"))
    if online and not is_self and version_status == "outdated":
        badges.append(BadgeView("업데이트 필요", "danger"))
    elif online and not is_self and version_status == "ahead":
        badges.append(BadgeView("상대가 더 최신", "neutral"))

    if is_self:
        subtitle = "이 PC가 로컬 입력을 처리하고 상태를 발행하고 있습니다."
    elif online and version_status == "outdated":
        subtitle = "이 PC는 현재 PC보다 오래된 버전을 실행 중입니다."
    elif online and version_status == "ahead":
        subtitle = "이 PC가 현재 PC보다 더 최신 버전을 실행 중입니다."
    elif is_selected_target and router_state == "active":
        subtitle = "현재 입력이 이 PC로 전달되고 있습니다."
    elif online:
        subtitle = "이 PC는 온라인 상태이며 준비되어 있습니다."
    else:
        subtitle = "이 PC는 현재 오프라인입니다."

    fields = (
        InspectorFieldView("현재 버전", current_version_label),
        InspectorFieldView("호환 가능 버전", compatibility_version_label),
        InspectorFieldView("버전 호환", version_status_label),
        InspectorFieldView("모니터 배치", _layout_summary(layout_node)),
        InspectorFieldView("실제 감지 모니터", str(0 if snapshot is None else len(snapshot.monitors))),
        InspectorFieldView("적용된 모니터 맵", _detection_summary(layout_node, snapshot)),
        InspectorFieldView("최근 연결", last_seen),
        InspectorFieldView("최근 감지", "-" if snapshot is None else (snapshot.captured_at or "-")),
        InspectorFieldView("감지 상태", freshness.detail),
        InspectorFieldView("감지/저장 차이", diff_summary),
    )
    action_label = "레이아웃 탭에서 모니터 맵을 수정하세요"
    if online and not is_self and version_status == "outdated":
        action_label = "버전 셀을 클릭해 이 노드에 업데이트 명령을 보낼 수 있습니다"
    elif online and not is_self and version_status == "ahead":
        action_label = "이 노드가 더 최신입니다. 현재 PC를 업데이트하면 다시 버전을 맞출 수 있습니다"
    return NodeDetailView(
        node_id=node_id,
        label=node_label,
        title=f"{node_label} PC",
        subtitle=subtitle,
        badges=tuple(badges),
        fields=fields,
        action_label=action_label,
    )


def _target_subtitle(*, online: bool, selected: bool, state: str | None) -> str:
    if selected and state == "active":
        return "현재 입력이 이 PC로 전달되고 있습니다."
    if online:
        return "전환 가능"
    return "연결 대기 중"


def _target_badges(*, online: bool, selected: bool, state: str | None) -> tuple[BadgeView, ...]:
    badges = [BadgeView("연결됨" if online else "오프라인", "success" if online else "danger")]
    if selected and state == "active":
        badges.append(BadgeView("사용 중", "accent"))
    return tuple(badges)


def _layout_summary(layout_node: LayoutNode | None) -> str:
    if layout_node is None:
        return "-"
    physical_rows = monitor_topology_to_rows(layout_node.monitors(), logical=False)
    return _rows_size_text(physical_rows)


def _display_count(layout_node: LayoutNode | None, snapshot) -> int:
    if snapshot is not None:
        return len(snapshot.monitors)
    if layout_node is None or layout_node.monitor_source == "fallback":
        return 0
    return len(layout_node.monitors().physical)


def _detection_summary(layout_node: LayoutNode | None, snapshot) -> str:
    if snapshot is None:
        return "감지 대기"
    if layout_node is None:
        return "실제 감지만 존재"
    if layout_node.monitor_source == "detected_override":
        return "실제 감지 + 물리 배치 보정"
    if layout_node.monitor_source == "detected":
        return "실제 감지 기준"
    if layout_node.monitor_source == "legacy":
        return "이전 저장값 사용"
    return "감지 대기"


def _monitor_diff_summary(layout_node: LayoutNode | None, snapshot) -> tuple[str, bool]:
    if snapshot is None or not snapshot.monitors:
        return ("실제 감지 정보가 없습니다.", False)
    logical_rows = snapshot_to_logical_rows(snapshot)
    if layout_node is None:
        return ("저장된 레이아웃이 없습니다.", False)
    physical_rows = monitor_topology_to_rows(layout_node.monitors(), logical=False)
    diff = compare_detected_and_physical_rows(logical_rows, physical_rows)
    return summarize_monitor_diff(diff), diff.has_difference


def _diff_card_detail(
    diff_node_ids: tuple[str, ...],
    stale_node_ids: tuple[str, ...],
    coordinator_id: str | None,
) -> str:
    parts = [f"코디네이터 {coordinator_id or '-'}"]
    if diff_node_ids:
        parts.append("차이: " + ", ".join(diff_node_ids[:3]))
    else:
        parts.append("감지 차이 없음")
    if stale_node_ids:
        parts.append("오래된 감지: " + ", ".join(stale_node_ids[:3]))
    return " | ".join(parts)


def _build_monitor_alert(
    *,
    diff_node_ids: tuple[str, ...],
    stale_node_ids: tuple[str, ...],
) -> tuple[str | None, str]:
    return (None, "neutral")


def _rows_size_text(rows: list[list[str | None]]) -> str:
    if not rows:
        return "-"
    return f"{max(len(row) for row in rows)} x {len(rows)}"


def _node_display_label(name: str, ip: str) -> str:
    return f"{name}({ip})"
