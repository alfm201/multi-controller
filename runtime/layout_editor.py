"""Interactive layout editor widget used by the status window."""

from dataclasses import dataclass, field
import math

from runtime.layout_dialogs import AutoSwitchDialog, MonitorMapDialog
from runtime.layout_geometry import (
    LayoutGeometrySpec,
    ViewportState,
    center_viewport,
    fit_viewport,
    layout_world_bounds,
    node_world_bounds,
    screen_delta_to_grid,
    screen_to_world,
    world_to_screen,
    zoom_at_point,
)
from runtime.layouts import (
    LayoutConfig,
    find_overlapping_nodes,
    monitor_topology_to_rows,
    replace_auto_switch_settings,
    replace_layout_node,
)
from runtime.status_view import (
    build_layout_editor_hint,
    build_layout_lock_text,
    build_layout_node_colors,
    build_layout_node_label,
    build_selected_node_text,
    build_status_view,
    build_viewport_summary,
)


@dataclass
class LayoutLockState:
    editor_id: str | None = None
    is_editor: bool = False
    pending: bool = False


@dataclass
class DragState:
    kind: str | None = None
    node_id: str | None = None
    origin_screen: tuple[float, float] | None = None
    origin_grid: tuple[int, int] | None = None
    origin_pan: tuple[float, float] | None = None
    start_layout: LayoutConfig | None = None
    preview_dirty: bool = False

    def clear(self):
        self.kind = None
        self.node_id = None
        self.origin_screen = None
        self.origin_grid = None
        self.origin_pan = None
        self.start_layout = None
        self.preview_dirty = False


@dataclass
class EditorState:
    draft_layout: LayoutConfig | None = None
    selected_node_id: str | None = None
    viewport: ViewportState = field(default_factory=ViewportState)
    drag: DragState = field(default_factory=DragState)
    lock: LayoutLockState = field(default_factory=LayoutLockState)
    auto_switch_enabled: bool = False


