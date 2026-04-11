"""User-facing status view models and text helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from runtime.layouts import LayoutNode, monitor_topology_to_rows
from runtime.monitor_inventory import (
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
    title: str
    subtitle: str
    badges: tuple[BadgeView, ...]
    fields: tuple[InspectorFieldView, ...]
    action_label: str


@dataclass(frozen=True)
class TargetView:
    node_id: str
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
    online: bool
    is_coordinator: bool
    is_authorized_controller: bool
    layout_summary: str
    display_count: int
    badges: tuple[BadgeView, ...]
    last_seen: str
    detection_summary: str
    freshness_label: str
    freshness_tone: str
    diff_summary: str
    has_monitor_diff: bool


@dataclass(frozen=True)
class StatusView:
    self_id: str
    coordinator_id: str | None
    online_peers: tuple[str, ...]
    connected_peer_count: int
    total_peer_count: int
    router_state: str | None
    selected_target: str | None
    authorized_controller: str | None
    config_path: str | None
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
    last_seen: dict[str, str] | None = None,
):
    coordinator = coordinator_resolver()
    coordinator_id = None if coordinator is None else coordinator.node_id
    online_peers = tuple(
        sorted(node_id for node_id, conn in registry.all() if conn and not conn.closed)
    )
    raw_router_state = None if router is None else router.get_target_state()
    raw_selected_target = None if router is None else router.get_selected_target()
    selected_target_online = (
        raw_selected_target is not None
        and (
            raw_selected_target == ctx.self_node.node_id
            or raw_selected_target in online_peers
        )
    )
    router_state = raw_router_state if selected_target_online else None
    selected_target = raw_selected_target if selected_target_online else None
    authorized_controller = None if sink is None else sink.get_authorized_controller()
    layout = ctx.layout
    last_seen = {} if last_seen is None else dict(last_seen)
    now = datetime.now()

    node_details = []
    peers = []
    targets = []
    diff_node_ids = []
    stale_node_ids = []

    for node in ctx.peers:
        layout_node = None if layout is None else layout.get_node(node.node_id)
        snapshot = ctx.get_monitor_inventory(node.node_id)
        online = node.node_id in online_peers
        freshness = describe_monitor_freshness(snapshot, online=online, now=now)
        diff_summary, has_monitor_diff = _monitor_diff_summary(layout_node, snapshot)
        if freshness.is_stale:
            stale_node_ids.append(node.node_id)
        if has_monitor_diff:
            diff_node_ids.append(node.node_id)

        detail = _build_node_detail_view(
            node_id=node.node_id,
            online=online,
            is_coordinator=node.node_id == coordinator_id,
            is_authorized_controller=node.node_id == authorized_controller,
            is_selected_target=node.node_id == selected_target,
            router_state=router_state,
            layout_node=layout_node,
            snapshot=snapshot,
            last_seen=last_seen.get(node.node_id, "-"),
            is_self=False,
            freshness=freshness,
            diff_summary=diff_summary,
            has_monitor_diff=has_monitor_diff,
        )
        node_details.append(detail)
        peers.append(
            PeerView(
                node_id=node.node_id,
                online=online,
                is_coordinator=node.node_id == coordinator_id,
                is_authorized_controller=node.node_id == authorized_controller,
                layout_summary=_layout_summary(layout_node),
                display_count=_display_count(layout_node, snapshot),
                badges=detail.badges,
                last_seen=last_seen.get(node.node_id, "-"),
                detection_summary=_detection_summary(layout_node, snapshot),
                freshness_label=freshness.label,
                freshness_tone=freshness.tone,
                diff_summary=diff_summary,
                has_monitor_diff=has_monitor_diff,
            )
        )
        if node.has_role("target"):
            targets.append(
                TargetView(
                    node_id=node.node_id,
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
    self_freshness = describe_monitor_freshness(self_snapshot, online=True, now=now)
    self_diff_summary, self_has_monitor_diff = _monitor_diff_summary(
        self_layout_node,
        self_snapshot,
    )
    if self_freshness.is_stale:
        stale_node_ids.insert(0, ctx.self_node.node_id)
    if self_has_monitor_diff:
        diff_node_ids.insert(0, ctx.self_node.node_id)

    self_detail = _build_node_detail_view(
        node_id=ctx.self_node.node_id,
        online=True,
        is_coordinator=ctx.self_node.node_id == coordinator_id,
        is_authorized_controller=ctx.self_node.node_id == authorized_controller,
        is_selected_target=selected_target is None,
        router_state=router_state,
        layout_node=self_layout_node,
        snapshot=self_snapshot,
        last_seen=last_seen.get(ctx.self_node.node_id, "-"),
        is_self=True,
        freshness=self_freshness,
        diff_summary=self_diff_summary,
        has_monitor_diff=self_has_monitor_diff,
    )
    node_details.insert(0, self_detail)
    detail_by_id = {detail.node_id: detail for detail in node_details}
    selected_detail = detail_by_id.get(selected_target or ctx.self_node.node_id, self_detail)

    summary_cards = _build_summary_cards(
        selected_target=selected_target,
        router_state=router_state,
        connected_peer_count=len(online_peers),
        total_peer_count=len(ctx.peers),
        coordinator_id=coordinator_id,
        local_detected_count=0 if self_snapshot is None else len(self_snapshot.monitors),
        local_freshness=self_freshness,
        diff_node_ids=tuple(diff_node_ids),
        stale_node_ids=tuple(stale_node_ids),
    )
    monitor_alert, monitor_alert_tone = _build_monitor_alert(
        diff_node_ids=tuple(diff_node_ids),
        stale_node_ids=tuple(stale_node_ids),
    )

    return StatusView(
        self_id=ctx.self_node.node_id,
        coordinator_id=coordinator_id,
        online_peers=online_peers,
        connected_peer_count=len(online_peers),
        total_peer_count=len(ctx.peers),
        router_state=router_state,
        selected_target=selected_target,
        authorized_controller=authorized_controller,
        config_path=None if ctx.config_path is None else str(ctx.config_path),
        peers=tuple(peers),
        targets=tuple(targets),
        summary_cards=summary_cards,
        node_details=tuple(node_details),
        selected_detail=selected_detail,
        monitor_alert=monitor_alert,
        monitor_alert_tone=monitor_alert_tone,
    )


def build_primary_status_text(view: StatusView) -> str:
    if view.selected_target and view.router_state == "active":
        return f"{view.selected_target} PC가 현재 제어 대상입니다."
    if view.selected_target and view.router_state == "pending":
        return f"{view.selected_target} PC로 전환 중입니다."
    if view.selected_target:
        return f"{view.selected_target} PC가 선택되어 있습니다."
    if view.total_peer_count == 0:
        return "설정된 다른 PC가 없습니다."
    if view.connected_peer_count == 0:
        return "다른 PC 연결을 기다리는 중입니다."
    return "PC를 선택해 입력 공유를 시작하세요."


def build_connection_summary_text(view: StatusView) -> str:
    return f"연결된 PC {view.connected_peer_count} / {view.total_peer_count}"


def build_selection_hint_text(view: StatusView) -> str:
    if view.selected_target and view.router_state == "active":
        return "입력이 선택된 PC로 전달되고 있습니다."
    if view.selected_target and view.router_state == "pending":
        return "선택한 PC가 입력 제어권을 넘겨주기를 기다리는 중입니다."
    if view.selected_target:
        return "대상이 선택되었고 전환 준비가 진행 중입니다."
    if view.connected_peer_count == 0:
        return "다른 PC가 실행 중인지, 연결 가능한지 확인해 주세요."
    return "요약 카드나 레이아웃 캔버스에서 PC를 선택해 주세요."


def build_peer_summary_text(peer: PeerView) -> str:
    parts = [peer.node_id, "연결됨" if peer.online else "오프라인"]
    if peer.is_authorized_controller:
        parts.append("제어권 보유")
    return " | ".join(parts)


def build_target_button_text(target: TargetView) -> str:
    status = "연결됨" if target.online else "오프라인"
    if target.selected and target.state == "active":
        detail = "사용 중"
    elif target.selected and target.state == "pending":
        detail = "전환 중"
    elif target.selected:
        detail = "선택됨"
    else:
        detail = "준비됨" if target.online else "대기 중"
    return f"{target.node_id} | {status} | {detail}"


def build_advanced_peer_text(peer: PeerView) -> str:
    detection_summary = getattr(peer, "detection_summary", None)
    if detection_summary is None:
        roles = getattr(peer, "roles", ())
        detection_summary = "/".join(roles) or "모니터 기준 정보 없음"
    parts = [peer.node_id, "연결됨" if peer.online else "연결 끊김", detection_summary]
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
    node_id: str,
    *,
    is_self: bool,
    is_online: bool,
    is_selected: bool,
    state: str | None,
) -> str:
    lines = [node_id]
    if is_self:
        lines.append("내 PC")
    elif is_selected and state == "active":
        lines.append("사용 중")
    elif is_selected and state == "pending":
        lines.append("전환 중")
    elif is_selected:
        lines.append("선택됨")
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
    if is_selected and state == "pending":
        return ("#fde7c7", "#9a6700")
    if is_selected:
        return ("#ede9fe", "#5b41b2")
    return ("#f4f4f5", "#4b5563")


def build_selected_node_text(node: LayoutNode | None) -> str:
    if node is None:
        return "선택된 PC: -"
    if node.monitor_source == "fallback":
        return f"선택된 PC: {node.node_id} | 모니터 감지 대기"
    logical = monitor_topology_to_rows(node.monitors(), logical=True)
    physical = monitor_topology_to_rows(node.monitors(), logical=False)
    return (
        f"선택된 PC: {node.node_id} | "
        f"물리 {_rows_size_text(physical)} | "
        f"논리 {_rows_size_text(logical)} | "
        f"모니터 {len(node.monitors().physical)}개"
    )


def build_layout_inspector_detail(
    node: LayoutNode | None,
    *,
    node_id: str | None,
    is_self: bool,
    is_online: bool,
    state: str | None,
    can_edit: bool,
) -> NodeDetailView:
    if node is None or node_id is None:
        return NodeDetailView(
            node_id="-",
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
    elif state == "pending":
        badges.append(BadgeView("전환 중", "warning"))
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
        title=f"{node_id} PC",
        subtitle=subtitle,
        badges=tuple(badges),
        fields=fields,
        action_label="선택한 PC의 모니터 맵을 수정하세요",
    )


def build_viewport_summary(zoom: float, pan_x: float, pan_y: float) -> str:
    return f"보기: {int(round(zoom * 100))}% | 이동 ({int(round(pan_x))}, {int(round(pan_y))})"


def _build_summary_cards(
    *,
    selected_target: str | None,
    router_state: str | None,
    connected_peer_count: int,
    total_peer_count: int,
    coordinator_id: str | None,
    local_detected_count: int,
    local_freshness,
    diff_node_ids: tuple[str, ...],
    stale_node_ids: tuple[str, ...],
) -> tuple[SummaryCardView, ...]:
    if selected_target and router_state == "active":
        target_detail = "현재 입력이 이 PC로 전달되고 있습니다."
        target_tone = "accent"
    elif selected_target and router_state == "pending":
        target_detail = "제어권 전환을 기다리는 중입니다."
        target_tone = "warning"
    elif selected_target:
        target_detail = "선택되었고 준비가 진행 중입니다."
        target_tone = "neutral"
    else:
        target_detail = "아직 선택된 대상이 없습니다."
        target_tone = "neutral"
    return (
        SummaryCardView("현재 대상", selected_target or "-", target_detail, target_tone),
        SummaryCardView(
            "연결 상태",
            f"{connected_peer_count} / {total_peer_count}",
            "제어 네트워크에 연결된 PC 수입니다.",
            "success" if connected_peer_count else "danger",
        ),
        SummaryCardView(
            "모니터 감지",
            local_freshness.label,
            f"로컬 모니터 {local_detected_count or 0}개 | {local_freshness.detail}",
            local_freshness.tone,
        ),
        SummaryCardView(
            "모니터 차이",
            "없음" if not diff_node_ids else f"{len(diff_node_ids)}개",
            _diff_card_detail(diff_node_ids, stale_node_ids, coordinator_id),
            "success" if not diff_node_ids and not stale_node_ids else "warning",
        ),
    )


def _build_node_detail_view(
    *,
    node_id: str,
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
) -> NodeDetailView:
    badges = [BadgeView("내 PC", "accent")] if is_self else []
    badges.append(BadgeView("연결됨" if online else "오프라인", "success" if online else "danger"))
    if is_selected_target and router_state == "active":
        badges.append(BadgeView("현재 대상", "accent"))
    elif is_selected_target and router_state == "pending":
        badges.append(BadgeView("전환 중", "warning"))
    elif is_selected_target:
        badges.append(BadgeView("선택됨", "neutral"))
    if is_coordinator:
        badges.append(BadgeView("코디네이터", "neutral"))
    if is_authorized_controller:
        badges.append(BadgeView("제어권 보유", "warning"))
    if layout_node is not None and layout_node.monitor_source.startswith("detected"):
        badges.append(BadgeView("실제 감지 기준", "success"))
    elif snapshot is None:
        badges.append(BadgeView("감지 정보 없음", "warning"))
    badges.append(BadgeView(f"감지 {freshness.label}", freshness.tone))
    if has_monitor_diff:
        badges.append(BadgeView("물리 보정 차이", "warning"))

    if is_self:
        subtitle = "이 PC가 로컬 입력을 처리하고 상태를 발행하고 있습니다."
    elif is_selected_target and router_state == "active":
        subtitle = "현재 입력이 이 PC로 전달되고 있습니다."
    elif is_selected_target and router_state == "pending":
        subtitle = "이 PC가 선택되었고 활성화를 기다리는 중입니다."
    elif online:
        subtitle = "이 PC는 온라인 상태이며 준비되어 있습니다."
    else:
        subtitle = "이 PC는 현재 오프라인입니다."

    fields = (
        InspectorFieldView("레이아웃", _layout_summary(layout_node)),
        InspectorFieldView("실제 감지 모니터", str(0 if snapshot is None else len(snapshot.monitors))),
        InspectorFieldView("적용된 모니터 맵", _detection_summary(layout_node, snapshot)),
        InspectorFieldView("최근 확인", last_seen),
        InspectorFieldView("최근 감지", "-" if snapshot is None else (snapshot.captured_at or "-")),
        InspectorFieldView("감지 상태", freshness.detail),
        InspectorFieldView("감지/저장 차이", diff_summary),
    )
    return NodeDetailView(
        node_id=node_id,
        title=f"{node_id} PC",
        subtitle=subtitle,
        badges=tuple(badges),
        fields=fields,
        action_label="레이아웃 탭에서 모니터 맵을 수정하세요",
    )


def _target_subtitle(*, online: bool, selected: bool, state: str | None) -> str:
    if selected and state == "active":
        return "현재 입력이 이 PC로 전달되고 있습니다."
    if selected and state == "pending":
        return "활성화를 기다리는 중입니다."
    if selected:
        return "선택됨"
    if online:
        return "전환 가능"
    return "연결 대기 중"


def _target_badges(*, online: bool, selected: bool, state: str | None) -> tuple[BadgeView, ...]:
    badges = [BadgeView("연결됨" if online else "오프라인", "success" if online else "danger")]
    if selected and state == "active":
        badges.append(BadgeView("사용 중", "accent"))
    elif selected and state == "pending":
        badges.append(BadgeView("전환 중", "warning"))
    elif selected:
        badges.append(BadgeView("선택됨", "neutral"))
    return tuple(badges)


def _layout_summary(layout_node: LayoutNode | None) -> str:
    if layout_node is None:
        return "-"
    return f"{layout_node.width} x {layout_node.height}"


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
    if not diff_node_ids and not stale_node_ids:
        return (None, "neutral")
    parts = []
    if diff_node_ids:
        parts.append("배치 차이: " + ", ".join(diff_node_ids[:4]))
    if stale_node_ids:
        parts.append("오래된 감지: " + ", ".join(stale_node_ids[:4]))
    return (" | ".join(parts), "warning")


def _rows_size_text(rows: list[list[str | None]]) -> str:
    if not rows:
        return "-"
    return f"{max(len(row) for row in rows)} x {len(rows)}"
