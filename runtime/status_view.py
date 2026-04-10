"""User-facing status view models and text helpers."""

from dataclasses import dataclass

from runtime.layouts import LayoutNode, monitor_topology_to_rows


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
    roles: tuple[str, ...]
    online: bool
    is_coordinator: bool
    is_authorized_controller: bool
    role_summary: str
    layout_summary: str
    display_count: int
    badges: tuple[BadgeView, ...]
    last_seen: str


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
    router_state = None if router is None else router.get_target_state()
    selected_target = None if router is None else router.get_selected_target()
    authorized_controller = None if sink is None else sink.get_authorized_controller()
    layout = ctx.layout
    last_seen = {} if last_seen is None else dict(last_seen)

    node_details = []
    peers = []
    targets = []
    for node in ctx.peers:
        layout_node = None if layout is None else layout.get_node(node.node_id)
        online = node.node_id in online_peers
        detail = _build_node_detail_view(
            node_id=node.node_id,
            roles=tuple(node.roles),
            online=online,
            is_coordinator=node.node_id == coordinator_id,
            is_authorized_controller=node.node_id == authorized_controller,
            is_selected_target=node.node_id == selected_target,
            router_state=router_state,
            layout_node=layout_node,
            last_seen=last_seen.get(node.node_id, "-"),
            is_self=False,
        )
        node_details.append(detail)
        peers.append(
            PeerView(
                node_id=node.node_id,
                roles=tuple(node.roles),
                online=online,
                is_coordinator=node.node_id == coordinator_id,
                is_authorized_controller=node.node_id == authorized_controller,
                role_summary=", ".join(node.roles) or "-",
                layout_summary=_layout_summary(layout_node),
                display_count=_display_count(layout_node),
                badges=detail.badges,
                last_seen=last_seen.get(node.node_id, "-"),
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
                    display_count=_display_count(layout_node),
                )
            )

    self_layout_node = None if layout is None else layout.get_node(ctx.self_node.node_id)
    self_detail = _build_node_detail_view(
        node_id=ctx.self_node.node_id,
        roles=tuple(ctx.self_node.roles),
        online=True,
        is_coordinator=ctx.self_node.node_id == coordinator_id,
        is_authorized_controller=ctx.self_node.node_id == authorized_controller,
        is_selected_target=selected_target is None,
        router_state=router_state,
        layout_node=self_layout_node,
        last_seen=last_seen.get(ctx.self_node.node_id, "-"),
        is_self=True,
    )
    node_details.insert(0, self_detail)

    detail_by_id = {detail.node_id: detail for detail in node_details}
    selected_detail = detail_by_id.get(selected_target or ctx.self_node.node_id, self_detail)
    summary_cards = _build_summary_cards(
        selected_target=selected_target,
        router_state=router_state,
        connected_peer_count=len(online_peers),
        total_peer_count=len(ctx.peers),
        auto_switch_enabled=False if layout is None else layout.auto_switch.enabled,
        authorized_controller=authorized_controller,
        coordinator_id=coordinator_id,
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
    )


def build_primary_status_text(view: StatusView) -> str:
    if view.selected_target and view.router_state == "active":
        return f"{view.selected_target} PC를 제어 중입니다."
    if view.selected_target and view.router_state == "pending":
        return f"{view.selected_target} PC로 연결 중입니다."
    if view.selected_target:
        return f"{view.selected_target} PC를 선택했습니다."
    if view.total_peer_count == 0:
        return "설정된 다른 PC가 없습니다."
    if view.connected_peer_count == 0:
        return "연결된 PC를 찾는 중입니다."
    return "제어할 PC를 선택해 주세요."


def build_connection_summary_text(view: StatusView) -> str:
    return f"연결된 PC {view.connected_peer_count} / {view.total_peer_count}"


def build_selection_hint_text(view: StatusView) -> str:
    if view.selected_target and view.router_state == "active":
        return "마우스와 키보드 입력은 현재 선택된 PC로 전달됩니다."
    if view.selected_target and view.router_state == "pending":
        return "응답을 기다리는 중입니다. 잠시 뒤 자동으로 이어집니다."
    if view.selected_target:
        return "선택은 되었지만 아직 제어가 시작되지는 않았습니다."
    if view.connected_peer_count == 0:
        return "네트워크와 대상 PC 실행 상태를 확인해 주세요."
    return "요약 탭의 버튼이나 레이아웃 탭에서 PC를 선택할 수 있습니다."


def build_target_button_text(target: TargetView) -> str:
    status = "연결됨" if target.online else "오프라인"
    if target.selected and target.state == "active":
        detail = "현재 제어 중"
    elif target.selected and target.state == "pending":
        detail = "연결 중"
    elif target.selected:
        detail = "선택됨"
    else:
        detail = "전환 가능" if target.online else "연결 대기"
    return f"{target.node_id} | {status} | {detail}"


