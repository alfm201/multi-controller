"""User-facing status view models and text helpers."""

from dataclasses import dataclass

from runtime.layouts import LayoutNode, monitor_topology_to_rows


@dataclass(frozen=True)
class TargetView:
    node_id: str
    online: bool
    selected: bool
    state: str | None


@dataclass(frozen=True)
class PeerView:
    node_id: str
    roles: tuple[str, ...]
    online: bool
    is_coordinator: bool
    is_authorized_controller: bool


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


def build_status_view(ctx, registry, coordinator_resolver, router=None, sink=None):
    coordinator = coordinator_resolver()
    coordinator_id = None if coordinator is None else coordinator.node_id
    online_peers = tuple(
        sorted(node_id for node_id, conn in registry.all() if conn and not conn.closed)
    )
    router_state = None if router is None else router.get_target_state()
    selected_target = None if router is None else router.get_selected_target()
    authorized_controller = None if sink is None else sink.get_authorized_controller()
    peers = tuple(
        PeerView(
            node_id=node.node_id,
            roles=tuple(node.roles),
            online=node.node_id in online_peers,
            is_coordinator=node.node_id == coordinator_id,
            is_authorized_controller=node.node_id == authorized_controller,
        )
        for node in ctx.peers
    )
    targets = tuple(
        TargetView(
            node_id=node.node_id,
            online=node.node_id in online_peers,
            selected=node.node_id == selected_target,
            state=router_state if node.node_id == selected_target else None,
        )
        for node in ctx.peers
        if node.has_role("target")
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
        peers=peers,
        targets=targets,
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
        detail_text = "내 변경이 바로 반영됩니다"
    elif editor_id:
        detail_text = f"{editor_id} PC가 편집 중입니다"
    else:
        detail_text = "변경사항은 바로 반영됩니다"
    return " | ".join((mode_text, auto_text, detail_text))


def build_layout_lock_text(editor_id: str | None, self_id: str, pending: bool = False) -> str:
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


def build_viewport_summary(zoom: float, pan_x: float, pan_y: float) -> str:
    return f"보기: {int(round(zoom * 100))}% | pan ({int(round(pan_x))}, {int(round(pan_y))})"