class LayoutEditor:
    """Canvas-driven layout editor with viewport zoom/pan controls."""

    def __init__(
        self,
        ctx,
        registry,
        coordinator_resolver,
        router=None,
        sink=None,
        coord_client=None,
        on_message=None,
    ):
        self.ctx = ctx
        self.registry = registry
        self.coordinator_resolver = coordinator_resolver
        self.router = router
        self.sink = sink
        self.coord_client = coord_client
        self._on_message = on_message or (lambda _message: None)
        self._spec = LayoutGeometrySpec()

        initial_layout = ctx.layout
        self.state = EditorState(
            draft_layout=initial_layout,
            auto_switch_enabled=(
                False if initial_layout is None else initial_layout.auto_switch.enabled
            ),
        )

        self._frame = None
        self._canvas = None
        self._vars = {}
        self._layout_item_to_node_id = {}
        self._canvas_width = 0
        self._canvas_height = 0
        self._viewport_initialized = False
        self._last_view = None
        self._auto_switch_dialog = None
        self._monitor_dialog = None
        self._layout_edit_toggle = None
        self._auto_switch_toggle = None
        self._auto_switch_settings_button = None
        self._monitor_editor_button = None
        self._fit_button = None
        self._zoom_reset_button = None
        self._view_reset_button = None

    def build(self, parent):
        import tkinter as tk
        from tkinter import ttk

        self._frame = ttk.Frame(parent, padding=12)
        self._frame.columnconfigure(0, weight=1)
        self._frame.rowconfigure(3, weight=1)

        self._vars["layout_hint"] = tk.StringVar()
        self._vars["lock_summary"] = tk.StringVar()
        self._vars["viewport"] = tk.StringVar()
        self._vars["selected_node"] = tk.StringVar(value="선택된 PC: -")
        self._vars["layout_edit"] = tk.BooleanVar(value=False)
        self._vars["auto_switch_enabled"] = tk.BooleanVar(
            value=self.state.auto_switch_enabled
        )

        tools = ttk.Frame(self._frame)
        tools.grid(row=0, column=0, sticky="ew")
        self._layout_edit_toggle = ttk.Checkbutton(
            tools,
            text="편집 모드",
            variable=self._vars["layout_edit"],
            command=self._on_edit_mode_changed,
        )
        self._layout_edit_toggle.pack(side="left")
        self._auto_switch_toggle = ttk.Checkbutton(
            tools,
            text="경계 자동 전환",
            variable=self._vars["auto_switch_enabled"],
            command=self._on_auto_switch_toggled,
        )
        self._auto_switch_toggle.pack(side="left", padx=(10, 0))
        self._auto_switch_settings_button = ttk.Button(
            tools,
            text="자동 전환 세부 설정",
            command=self._open_auto_switch_editor,
        )
        self._auto_switch_settings_button.pack(side="left", padx=(10, 0))
        self._monitor_editor_button = ttk.Button(
            tools,
            text="모니터 맵 편집",
            command=self._open_monitor_editor,
        )
        self._monitor_editor_button.pack(side="left", padx=(10, 0))

        view_tools = ttk.Frame(tools)
        view_tools.pack(side="right")
        ttk.Button(view_tools, text="-", width=3, command=self._zoom_out).pack(
            side="left"
        )
        ttk.Button(view_tools, text="+", width=3, command=self._zoom_in).pack(
            side="left",
            padx=(6, 0),
        )
        self._zoom_reset_button = ttk.Button(
            view_tools,
            text="100%",
            command=self._reset_zoom,
        )
        self._zoom_reset_button.pack(side="left", padx=(6, 0))
        self._fit_button = ttk.Button(view_tools, text="맞춤", command=self.fit_view)
        self._fit_button.pack(side="left", padx=(6, 0))
        self._view_reset_button = ttk.Button(
            view_tools,
            text="초기화",
            command=self.reset_view,
        )
        self._view_reset_button.pack(side="left", padx=(6, 0))

        ttk.Label(
            self._frame,
            textvariable=self._vars["layout_hint"],
            foreground="#555555",
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))

        info = ttk.Frame(self._frame)
        info.grid(row=2, column=0, sticky="ew", pady=(6, 8))
        info.columnconfigure(0, weight=1)
        ttk.Label(info, textvariable=self._vars["lock_summary"]).grid(
            row=0,
            column=0,
            sticky="w",
        )
        ttk.Label(info, textvariable=self._vars["viewport"]).grid(
            row=0,
            column=1,
            sticky="e",
        )
        ttk.Label(info, textvariable=self._vars["selected_node"]).grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(4, 0),
        )

        self._canvas = tk.Canvas(
            self._frame,
            width=980,
            height=620,
            highlightthickness=0,
            background="#ffffff",
        )
        self._canvas.grid(row=3, column=0, sticky="nsew")
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self._canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self._canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        self._canvas.bind("<Button-4>", self._on_mouse_wheel)
        self._canvas.bind("<Button-5>", self._on_mouse_wheel)
        self._canvas.bind("<KeyPress-plus>", lambda _event: self._zoom_in())
        self._canvas.bind("<KeyPress-equal>", lambda _event: self._zoom_in())
        self._canvas.bind("<KeyPress-minus>", lambda _event: self._zoom_out())
        self._canvas.bind("<KeyPress-0>", lambda _event: self._reset_zoom())
        self._canvas.bind("<KeyPress-f>", lambda _event: self.fit_view())
        self._canvas.bind("<Escape>", self._on_escape)
        return self._frame

    def refresh(self, view):
        self._last_view = view
        self._sync_layout_draft()
        editor_id = None if self.coord_client is None else self.coord_client.get_layout_editor()
        pending = (
            False
            if self.coord_client is None
            else self.coord_client.is_layout_edit_pending()
        )
        is_editor = (
            False if self.coord_client is None else self.coord_client.is_layout_editor()
        )
        self.state.lock = LayoutLockState(
            editor_id=editor_id,
            is_editor=is_editor,
            pending=pending,
        )

        if (
            self.state.draft_layout is not None
            and self.state.selected_node_id is not None
            and self.state.draft_layout.get_node(self.state.selected_node_id) is None
        ):
            self.state.selected_node_id = None

        self._vars["layout_edit"].set(is_editor or pending)
        self._vars["auto_switch_enabled"].set(self.state.auto_switch_enabled)
        self._vars["layout_hint"].set(
            build_layout_editor_hint(
                is_editor,
                self.state.auto_switch_enabled,
                editor_id,
                self.ctx.self_node.node_id,
                pending,
            )
        )
        self._vars["lock_summary"].set(
            build_layout_lock_text(editor_id, self.ctx.self_node.node_id, pending)
        )
        self._vars["viewport"].set(
            build_viewport_summary(
                self.state.viewport.zoom,
                self.state.viewport.pan_x,
                self.state.viewport.pan_y,
            )
        )
        selected = (
            None
            if self.state.draft_layout is None or self.state.selected_node_id is None
            else self.state.draft_layout.get_node(self.state.selected_node_id)
        )
        self._vars["selected_node"].set(build_selected_node_text(selected))

        self._set_widget_enabled(
            self._layout_edit_toggle,
            self.coord_client is not None
            and editor_id in (None, self.ctx.self_node.node_id),
        )
        self._set_widget_enabled(
            self._auto_switch_toggle,
            self._can_edit_layout() and self.state.draft_layout is not None,
        )
        self._set_widget_enabled(
            self._auto_switch_settings_button,
            self._can_edit_layout() and self.state.draft_layout is not None,
        )
        self._set_widget_enabled(
            self._monitor_editor_button,
            self._can_edit_layout()
            and self.state.draft_layout is not None
            and self.state.selected_node_id is not None,
        )
        has_layout = self.state.draft_layout is not None
        self._set_widget_enabled(self._fit_button, has_layout)
        self._set_widget_enabled(self._zoom_reset_button, has_layout)
        self._set_widget_enabled(self._view_reset_button, has_layout)
        self.render(view)

    def render(self, view):
        if self._canvas is None:
            return
        self._canvas.delete("all")
        self._layout_item_to_node_id.clear()

        layout = self.state.draft_layout
        if layout is None:
            self._canvas.create_text(
                max(self._canvas_width / 2, 100),
                max(self._canvas_height / 2, 100),
                text="레이아웃 정보가 없습니다.",
                fill="#6b7280",
            )
            return

        if not self._viewport_initialized and self._canvas_width and self._canvas_height:
            self.fit_view()

        self._draw_background_grid()

        online = {peer.node_id: peer.online for peer in view.peers}
        state_by_target = {target.node_id: target for target in view.targets}
        current_node_id = view.selected_target or view.self_id
        for node in layout.nodes:
            bounds = node_world_bounds(node, self._spec)
            x1, y1 = world_to_screen(bounds.left, bounds.top, self.state.viewport)
            x2, y2 = world_to_screen(bounds.right, bounds.bottom, self.state.viewport)
            target_view = state_by_target.get(node.node_id)
            state = None if target_view is None else target_view.state
            fill, outline = build_layout_node_colors(
                is_self=node.node_id == view.self_id,
                is_online=(
                    True if node.node_id == view.self_id else online.get(node.node_id, False)
                ),
                is_selected=node.node_id == current_node_id,
                state=state,
            )
            width = (
                4
                if node.node_id == self.state.selected_node_id
                else 3 if node.node_id == current_node_id else 2
            )
            rect_id = self._canvas.create_rectangle(
                x1,
                y1,
                x2,
                y2,
                fill=fill,
                outline=outline,
                width=width,
                tags=("layout-node", f"node:{node.node_id}"),
            )
            self._draw_monitor_overlays(node, x1, y1, x2, y2, outline)
            label = build_layout_node_label(
                node.node_id,
                is_self=node.node_id == view.self_id,
                is_online=(
                    True if node.node_id == view.self_id else online.get(node.node_id, False)
                ),
                is_selected=node.node_id == current_node_id,
                state=state,
            )
            text_id = self._canvas.create_text(
                (x1 + x2) / 2,
                y1 + min((y2 - y1) * 0.28, 24),
                text=label,
                justify="center",
                width=max(x2 - x1 - 8, 16),
                tags=("layout-node", f"node:{node.node_id}"),
            )
            self._layout_item_to_node_id[rect_id] = node.node_id
            self._layout_item_to_node_id[text_id] = node.node_id

    def fit_view(self):
        if self.state.draft_layout is None or not self._canvas_width or not self._canvas_height:
            return
        bounds = layout_world_bounds(self.state.draft_layout, self._spec)
        self.state.viewport = fit_viewport(
            bounds,
            self._canvas_width,
            self._canvas_height,
            self._spec,
        )
        self._viewport_initialized = True
        self._update_viewport_summary()
        self.render(self._fallback_view())

    def reset_view(self):
        if self.state.draft_layout is None or not self._canvas_width or not self._canvas_height:
            return
        bounds = layout_world_bounds(self.state.draft_layout, self._spec)
        self.state.viewport = center_viewport(
            bounds,
            self._canvas_width,
            self._canvas_height,
            self._spec,
            zoom=1.0,
        )
        self._viewport_initialized = True
        self._update_viewport_summary()
        self.render(self._fallback_view())

    def close(self):
        self._close_auto_switch_editor()
        self._close_monitor_editor()

    def _fallback_view(self):
        if self._last_view is not None:
            return self._last_view
        return build_status_view(
            self.ctx,
            self.registry,
            self.coordinator_resolver,
            router=self.router,
            sink=self.sink,
        )

    def _sync_layout_draft(self):
        if self.ctx.layout is None:
            return
        if self.state.drag.kind != "node" or self.coord_client is None or not self.coord_client.is_layout_editor():
            self.state.draft_layout = self.ctx.layout
            self.state.auto_switch_enabled = self.ctx.layout.auto_switch.enabled

    def _publish_layout(
        self,
        candidate: LayoutConfig,
        success_message: str,
        *,
        persist: bool = True,
    ) -> bool:
        previous = self.state.draft_layout
        self.state.draft_layout = candidate
        self.state.auto_switch_enabled = candidate.auto_switch.enabled
        if self.coord_client is None or not self.coord_client.publish_layout(
            candidate, persist=persist
        ):
            self.state.draft_layout = self.ctx.layout or previous
            if self.state.draft_layout is not None:
                self.state.auto_switch_enabled = self.state.draft_layout.auto_switch.enabled
            self._set_message("레이아웃 변경을 전송하지 못했습니다.")
            return False
        self._set_message(success_message)
        return True

    def _update_viewport_summary(self):
        if "viewport" in self._vars:
            self._vars["viewport"].set(
                build_viewport_summary(
                    self.state.viewport.zoom,
                    self.state.viewport.pan_x,
                    self.state.viewport.pan_y,
                )
            )

    def _on_canvas_configure(self, event):
        self._canvas_width = max(int(event.width), 1)
        self._canvas_height = max(int(event.height), 1)
        if not self._viewport_initialized and self.state.draft_layout is not None:
            self.fit_view()
        else:
            self._update_viewport_summary()
            self.render(self._fallback_view())

    def _on_canvas_press(self, event):
        if self._canvas is not None:
            self._canvas.focus_set()
        node_id = self._node_id_from_canvas_event()
        if node_id is not None:
            self.state.selected_node_id = node_id
        if node_id is not None and self._can_edit_layout():
            node = (
                None
                if self.state.draft_layout is None
                else self.state.draft_layout.get_node(node_id)
            )
            if node is not None:
                self.state.drag = DragState(
                    kind="node",
                    node_id=node_id,
                    origin_screen=(event.x, event.y),
                    origin_grid=(node.x, node.y),
                    start_layout=self.state.draft_layout,
                )
            return
        if node_id is not None:
            self._activate_layout_node(node_id)
            return
        self.state.drag = DragState(
            kind="pan",
            origin_screen=(event.x, event.y),
            origin_pan=(self.state.viewport.pan_x, self.state.viewport.pan_y),
        )

    def _on_canvas_drag(self, event):
        if self._canvas is None or self.state.drag.kind is None:
            return
        if self.state.drag.kind == "pan":
            if self.state.drag.origin_screen is None or self.state.drag.origin_pan is None:
                return
            dx = event.x - self.state.drag.origin_screen[0]
            dy = event.y - self.state.drag.origin_screen[1]
            self.state.viewport = ViewportState(
                zoom=self.state.viewport.zoom,
                pan_x=self.state.drag.origin_pan[0] + dx,
                pan_y=self.state.drag.origin_pan[1] + dy,
            )
            self._update_viewport_summary()
            self.render(self._fallback_view())
            return

        if self.state.drag.kind != "node" or not self._can_edit_layout():
            return
        if None in (
            self.state.draft_layout,
            self.state.drag.node_id,
            self.state.drag.origin_screen,
            self.state.drag.origin_grid,
        ):
            return

        delta_grid_x, delta_grid_y = screen_delta_to_grid(
            event.x - self.state.drag.origin_screen[0],
            event.y - self.state.drag.origin_screen[1],
            self.state.viewport,
            self._spec,
        )
        grid_x = self.state.drag.origin_grid[0] + delta_grid_x
        grid_y = self.state.drag.origin_grid[1] + delta_grid_y
        current = self.state.draft_layout.get_node(self.state.drag.node_id)
        if current is None or (current.x == grid_x and current.y == grid_y):
            return
        candidate = replace_layout_node(
            self.state.draft_layout,
            self.state.drag.node_id,
            x=grid_x,
            y=grid_y,
        )
        overlaps = [
            pair
            for pair in find_overlapping_nodes(candidate)
            if self.state.drag.node_id in pair
        ]
        if overlaps:
            self._set_message("겹치는 배치는 사용할 수 없습니다.")
            return
        if self._publish_layout(candidate, "레이아웃 미리보기를 반영했습니다.", persist=False):
            self.state.drag.preview_dirty = True
            self.render(self._fallback_view())

    def _on_canvas_release(self, _event):
        if (
            self.state.drag.kind == "node"
            and self.state.drag.preview_dirty
            and self.state.draft_layout is not None
            and self.state.draft_layout != self.state.drag.start_layout
        ):
            self._publish_layout(self.state.draft_layout, "레이아웃을 저장했습니다.")
        self.state.drag.clear()

    def _on_mouse_wheel(self, event):
        if self.state.draft_layout is None:
            return
        delta = getattr(event, "delta", 0)
        num = getattr(event, "num", None)
        if delta > 0 or num == 4:
            factor = 1.1
        elif delta < 0 or num == 5:
            factor = 1 / 1.1
        else:
            return
        self.state.viewport = zoom_at_point(
            self.state.viewport,
            factor=factor,
            anchor_screen_x=event.x,
            anchor_screen_y=event.y,
            spec=self._spec,
        )
        self._viewport_initialized = True
        self._update_viewport_summary()
        self.render(self._fallback_view())

    def _on_escape(self, _event):
        if self.state.drag.kind == "node" and self.state.drag.preview_dirty:
            if self.state.drag.start_layout is not None:
                self._publish_layout(
                    self.state.drag.start_layout,
                    "레이아웃 미리보기를 되돌렸습니다.",
                    persist=False,
                )
                self.render(self._fallback_view())
            self.state.drag.clear()
            return
        if self.state.drag.kind == "pan":
            self.state.drag.clear()
            return
        if self._vars["layout_edit"].get():
            self._vars["layout_edit"].set(False)
            self._on_edit_mode_changed()

    def _zoom_in(self):
        if self.state.draft_layout is None:
            return
        anchor_x = self._canvas_width / 2.0 if self._canvas_width else 0.0
        anchor_y = self._canvas_height / 2.0 if self._canvas_height else 0.0
        self.state.viewport = zoom_at_point(
            self.state.viewport,
            factor=1.1,
            anchor_screen_x=anchor_x,
            anchor_screen_y=anchor_y,
            spec=self._spec,
        )
        self._viewport_initialized = True
        self._update_viewport_summary()
        self.render(self._fallback_view())

    def _zoom_out(self):
        if self.state.draft_layout is None:
            return
        anchor_x = self._canvas_width / 2.0 if self._canvas_width else 0.0
        anchor_y = self._canvas_height / 2.0 if self._canvas_height else 0.0
        self.state.viewport = zoom_at_point(
            self.state.viewport,
            factor=1 / 1.1,
            anchor_screen_x=anchor_x,
            anchor_screen_y=anchor_y,
            spec=self._spec,
        )
        self._viewport_initialized = True
        self._update_viewport_summary()
        self.render(self._fallback_view())

    def _reset_zoom(self):
        if self.state.draft_layout is None or not self._canvas_width or not self._canvas_height:
            return
        bounds = layout_world_bounds(self.state.draft_layout, self._spec)
        self.state.viewport = center_viewport(
            bounds,
            self._canvas_width,
            self._canvas_height,
            self._spec,
            zoom=1.0,
        )
        self._viewport_initialized = True
        self._update_viewport_summary()
        self.render(self._fallback_view())

    def _on_edit_mode_changed(self):
        if self.coord_client is None:
            self._vars["layout_edit"].set(False)
            self._set_message("편집 기능을 사용할 수 없습니다.")
            return
        if self._vars["layout_edit"].get():
            editor = self.coord_client.get_layout_editor()
            if editor not in (None, self.ctx.self_node.node_id):
                self._vars["layout_edit"].set(False)
                self._set_message(f"{editor} PC가 이미 편집 중입니다.")
                return
            self.coord_client.request_layout_edit()
            self._set_message("편집 권한을 요청했습니다.")
            return
        self.state.drag.clear()
        self.close()
        self.coord_client.end_layout_edit()
        self._set_message("편집 모드를 종료했습니다.")

    def _on_auto_switch_toggled(self):
        if self.state.draft_layout is None:
            return
        if not self._can_edit_layout():
            self._vars["auto_switch_enabled"].set(
                self.state.draft_layout.auto_switch.enabled
            )
            self._set_message("편집 권한을 가진 노드만 자동 전환 설정을 바꿀 수 있습니다.")
            return
        candidate = replace_auto_switch_settings(
            self.state.draft_layout,
            enabled=self._vars["auto_switch_enabled"].get(),
        )
        if not self._publish_layout(candidate, "자동 전환 설정을 실시간으로 반영했습니다."):
            self._vars["auto_switch_enabled"].set(
                self.state.draft_layout.auto_switch.enabled
            )

    def _open_auto_switch_editor(self):
        if not self._can_edit_layout() or self.state.draft_layout is None:
            self._set_message("편집 권한을 가진 노드만 자동 전환 세부 설정을 바꿀 수 있습니다.")
            return
        self._close_auto_switch_editor()
        self._auto_switch_dialog = AutoSwitchDialog(
            self._frame.winfo_toplevel(),
            self._current_layout,
            self._publish_layout,
        )

    def _open_monitor_editor(self):
        if (
            not self._can_edit_layout()
            or self.state.draft_layout is None
            or self.state.selected_node_id is None
        ):
            self._set_message("편집 모드에서 선택된 PC가 있어야 모니터 맵을 수정할 수 있습니다.")
            return
        self._close_monitor_editor()
        self._monitor_dialog = MonitorMapDialog(
            self._frame.winfo_toplevel(),
            self.state.selected_node_id,
            self._current_layout,
            self._publish_layout,
        )

    def _close_auto_switch_editor(self):
        if self._auto_switch_dialog is not None:
            self._auto_switch_dialog.close()
            self._auto_switch_dialog = None

    def _close_monitor_editor(self):
        if self._monitor_dialog is not None:
            self._monitor_dialog.close()
            self._monitor_dialog = None

    def _current_layout(self):
        return self.state.draft_layout

    def _activate_layout_node(self, node_id: str):
        if node_id == self.ctx.self_node.node_id:
            if self.coord_client is not None:
                self.coord_client.clear_target()
                self._set_message("내 PC 제어로 돌아왔습니다.")
            return
        node = self.ctx.get_node(node_id)
        if node is None:
            self._set_message(f"{node_id} PC 정보를 찾을 수 없습니다.")
            return
        if not node.has_role("target"):
            self._set_message(f"{node_id} PC는 target 역할이 아닙니다.")
            return
        online_ids = {
            peer.node_id
            for peer in self._fallback_view().peers
            if peer.online
        }
        if node_id not in online_ids:
            self._set_message(f"{node_id} PC가 아직 연결되지 않았습니다.")
            return
        if self.coord_client is not None:
            self.coord_client.request_target(node_id)
            self._set_message(f"{node_id} PC로 전환을 요청했습니다.")

    def _draw_background_grid(self):
        if self._canvas is None or not self._canvas_width or not self._canvas_height:
            return
        world_left, world_top = screen_to_world(0, 0, self.state.viewport)
        world_right, world_bottom = screen_to_world(
            self._canvas_width,
            self._canvas_height,
            self.state.viewport,
        )
        pitch_x = self._spec.grid_pitch_x
        pitch_y = self._spec.grid_pitch_y
        start_x = math.floor(world_left / pitch_x) * pitch_x
        start_y = math.floor(world_top / pitch_y) * pitch_y
        x = start_x
        while x <= world_right:
            sx1, sy1 = world_to_screen(x, world_top, self.state.viewport)
            sx2, sy2 = world_to_screen(x, world_bottom, self.state.viewport)
            self._canvas.create_line(sx1, sy1, sx2, sy2, fill="#f2f4f7")
            x += pitch_x
        y = start_y
        while y <= world_bottom:
            sx1, sy1 = world_to_screen(world_left, y, self.state.viewport)
            sx2, sy2 = world_to_screen(world_right, y, self.state.viewport)
            self._canvas.create_line(sx1, sy1, sx2, sy2, fill="#f2f4f7")
            y += pitch_y

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
            rect_id = self._canvas.create_rectangle(
                dx1 + 6,
                dy1 + 6,
                dx2 - 6,
                dy2 - 6,
                outline=outline,
                width=1,
                dash=(3, 2),
                tags=("layout-node", f"node:{node.node_id}"),
            )
            text_id = self._canvas.create_text(
                (dx1 + dx2) / 2,
                (dy1 + dy2) / 2,
                text=display.display_id,
                fill=outline,
                tags=("layout-node", f"node:{node.node_id}"),
            )
            self._layout_item_to_node_id[rect_id] = node.node_id
            self._layout_item_to_node_id[text_id] = node.node_id

    def _node_id_from_canvas_event(self):
        item = None if self._canvas is None else self._canvas.find_withtag("current")
        return None if not item else self._layout_item_to_node_id.get(item[0])

    def _can_edit_layout(self) -> bool:
        return (
            self.coord_client is not None
            and self.coord_client.is_layout_editor()
            and self._vars["layout_edit"].get()
        )

    def _set_widget_enabled(self, widget, enabled: bool):
        if widget is not None:
            widget.state(["!disabled"] if enabled else ["disabled"])

    def _set_message(self, message: str):
        self._on_message(message)