def build_peer_summary_text(peer: PeerView) -> str:
    parts = [peer.node_id, "연결됨" if peer.online else "오프라인"]
    if peer.is_authorized_controller:
        parts.append("현재 제어 권한 보유")
    return " | ".join(parts)


def build_advanced_peer_text(peer: PeerView) -> str:
    parts = [
        peer.node_id,
        "/".join(peer.roles),
        "connected" if peer.online else "disconnected",
    ]
    if peer.is_coordinator:
        parts.append("coordinator")
    if peer.is_authorized_controller:
        parts.append("lease-holder")
    return " | ".join(parts)


def build_layout_editor_hint(
    editing_enabled: bool,
    auto_switch_enabled: bool,
    editor_id: str | None,
    self_id: str,
    pending: bool = False,
) -> str:
    if pending and editor_id != self_id:
        mode_text = "편집 모드: 요청 중"
    elif editing_enabled and editor_id == self_id:
        mode_text = "편집 모드: 켜짐"
    elif editor_id and editor_id != self_id:
        mode_text = f"편집 모드: 잠김 ({editor_id})"
    else:
        mode_text = "편집 모드: 꺼짐"
    auto_text = "자동 전환: 켜짐" if auto_switch_enabled else "자동 전환: 꺼짐"
    if editor_id == self_id:
        detail_text = "빈 공간을 드래그해 화면 이동"
    elif editor_id:
        detail_text = f"{editor_id} PC가 편집 중입니다"
    else:
        detail_text = "선택한 PC의 모니터 맵 편집"
    return " | ".join((mode_text, auto_text, detail_text))


def build_layout_lock_text(
    editor_id: str | None, self_id: str, pending: bool = False
) -> str:
    if pending and editor_id != self_id:
        return "편집 잠금: 요청 중"
    if editor_id == self_id:
        return "편집 잠금: 내 세션"
    if editor_id:
        return f"편집 잠금: {editor_id} 사용 중"
    return "편집 잠금: 없음"


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
        lines.append("제어 중")
    elif is_selected and state == "pending":
        lines.append("연결 중")
    elif is_selected:
        lines.append("선택됨")
    elif is_online:
        lines.append("연결됨")
    else:
        lines.append("오프라인")
    return "\n".join(lines)


