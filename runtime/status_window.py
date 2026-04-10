"""User-facing status window and shared 2D layout editor."""

from dataclasses import dataclass

from runtime.layouts import (
    LayoutConfig,
    layout_bounds,
    monitor_topology_to_rows,
    replace_auto_switch_settings,
    replace_layout_monitors,
    replace_layout_node,
)


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
    online_peers = tuple(sorted(node_id for node_id, conn in registry.all() if conn and not conn.closed))
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


def build_primary_status_text(view):
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


def build_connection_summary_text(view):
    return f"연결된 PC {view.connected_peer_count} / {view.total_peer_count}"


def build_selection_hint_text(view):
    if view.selected_target and view.router_state == "active":
        return "마우스와 키보드 입력은 현재 선택된 PC로 전달됩니다."
    if view.selected_target and view.router_state == "pending":
        return "응답을 기다리는 중입니다. 잠시 뒤 자동으로 이어집니다."
    if view.selected_target:
        return "선택은 되었지만 아직 제어가 시작되지는 않았습니다."
    if view.connected_peer_count == 0:
        return "네트워크와 대상 PC 실행 상태를 확인해 주세요."
    return "레이아웃에서 PC를 클릭하면 바로 전환할 수 있습니다."


def build_target_button_text(target):
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


def build_peer_summary_text(peer):
    parts = [peer.node_id, "연결됨" if peer.online else "오프라인"]
    if peer.is_authorized_controller:
        parts.append("현재 제어 권한 보유")
    return " | ".join(parts)


def build_advanced_peer_text(peer):
    parts = [peer.node_id, "/".join(peer.roles), "connected" if peer.online else "disconnected"]
    if peer.is_coordinator:
        parts.append("coordinator")
    if peer.is_authorized_controller:
        parts.append("lease-holder")
    return " | ".join(parts)


def build_layout_editor_hint(editing_enabled: bool, auto_switch_enabled: bool, editor_id: str | None, self_id: str, pending: bool = False) -> str:
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


def build_layout_node_label(node_id: str, *, is_self: bool, is_online: bool, is_selected: bool, state: str | None) -> str:
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


def build_layout_node_colors(*, is_self: bool, is_online: bool, is_selected: bool, state: str | None) -> tuple[str, str]:
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


def format_monitor_grid_text(rows: list[list[str | None]]) -> str:
    return "\n".join(" ".join(cell if cell is not None else "." for cell in row) for row in rows)


def parse_monitor_grid_text(text: str) -> list[list[str | None]]:
    rows = []
    for raw_line in text.splitlines():
        tokens = [token.strip() for token in raw_line.replace(",", " ").split()]
        if tokens:
            rows.append([None if token in {".", "-"} else token for token in tokens])
    if not rows:
        raise ValueError("모니터 맵은 한 줄 이상 필요합니다.")
    return rows


def parse_auto_switch_form(values: dict[str, str]) -> dict:
    parsed = {}
    specs = {
        "edge_threshold": ("number", 0.0, 0.25),
        "warp_margin": ("number", 0.0, 0.25),
        "cooldown_ms": ("integer", 0, None),
        "return_guard_ms": ("integer", 0, None),
        "anchor_dead_zone": ("number", 0.0, 0.5),
    }
    for key, (kind, minimum, maximum) in specs.items():
        raw = values.get(key, "").strip()
        if not raw:
            raise ValueError(f"{key} 값을 입력해 주세요.")
        try:
            value = int(raw) if kind == "integer" else float(raw)
        except ValueError as exc:
            raise ValueError(f"{key} 값 형식이 올바르지 않습니다.") from exc
        if value < minimum or (maximum is not None and value > maximum):
            if maximum is None:
                raise ValueError(f"{key} 값은 {minimum} 이상이어야 합니다.")
            raise ValueError(f"{key} 값은 {minimum} ~ {maximum} 범위여야 합니다.")
        parsed[key] = value
    return parsed