def build_layout_node_colors(
    *, is_self: bool, is_online: bool, is_selected: bool, state: str | None
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
    logical = monitor_topology_to_rows(node.monitors(), logical=True)
    physical = monitor_topology_to_rows(node.monitors(), logical=False)
    return (
        f"선택된 PC: {node.node_id} | "
        f"물리 {max(len(row) for row in physical)}x{len(physical)} | "
        f"논리 {max(len(row) for row in logical)}x{len(logical)} | "
        f"display {len(node.monitors().physical)}개"
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
            subtitle="레이아웃에서 PC를 선택해 주세요.",
            badges=(BadgeView("대기", "neutral"),),
            fields=(
                InspectorFieldView("물리 크기", "-"),
                InspectorFieldView("논리 크기", "-"),
                InspectorFieldView("디스플레이", "-"),
                InspectorFieldView("편집", "편집 모드 필요"),
            ),
            action_label="선택한 PC의 모니터 맵 편집",
        )

    logical = monitor_topology_to_rows(node.monitors(), logical=True)
    physical = monitor_topology_to_rows(node.monitors(), logical=False)
    badges = []
    if is_self:
        badges.append(BadgeView("내 PC", "accent"))
    badges.append(BadgeView("연결됨" if is_online else "오프라인", "success" if is_online else "danger"))
    if state == "active":
        badges.append(BadgeView("현재 제어 중", "accent"))
    elif state == "pending":
        badges.append(BadgeView("연결 중", "warning"))
    fields = (
        InspectorFieldView("물리 크기", f"{max(len(row) for row in physical)} x {len(physical)}"),
        InspectorFieldView("논리 크기", f"{max(len(row) for row in logical)} x {len(logical)}"),
        InspectorFieldView("디스플레이", str(len(node.monitors().physical))),
        InspectorFieldView("편집", "가능" if can_edit else "읽기 전용"),
    )
    subtitle = (
        "선택한 PC의 모니터 맵과 크기를 확인하세요."
        if can_edit
        else "편집 모드에서 선택한 PC를 조정할 수 있습니다."
    )
    return NodeDetailView(
        node_id=node_id,
        title=f"{node_id} PC",
        subtitle=subtitle,
        badges=tuple(badges),
        fields=fields,
        action_label="선택한 PC의 모니터 맵 편집",
    )


def build_viewport_summary(zoom: float, pan_x: float, pan_y: float) -> str:
    return f"보기: {int(round(zoom * 100))}% | pan ({int(round(pan_x))}, {int(round(pan_y))})"


def _build_summary_cards(
    *,
    selected_target: str | None,
    router_state: str | None,
    connected_peer_count: int,
    total_peer_count: int,
    auto_switch_enabled: bool,
    authorized_controller: str | None,
    coordinator_id: str | None,
) -> tuple[SummaryCardView, ...]:
    if selected_target and router_state == "active":
        target_detail = "현재 입력이 전달되는 대상입니다."
        target_tone = "accent"
    elif selected_target and router_state == "pending":
        target_detail = "응답을 기다리는 중입니다."
        target_tone = "warning"
    elif selected_target:
        target_detail = "전환 직전 상태입니다."
        target_tone = "neutral"
    else:
        target_detail = "아직 선택된 대상이 없습니다."
        target_tone = "neutral"

    connection_tone = "success" if connected_peer_count else "danger"
    auto_switch_text = "켜짐" if auto_switch_enabled else "꺼짐"
    auto_switch_detail = "경계 이동에 따라 대상 전환" if auto_switch_enabled else "직접 전환만 사용"
    return (
        SummaryCardView("현재 타깃", selected_target or "-", target_detail, target_tone),
        SummaryCardView(
            "연결 상태",
            f"{connected_peer_count} / {total_peer_count}",
            "온라인 피어 수",
            connection_tone,
        ),
        SummaryCardView("자동 전환", auto_switch_text, auto_switch_detail, "neutral"),
        SummaryCardView(
            "제어 권한",
            authorized_controller or "-",
            f"coordinator: {coordinator_id or '-'}",
            "neutral",
        ),
    )


def _build_node_detail_view(
    *,
    node_id: str,
    roles: tuple[str, ...],
    online: bool,
    is_coordinator: bool,
    is_authorized_controller: bool,
    is_selected_target: bool,
    router_state: str | None,
    layout_node: LayoutNode | None,
    last_seen: str,
    is_self: bool,
) -> NodeDetailView:
    badges = []
    if is_self:
        badges.append(BadgeView("내 PC", "accent"))
    badges.append(BadgeView("연결됨" if online else "오프라인", "success" if online else "danger"))
    if is_selected_target and router_state == "active":
        badges.append(BadgeView("현재 제어 중", "accent"))
    elif is_selected_target and router_state == "pending":
        badges.append(BadgeView("연결 중", "warning"))
    elif is_selected_target:
        badges.append(BadgeView("선택됨", "neutral"))
    if is_coordinator:
        badges.append(BadgeView("coordinator", "neutral"))
    if is_authorized_controller:
        badges.append(BadgeView("권한 보유", "warning"))

    if is_self:
        subtitle = "현재 로컬 입력을 보내는 기본 노드입니다."
    elif is_selected_target and router_state == "active":
        subtitle = "현재 입력이 이 PC로 전달되고 있습니다."
    elif is_selected_target and router_state == "pending":
        subtitle = "응답을 기다리는 중입니다."
    elif online:
        subtitle = "전환 가능한 상태입니다."
    else:
        subtitle = "연결이 복구되면 전환할 수 있습니다."

    fields = (
        InspectorFieldView("역할", ", ".join(roles) or "-"),
        InspectorFieldView("레이아웃", _layout_summary(layout_node)),
        InspectorFieldView("디스플레이", str(_display_count(layout_node))),
        InspectorFieldView("최근 갱신", last_seen),
        InspectorFieldView("편집 가능", "예" if "target" in roles else "아니오"),
    )
    action_label = (
        "레이아웃 탭에서 모니터 맵 편집"
        if "target" in roles
        else "target 역할이 아닙니다"
    )
    return NodeDetailView(
        node_id=node_id,
        title=f"{node_id} PC",
        subtitle=subtitle,
        badges=tuple(badges),
        fields=fields,
        action_label=action_label,
    )


def _target_subtitle(*, online: bool, selected: bool, state: str | None) -> str:
    if selected and state == "active":
        return "현재 입력 전달 중"
    if selected and state == "pending":
        return "응답 대기 중"
    if selected:
        return "선택됨"
    if online:
        return "즉시 전환 가능"
    return "연결 대기"


def _target_badges(
    *,
    online: bool,
    selected: bool,
    state: str | None,
) -> tuple[BadgeView, ...]:
    badges = [BadgeView("연결됨" if online else "오프라인", "success" if online else "danger")]
    if selected and state == "active":
        badges.append(BadgeView("현재 제어 중", "accent"))
    elif selected and state == "pending":
        badges.append(BadgeView("연결 중", "warning"))
    elif selected:
        badges.append(BadgeView("선택됨", "neutral"))
    return tuple(badges)


def _layout_summary(layout_node: LayoutNode | None) -> str:
    if layout_node is None:
        return "-"
    return f"{layout_node.width} x {layout_node.height}"


def _display_count(layout_node: LayoutNode | None) -> int:
    if layout_node is None:
        return 0
    return len(layout_node.monitors().physical)