class StatusWindow:
    GRID_PITCH_X = 140
    GRID_PITCH_Y = 110
    TILE_MARGIN_X = 12
    TILE_MARGIN_Y = 14
    DEFAULT_CANVAS_WIDTH = 700
    DEFAULT_CANVAS_HEIGHT = 320
    CANVAS_PADDING = 24

    def __init__(self, ctx, registry, coordinator_resolver, router=None, sink=None, coord_client=None, config_reloader=None, refresh_ms=500):
        self.ctx = ctx
        self.registry = registry
        self.coordinator_resolver = coordinator_resolver
        self.router = router
        self.sink = sink
        self.coord_client = coord_client
        self.config_reloader = config_reloader
        self.refresh_ms = refresh_ms
        self._root = None
        self._vars = {}
        self._peer_frame = None
        self._advanced_frame = None
        self._advanced_peer_var = None
        self._peer_labels = {}
        self._layout_canvas = None
        self._layout_edit_toggle = None
        self._auto_switch_toggle = None
        self._auto_switch_settings_button = None
        self._monitor_editor_button = None
        self._draft_layout = ctx.layout
        self._layout_item_to_node_id = {}
        self._layout_geometry = None
        self._drag_node_id = None
        self._drag_origin_canvas = None
        self._drag_origin_grid = None
        self._selected_layout_node_id = None
        self._advanced_visible = False
        self._monitor_editor = None
        self._auto_switch_editor = None
        self._on_close = None

    def run(self, on_close):
        import tkinter as tk
        from tkinter import ttk

        self._on_close = on_close
        self._root = tk.Tk()
        self._root.title(f"multi-controller [{self.ctx.self_node.node_id}]")
        self._root.geometry("900x810")
        self._root.protocol("WM_DELETE_WINDOW", self._handle_close)
        frame = ttk.Frame(self._root, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        for name in ("headline", "summary", "hint", "layout_hint", "selected_node", "self_id", "coordinator", "router", "lease", "config_path", "message"):
            self._vars[name] = tk.StringVar()
        self._vars["advanced_toggle"] = tk.StringVar(value="고급 정보 보기")
        self._advanced_peer_var = tk.StringVar()
        self._vars["layout_edit"] = tk.BooleanVar(value=False)
        self._vars["auto_switch_enabled"] = tk.BooleanVar(value=True if self._draft_layout is None else self._draft_layout.auto_switch.enabled)

        ttk.Label(frame, text=f"내 PC: {self.ctx.self_node.node_id}").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, textvariable=self._vars["headline"]).grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Label(frame, textvariable=self._vars["summary"]).grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Label(frame, textvariable=self._vars["hint"], foreground="#555555").grid(row=3, column=0, sticky="w", pady=(4, 0))
        self._build_layout_group(frame, ttk, tk)
        self._build_peer_group(frame, ttk)
        self._build_button_row(frame, ttk)
        self._build_advanced_group(frame, ttk)
        ttk.Label(frame, textvariable=self._vars["message"], foreground="#555555").grid(row=8, column=0, sticky="w", pady=(8, 0))
        self._refresh()
        self._root.mainloop()

    def _build_layout_group(self, frame, ttk, tk):
        group = ttk.LabelFrame(frame, text="PC 레이아웃")
        group.grid(row=4, column=0, sticky="nsew", pady=(16, 0))
        group.columnconfigure(0, weight=1)
        group.rowconfigure(3, weight=1)

        tools = ttk.Frame(group, padding=(8, 8, 8, 0))
        tools.grid(row=0, column=0, sticky="ew")
        self._layout_edit_toggle = ttk.Checkbutton(tools, text="편집 모드", variable=self._vars["layout_edit"], command=self._on_edit_mode_changed)
        self._layout_edit_toggle.pack(side="left")
        self._auto_switch_toggle = ttk.Checkbutton(tools, text="경계 자동 전환", variable=self._vars["auto_switch_enabled"], command=self._on_auto_switch_toggled)
        self._auto_switch_toggle.pack(side="left", padx=(12, 0))
        self._auto_switch_settings_button = ttk.Button(tools, text="자동 전환 세부 설정", command=self._open_auto_switch_editor)
        self._auto_switch_settings_button.pack(side="left", padx=(12, 0))
        self._monitor_editor_button = ttk.Button(tools, text="모니터 맵 편집", command=self._open_monitor_editor)
        self._monitor_editor_button.pack(side="left", padx=(12, 0))

        ttk.Label(group, textvariable=self._vars["layout_hint"], foreground="#555555", padding=(8, 4, 8, 2)).grid(row=1, column=0, sticky="w")
        ttk.Label(group, textvariable=self._vars["selected_node"], foreground="#555555", padding=(8, 0, 8, 6)).grid(row=2, column=0, sticky="w")

        canvas_frame = ttk.Frame(group, padding=(8, 0, 8, 8))
        canvas_frame.grid(row=3, column=0, sticky="nsew")
        canvas_frame.columnconfigure(0, weight=1)
        canvas_frame.rowconfigure(0, weight=1)
        self._layout_canvas = tk.Canvas(canvas_frame, width=self.DEFAULT_CANVAS_WIDTH, height=self.DEFAULT_CANVAS_HEIGHT, highlightthickness=0, background="#ffffff")
        x_scroll = ttk.Scrollbar(canvas_frame, orient="horizontal", command=self._layout_canvas.xview)
        y_scroll = ttk.Scrollbar(canvas_frame, orient="vertical", command=self._layout_canvas.yview)
        self._layout_canvas.configure(xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set)
        self._layout_canvas.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        self._layout_canvas.bind("<ButtonPress-1>", self._on_layout_press)
        self._layout_canvas.bind("<B1-Motion>", self._on_layout_drag)
        self._layout_canvas.bind("<ButtonRelease-1>", self._on_layout_release)

    def _build_peer_group(self, frame, ttk):
        group = ttk.LabelFrame(frame, text="현재 연결 상태")
        group.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        group.columnconfigure(0, weight=1)
        self._peer_frame = ttk.Frame(group, padding=8)
        self._peer_frame.grid(row=0, column=0, sticky="ew")

    def _build_button_row(self, frame, ttk):
        row = ttk.Frame(frame)
        row.grid(row=6, column=0, sticky="ew", pady=(16, 0))
        if self.config_reloader is not None:
            ttk.Button(row, text="Config Reload", command=self._reload_config).pack(side="left")
        if self.coord_client is not None:
            ttk.Button(row, text="선택 해제", command=self._clear_target).pack(side="left", padx=(8, 0))
        ttk.Button(row, textvariable=self._vars["advanced_toggle"], command=self._toggle_advanced).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="닫기", command=self._handle_close).pack(side="right")

    def _build_advanced_group(self, frame, ttk):
        self._advanced_frame = ttk.LabelFrame(frame, text="고급 정보")
        self._advanced_frame.grid(row=7, column=0, sticky="ew", pady=(12, 0))
        self._advanced_frame.columnconfigure(1, weight=1)
        rows = [("현재 노드", "self_id"), ("현재 coordinator", "coordinator"), ("라우터 상태", "router"), ("허용 controller", "lease"), ("config 경로", "config_path")]
        for index, (label, key) in enumerate(rows):
            ttk.Label(self._advanced_frame, text=label).grid(row=index, column=0, sticky="w", pady=2, padx=(8, 8))
            ttk.Label(self._advanced_frame, textvariable=self._vars[key]).grid(row=index, column=1, sticky="w", pady=2, padx=(0, 8))
        ttk.Label(self._advanced_frame, text="peer 상세").grid(row=len(rows), column=0, sticky="nw", pady=(8, 8), padx=(8, 8))
        ttk.Label(self._advanced_frame, textvariable=self._advanced_peer_var, justify="left").grid(row=len(rows), column=1, sticky="w", pady=(8, 8), padx=(0, 8))
        self._advanced_frame.grid_remove()

    def _refresh(self):
        if self._root is None:
            return
        view = build_status_view(self.ctx, self.registry, self.coordinator_resolver, router=self.router, sink=self.sink)
        self._sync_layout_draft()
        editor_id = None if self.coord_client is None else self.coord_client.get_layout_editor()
        pending = False if self.coord_client is None else self.coord_client.is_layout_edit_pending()
        is_editor = False if self.coord_client is None else self.coord_client.is_layout_editor()
        if self._draft_layout and self._selected_layout_node_id and self._draft_layout.get_node(self._selected_layout_node_id) is None:
            self._selected_layout_node_id = None
        self._vars["layout_edit"].set(is_editor or pending)
        self._set_widget_enabled(self._layout_edit_toggle, self.coord_client is not None and editor_id in (None, self.ctx.self_node.node_id))
        self._set_widget_enabled(self._auto_switch_toggle, is_editor and self._draft_layout is not None)
        self._set_widget_enabled(self._auto_switch_settings_button, is_editor and self._draft_layout is not None)
        self._set_widget_enabled(self._monitor_editor_button, is_editor and self._draft_layout is not None and self._selected_layout_node_id is not None)
        self._vars["headline"].set(build_primary_status_text(view))
        self._vars["summary"].set(build_connection_summary_text(view))
        self._vars["hint"].set(build_selection_hint_text(view))
        self._vars["layout_hint"].set(build_layout_editor_hint(is_editor, self._vars["auto_switch_enabled"].get(), editor_id, self.ctx.self_node.node_id, pending))
        self._vars["selected_node"].set(self._selected_node_text())
        self._vars["self_id"].set(view.self_id)
        self._vars["coordinator"].set(view.coordinator_id or "-")
        self._vars["router"].set(f"{view.router_state or '-'} / {view.selected_target or '-'}")
        self._vars["lease"].set(view.authorized_controller or "-")
        self._vars["config_path"].set(view.config_path or "-")
        self._advanced_peer_var.set("\n".join(build_advanced_peer_text(peer) for peer in view.peers) or "-")
        self._render_peers(view.peers)
        if self._drag_node_id is None:
            self._render_layout(view)
        self._root.after(self.refresh_ms, self._refresh)

    def _sync_layout_draft(self):
        if self.ctx.layout is None:
            return
        if self._drag_node_id is None or self.coord_client is None or not self.coord_client.is_layout_editor():
            self._draft_layout = self.ctx.layout
            self._vars["auto_switch_enabled"].set(self.ctx.layout.auto_switch.enabled)

    def _render_peers(self, peers):
        from tkinter import ttk
        current = {peer.node_id for peer in peers}
        for node_id in set(self._peer_labels) - current:
            self._peer_labels.pop(node_id).destroy()
        for index, peer in enumerate(peers):
            label = self._peer_labels.get(peer.node_id)
            if label is None:
                label = ttk.Label(self._peer_frame, anchor="w")
                self._peer_labels[peer.node_id] = label
            label.grid(row=index, column=0, sticky="ew", pady=2)
            label.configure(text=build_peer_summary_text(peer))

    def _render_layout(self, view):
        if self._layout_canvas is None or self._draft_layout is None:
            return
        self._layout_canvas.delete("all")
        self._layout_item_to_node_id.clear()
        geometry = self._compute_layout_geometry(self._draft_layout)
        self._layout_geometry = geometry
        self._layout_canvas.configure(scrollregion=(0, 0, geometry["scene_width"], geometry["scene_height"]))
        online = {peer.node_id: peer.online for peer in view.peers}
        state_by_target = {target.node_id: target for target in view.targets}
        current_node_id = view.selected_target or view.self_id
        for node in self._draft_layout.nodes:
            x1, y1, x2, y2 = self._node_canvas_bounds(node, geometry)
            target_view = state_by_target.get(node.node_id)
            state = None if target_view is None else target_view.state
            fill, outline = build_layout_node_colors(
                is_self=node.node_id == view.self_id,
                is_online=True if node.node_id == view.self_id else online.get(node.node_id, False),
                is_selected=node.node_id == current_node_id,
                state=state,
            )
            width = 4 if node.node_id == self._selected_layout_node_id else 3 if node.node_id == current_node_id else 2
            rect_id = self._layout_canvas.create_rectangle(x1, y1, x2, y2, fill=fill, outline=outline, width=width, tags=("layout-node", f"node:{node.node_id}"))
            self._draw_monitor_overlays(node, x1, y1, x2, y2, outline)
            text_id = self._layout_canvas.create_text(
                (x1 + x2) / 2,
                y1 + 20,
                text=build_layout_node_label(
                    node.node_id,
                    is_self=node.node_id == view.self_id,
                    is_online=True if node.node_id == view.self_id else online.get(node.node_id, False),
                    is_selected=node.node_id == current_node_id,
                    state=state,
                ),
                justify="center",
                tags=("layout-node", f"node:{node.node_id}"),
            )
            self._layout_item_to_node_id[rect_id] = node.node_id
            self._layout_item_to_node_id[text_id] = node.node_id

    def _draw_monitor_overlays(self, node, x1, y1, x2, y2, outline):
        topology = node.monitors()
        if len(topology.physical) <= 1:
            return
        rows = monitor_topology_to_rows(topology, logical=False)
        grid_h = len(rows)
        grid_w = max(len(row) for row in rows)
        for display in topology.physical:
            dx1 = x1 + (display.x / grid_w) * (x2 - x1)
            dy1 = y1 + (display.y / grid_h) * (y2 - y1)
            dx2 = x1 + ((display.x + display.width) / grid_w) * (x2 - x1)
            dy2 = y1 + ((display.y + display.height) / grid_h) * (y2 - y1)
            rect_id = self._layout_canvas.create_rectangle(dx1 + 6, dy1 + 6, dx2 - 6, dy2 - 6, outline=outline, width=1, dash=(3, 2), tags=("layout-node", f"node:{node.node_id}"))
            text_id = self._layout_canvas.create_text((dx1 + dx2) / 2, (dy1 + dy2) / 2, text=display.display_id, fill=outline, tags=("layout-node", f"node:{node.node_id}"))
            self._layout_item_to_node_id[rect_id] = node.node_id
            self._layout_item_to_node_id[text_id] = node.node_id

    def _compute_layout_geometry(self, layout: LayoutConfig):
        min_x, min_y, max_x, max_y = layout_bounds(layout)
        return {
            "min_x": min_x,
            "min_y": min_y,
            "scene_width": max(self.DEFAULT_CANVAS_WIDTH, (max_x - min_x) * self.GRID_PITCH_X + self.CANVAS_PADDING * 2),
            "scene_height": max(self.DEFAULT_CANVAS_HEIGHT, (max_y - min_y) * self.GRID_PITCH_Y + self.CANVAS_PADDING * 2),
        }

    def _node_canvas_bounds(self, node, geometry):
        x1 = self.CANVAS_PADDING + (node.x - geometry["min_x"]) * self.GRID_PITCH_X + self.TILE_MARGIN_X
        y1 = self.CANVAS_PADDING + (node.y - geometry["min_y"]) * self.GRID_PITCH_Y + self.TILE_MARGIN_Y
        x2 = self.CANVAS_PADDING + (node.x + node.width - geometry["min_x"]) * self.GRID_PITCH_X - self.TILE_MARGIN_X
        y2 = self.CANVAS_PADDING + (node.y + node.height - geometry["min_y"]) * self.GRID_PITCH_Y - self.TILE_MARGIN_Y
        return x1, y1, x2, y2

    def _on_layout_press(self, event):
        node_id = self._node_id_from_canvas_event()
        if node_id is None:
            return
        self._selected_layout_node_id = node_id
        if self._can_edit_layout():
            self._drag_node_id = node_id
            self._drag_origin_canvas = (self._layout_canvas.canvasx(event.x), self._layout_canvas.canvasy(event.y))
            node = None if self._draft_layout is None else self._draft_layout.get_node(node_id)
            if node is not None:
                self._drag_origin_grid = (node.x, node.y)
            return
        self._activate_layout_node(node_id)

    def _on_layout_drag(self, event):
        if None in (self._layout_canvas, self._draft_layout, self._drag_node_id, self._drag_origin_canvas, self._drag_origin_grid) or not self._can_edit_layout():
            return
        grid_x = self._drag_origin_grid[0] + round((self._layout_canvas.canvasx(event.x) - self._drag_origin_canvas[0]) / self.GRID_PITCH_X)
        grid_y = self._drag_origin_grid[1] + round((self._layout_canvas.canvasy(event.y) - self._drag_origin_canvas[1]) / self.GRID_PITCH_Y)
        current = self._draft_layout.get_node(self._drag_node_id)
        if current is None or (current.x == grid_x and current.y == grid_y):
            return
        candidate = replace_layout_node(self._draft_layout, self._drag_node_id, x=grid_x, y=grid_y)
        if self._find_overlaps(candidate, self._drag_node_id):
            self._vars["message"].set("겹치는 배치는 사용할 수 없습니다.")
            return
        self._publish_layout(candidate, "레이아웃을 실시간으로 적용했습니다.")

    def _on_layout_release(self, _event):
        self._drag_node_id = None
        self._drag_origin_canvas = None
        self._drag_origin_grid = None

    def _node_id_from_canvas_event(self):
        item = None if self._layout_canvas is None else self._layout_canvas.find_withtag("current")
        return None if not item else self._layout_item_to_node_id.get(item[0])

    def _activate_layout_node(self, node_id):
        if node_id == self.ctx.self_node.node_id:
            if self.coord_client is not None:
                self.coord_client.clear_target()
                self._vars["message"].set("내 PC 제어로 돌아왔습니다.")
            return
        node = self.ctx.get_node(node_id)
        if node is None:
            self._vars["message"].set(f"{node_id} PC 정보를 찾을 수 없습니다.")
            return
        if not node.has_role("target"):
            self._vars["message"].set(f"{node_id} PC는 target 역할이 아닙니다.")
            return
        online_ids = {peer.node_id for peer in build_status_view(self.ctx, self.registry, self.coordinator_resolver, router=self.router, sink=self.sink).peers if peer.online}
        if node_id not in online_ids:
            self._vars["message"].set(f"{node_id} PC가 아직 연결되지 않았습니다.")
            return
        if self.coord_client is not None:
            self.coord_client.request_target(node_id)
            self._vars["message"].set(f"{node_id} PC로 전환을 요청했습니다.")

    def _on_auto_switch_toggled(self):
        if self._draft_layout is None:
            return
        if not self._can_edit_layout():
            self._vars["auto_switch_enabled"].set(self._draft_layout.auto_switch.enabled)
            self._vars["message"].set("편집 권한을 가진 노드만 자동 전환 설정을 바꿀 수 있습니다.")
            return
        candidate = replace_auto_switch_settings(self._draft_layout, enabled=self._vars["auto_switch_enabled"].get())
        self._publish_layout(candidate, "자동 전환 설정을 실시간으로 반영했습니다.")

    def _open_auto_switch_editor(self):
        if not self._can_edit_layout() or self._draft_layout is None:
            self._vars["message"].set("편집 권한을 가진 노드만 자동 전환 세부 설정을 바꿀 수 있습니다.")
            return
        import tkinter as tk
        from tkinter import ttk

        self._close_auto_switch_editor()
        win = tk.Toplevel(self._root)
        win.title("자동 전환 세부 설정")
        win.geometry("430x320")
        frame = ttk.Frame(win, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        settings = self._draft_layout.auto_switch
        fields = [
            ("edge_threshold", "경계 감도"),
            ("warp_margin", "anchor margin"),
            ("cooldown_ms", "cooldown(ms)"),
            ("return_guard_ms", "return guard(ms)"),
            ("anchor_dead_zone", "anchor dead-zone"),
        ]
        entries = {}
        for index, (key, label) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=index, column=0, sticky="w", pady=4, padx=(0, 8))
            entry = ttk.Entry(frame)
            entry.grid(row=index, column=1, sticky="ew", pady=4)
            entry.insert(0, str(getattr(settings, key)))
            entries[key] = entry
        status_var = tk.StringVar(value="값을 검증한 뒤 적용하면 전체 노드에 즉시 반영됩니다.")
        ttk.Label(frame, textvariable=status_var, foreground="#555555", wraplength=380).grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=(12, 0))
        buttons = ttk.Frame(frame)
        buttons.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(buttons, text="검증", command=lambda: self._apply_auto_switch_editor(entries, status_var, False)).pack(side="left")
        ttk.Button(buttons, text="적용", command=lambda: self._apply_auto_switch_editor(entries, status_var, True)).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="닫기", command=self._close_auto_switch_editor).pack(side="right")
        self._auto_switch_editor = {"window": win}
        win.protocol("WM_DELETE_WINDOW", self._close_auto_switch_editor)

    def _apply_auto_switch_editor(self, entries, status_var, apply):
        if self._draft_layout is None:
            return
        try:
            parsed = parse_auto_switch_form({key: entry.get() for key, entry in entries.items()})
            candidate = replace_auto_switch_settings(self._draft_layout, **parsed)
        except Exception as exc:
            status_var.set(f"검증 실패: {exc}")
            return
        if not apply:
            status_var.set("검증 성공: 자동 전환 세부 설정 값을 사용할 수 있습니다.")
            return
        if self._publish_layout(candidate, "자동 전환 세부 설정을 실시간으로 적용했습니다."):
            status_var.set("적용 완료: 자동 전환 세부 설정을 반영했습니다.")
        else:
            status_var.set("적용 실패: 변경사항을 전송하지 못했습니다.")

    def _on_edit_mode_changed(self):
        if self.coord_client is None:
            self._vars["layout_edit"].set(False)
            self._vars["message"].set("편집 기능을 사용할 수 없습니다.")
            return
        if self._vars["layout_edit"].get():
            editor = self.coord_client.get_layout_editor()
            if editor not in (None, self.ctx.self_node.node_id):
                self._vars["layout_edit"].set(False)
                self._vars["message"].set(f"{editor} PC가 이미 편집 중입니다.")
                return
            self.coord_client.request_layout_edit()
            self._vars["message"].set("편집 권한을 요청했습니다.")
            return
        self._drag_node_id = None
        self._drag_origin_canvas = None
        self._drag_origin_grid = None
        self._close_auto_switch_editor()
        self._close_monitor_editor()
        self.coord_client.end_layout_edit()
        self._vars["message"].set("편집 모드를 종료했습니다.")

    def _open_monitor_editor(self):
        if not self._can_edit_layout() or self._draft_layout is None or self._selected_layout_node_id is None:
            self._vars["message"].set("편집 모드에서 선택된 PC가 있어야 모니터 맵을 수정할 수 있습니다.")
            return
        import tkinter as tk
        from tkinter import ttk

        node = self._draft_layout.get_node(self._selected_layout_node_id)
        if node is None:
            return
        self._close_monitor_editor()
        win = tk.Toplevel(self._root)
        win.title(f"모니터 맵 편집 [{node.node_id}]")
        win.geometry("680x450")
        frame = ttk.Frame(win, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(2, weight=1)
        ttk.Label(frame, text="공백으로 셀을 구분하고 빈 칸은 . 으로 입력하세요.", wraplength=620).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, text="논리 모니터 배치").grid(row=1, column=0, sticky="w", pady=(10, 4))
        ttk.Label(frame, text="물리 모니터 배치").grid(row=1, column=1, sticky="w", pady=(10, 4))
        logical = tk.Text(frame, width=32, height=14)
        physical = tk.Text(frame, width=32, height=14)
        logical.grid(row=2, column=0, sticky="nsew", padx=(0, 8))
        physical.grid(row=2, column=1, sticky="nsew")
        logical.insert("1.0", format_monitor_grid_text(monitor_topology_to_rows(node.monitors(), logical=True)))
        physical.insert("1.0", format_monitor_grid_text(monitor_topology_to_rows(node.monitors(), logical=False)))
        status_var = tk.StringVar(value=f"{node.node_id} PC의 논리/물리 모니터 맵을 별도로 편집할 수 있습니다.")
        ttk.Label(frame, textvariable=status_var, foreground="#555555").grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(buttons, text="검증", command=lambda: self._apply_monitor_editor(node.node_id, logical, physical, status_var, False)).pack(side="left")
        ttk.Button(buttons, text="적용", command=lambda: self._apply_monitor_editor(node.node_id, logical, physical, status_var, True)).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="닫기", command=self._close_monitor_editor).pack(side="right")
        self._monitor_editor = {"window": win}
        win.protocol("WM_DELETE_WINDOW", self._close_monitor_editor)

    def _apply_monitor_editor(self, node_id, logical_widget, physical_widget, status_var, apply):
        if self._draft_layout is None:
            return
        try:
            candidate = replace_layout_monitors(
                self._draft_layout,
                node_id,
                logical_rows=parse_monitor_grid_text(logical_widget.get("1.0", "end")),
                physical_rows=parse_monitor_grid_text(physical_widget.get("1.0", "end")),
            )
            overlaps = self._find_overlaps(candidate)
            if overlaps:
                raise ValueError("물리 배치 변경으로 PC가 겹칩니다.")
        except Exception as exc:
            status_var.set(f"검증 실패: {exc}")
            return
        node = candidate.get_node(node_id)
        logical_widget.delete("1.0", "end")
        physical_widget.delete("1.0", "end")
        logical_widget.insert("1.0", format_monitor_grid_text(monitor_topology_to_rows(node.monitors(), logical=True)))
        physical_widget.insert("1.0", format_monitor_grid_text(monitor_topology_to_rows(node.monitors(), logical=False)))
        if not apply:
            status_var.set(f"검증 성공: 물리 {node.width}x{node.height}, display {len(node.monitors().physical)}개")
            return
        if self._publish_layout(candidate, "모니터 맵을 실시간으로 적용했습니다."):
            status_var.set(f"적용 완료: 물리 {node.width}x{node.height}, display {len(node.monitors().physical)}개")
        else:
            status_var.set("적용 실패: 변경사항을 전송하지 못했습니다.")

    def _close_monitor_editor(self):
        if self._monitor_editor is None:
            return
        window = self._monitor_editor.get("window")
        self._monitor_editor = None
        if window is not None and window.winfo_exists():
            window.destroy()

    def _close_auto_switch_editor(self):
        if self._auto_switch_editor is None:
            return
        window = self._auto_switch_editor.get("window")
        self._auto_switch_editor = None
        if window is not None and window.winfo_exists():
            window.destroy()

    def _toggle_advanced(self):
        self._advanced_visible = not self._advanced_visible
        if self._advanced_visible:
            self._advanced_frame.grid()
            self._vars["advanced_toggle"].set("고급 정보 숨기기")
        else:
            self._advanced_frame.grid_remove()
            self._vars["advanced_toggle"].set("고급 정보 보기")

    def _reload_config(self):
        if self.config_reloader is None:
            return
        try:
            self.config_reloader.reload()
        except Exception as exc:
            self._vars["message"].set(f"reload 실패: {exc}")
        else:
            self._draft_layout = self.ctx.layout
            self._vars["message"].set("config reload 완료")
            if self.ctx.layout is not None:
                self._vars["auto_switch_enabled"].set(self.ctx.layout.auto_switch.enabled)

    def _clear_target(self):
        if self.coord_client is not None:
            self.coord_client.clear_target()
            self._vars["message"].set("target 선택 해제")

    def _handle_close(self):
        self._close_auto_switch_editor()
        self._close_monitor_editor()
        if self.coord_client is not None and self.coord_client.is_layout_editor():
            self.coord_client.end_layout_edit()
        if self._on_close is not None:
            self._on_close()
        if self._root is not None:
            self._root.destroy()
            self._root = None

    def _can_edit_layout(self):
        return self.coord_client is not None and self.coord_client.is_layout_editor() and self._vars["layout_edit"].get()

    def _publish_layout(self, candidate: LayoutConfig, success_message: str):
        previous = self._draft_layout
        self._draft_layout = candidate
        if self.coord_client is None or not self.coord_client.publish_layout(candidate):
            self._draft_layout = self.ctx.layout or previous
            self._vars["message"].set("레이아웃 변경을 전송하지 못했습니다.")
            return False
        self._vars["message"].set(success_message)
        return True

    def _selected_node_text(self):
        if self._draft_layout is None or self._selected_layout_node_id is None:
            return "선택된 PC: -"
        node = self._draft_layout.get_node(self._selected_layout_node_id)
        if node is None:
            return "선택된 PC: -"
        logical = monitor_topology_to_rows(node.monitors(), logical=True)
        physical = monitor_topology_to_rows(node.monitors(), logical=False)
        return f"선택된 PC: {node.node_id} | 물리 {max(len(r) for r in physical)}x{len(physical)} | 논리 {max(len(r) for r in logical)}x{len(logical)} | display {len(node.monitors().physical)}개"

    def _find_overlaps(self, layout: LayoutConfig, moving_node_id: str | None = None):
        overlaps = []
        for index, left in enumerate(layout.nodes):
            for right in layout.nodes[index + 1 :]:
                if left.left < right.right and left.right > right.left and left.top < right.bottom and left.bottom > right.top:
                    if moving_node_id is None or moving_node_id in (left.node_id, right.node_id):
                        overlaps.append((left.node_id, right.node_id))
        return overlaps

    def _set_widget_enabled(self, widget, enabled: bool):
        if widget is not None:
            widget.state(["!disabled"] if enabled else ["disabled"])
