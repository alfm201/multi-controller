"""Interactive layout editor widget used by the status window."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import threading

from runtime.gui_style import PALETTE, palette_for_tone
from runtime.layout_dialogs import AutoSwitchDialog, MonitorMapDialog, build_monitor_preset
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
    replace_layout_monitors,
    replace_layout_node,
)
from runtime.status_view import (
    build_layout_editor_hint,
    build_layout_inspector_detail,
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
        monitor_inventory_manager=None,
        on_message=None,
        on_select_node=None,
    ):
        self.ctx = ctx
        self.registry = registry
        self.coordinator_resolver = coordinator_resolver
        self.router = router
        self.sink = sink
        self.coord_client = coord_client
        self.monitor_inventory_manager = monitor_inventory_manager
        self._on_message = on_message or (lambda _message: None)
        self._on_select_node = on_select_node or (lambda _node_id: None)
        self._spec = LayoutGeometrySpec()
        initial_layout = ctx.layout
        self.state = EditorState(
            draft_layout=initial_layout,
            auto_switch_enabled=False if initial_layout is None else initial_layout.auto_switch.enabled,
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
        self._selection_badge_frame = None
        self._selection_field_frame = None
        self._last_render_signature = None
        self._last_inspector_signature = None
        self._configure_job = None
        self._node_items = {}
        self._background_signature = None
        self._empty_text_id = None

    def build(self, parent):
        import tkinter as tk
        from tkinter import ttk

        self._frame = ttk.Frame(parent, padding=12, style="App.TFrame")
        self._frame.columnconfigure(0, weight=1)
        self._frame.rowconfigure(2, weight=1)
        self._vars["layout_hint"] = tk.StringVar()
        self._vars["lock_summary"] = tk.StringVar()
        self._vars["viewport"] = tk.StringVar()
        self._vars["selected_node"] = tk.StringVar(value="선택된 PC: -")
        self._vars["layout_edit"] = tk.BooleanVar(value=False)
        self._vars["auto_switch_enabled"] = tk.BooleanVar(value=self.state.auto_switch_enabled)
        self._vars["selection_title"] = tk.StringVar(value="선택된 PC 없음")
        self._vars["selection_subtitle"] = tk.StringVar(value="레이아웃 캔버스에서 PC를 선택하세요.")
        self._vars["selection_action"] = tk.StringVar(value="선택한 PC의 모니터 맵을 수정하세요.")

        tools = ttk.Frame(self._frame, style="Toolbar.TFrame")
        tools.grid(row=0, column=0, sticky="ew")
        for column in range(3):
            tools.columnconfigure(column, weight=1, uniform="layout-tools")
        self._layout_edit_toggle = ttk.Button(
            tools,
            text="편집",
            command=self._toggle_edit_mode,
            style="ToggleOff.TButton",
        )
        self._layout_edit_toggle.grid(row=0, column=0, sticky="ew")
        self._auto_switch_toggle = ttk.Button(
            tools,
            text="자동 전환",
            command=self._toggle_auto_switch,
            style="ToggleOff.TButton",
        )
        self._auto_switch_toggle.grid(row=0, column=1, sticky="ew", padx=(10, 0))
        self._auto_switch_settings_button = ttk.Button(
            tools,
            text="자동 전환 설정",
            command=self._open_auto_switch_editor,
            style="Toolbar.TButton",
        )
        self._auto_switch_settings_button.grid(row=0, column=2, sticky="ew", padx=(10, 0))
        ttk.Label(self._frame, textvariable=self._vars["layout_hint"], style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0))

        content = ttk.Frame(self._frame, style="App.TFrame")
        content.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        content.columnconfigure(0, weight=4)
        content.columnconfigure(1, weight=2)
        content.rowconfigure(0, weight=1)
        canvas_shell = ttk.Frame(content, style="Surface.TFrame")
        canvas_shell.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        canvas_shell.columnconfigure(0, weight=1)
        canvas_shell.rowconfigure(1, weight=1)
        info = ttk.Frame(canvas_shell, style="Surface.TFrame")
        info.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        info.columnconfigure(0, weight=1)
        ttk.Label(info, textvariable=self._vars["lock_summary"], style="Surface.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(info, textvariable=self._vars["viewport"], style="SurfaceMuted.TLabel").grid(row=0, column=1, sticky="e")
        ttk.Label(info, textvariable=self._vars["selected_node"], style="SurfaceMuted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self._canvas = tk.Canvas(canvas_shell, width=980, height=620, highlightthickness=0, background=PALETTE["canvas"])
        self._canvas.grid(row=1, column=0, sticky="nsew")
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

        inspector = ttk.LabelFrame(content, text="선택된 PC", padding=12, style="Panel.TLabelframe")
        inspector.grid(row=0, column=1, sticky="nsew")
        inspector.columnconfigure(0, weight=1)
        ttk.Label(inspector, textvariable=self._vars["selection_title"], style="InspectorTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(inspector, textvariable=self._vars["selection_subtitle"], wraplength=320, style="SurfaceMuted.TLabel").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._selection_badge_frame = ttk.Frame(inspector, style="Surface.TFrame")
        self._selection_badge_frame.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self._selection_field_frame = ttk.Frame(inspector, style="Surface.TFrame")
        self._selection_field_frame.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        self._selection_field_frame.columnconfigure(1, weight=1)
        ttk.Label(inspector, textvariable=self._vars["selection_action"], style="SurfaceMuted.TLabel", wraplength=320).grid(row=4, column=0, sticky="w", pady=(10, 0))
        inspector_actions = ttk.Frame(inspector, style="Surface.TFrame")
        inspector_actions.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        inspector_actions.columnconfigure(0, weight=1)
        self._monitor_editor_button = ttk.Button(inspector_actions, text="모니터 맵 편집", command=self._open_monitor_editor, style="Primary.TButton")
        self._monitor_editor_button.grid(row=0, column=0, sticky="ew")
        view_tools = ttk.Frame(inspector_actions, style="Surface.TFrame")
        view_tools.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        for column in range(5):
            view_tools.columnconfigure(column, weight=1, uniform="view-tools")
        ttk.Button(view_tools, text="-", width=3, command=self._zoom_out, style="Toolbar.TButton").grid(row=0, column=0, sticky="ew")
        ttk.Button(view_tools, text="+", width=3, command=self._zoom_in, style="Toolbar.TButton").grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self._zoom_reset_button = ttk.Button(view_tools, text="100%", width=6, command=self._reset_zoom, style="Toolbar.TButton")
        self._zoom_reset_button.grid(row=0, column=2, sticky="ew", padx=(6, 0))
        self._fit_button = ttk.Button(view_tools, text="맞춤", width=6, command=self.fit_view, style="Toolbar.TButton")
        self._fit_button.grid(row=0, column=3, sticky="ew", padx=(6, 0))
        self._view_reset_button = ttk.Button(view_tools, text="초기화", width=10, command=self.reset_view, style="Toolbar.TButton")
        self._view_reset_button.grid(row=0, column=4, sticky="ew", padx=(6, 0))
        return self._frame

    def refresh(self, view):
        self._last_view = view
        self._sync_layout_draft()
        editor_id = None if self.coord_client is None else self.coord_client.get_layout_editor()
        pending = False if self.coord_client is None else self.coord_client.is_layout_edit_pending()
        is_editor = False if self.coord_client is None else self.coord_client.is_layout_editor()
        self.state.lock = LayoutLockState(editor_id=editor_id, is_editor=is_editor, pending=pending)
        if self.state.draft_layout is not None:
            if self.state.selected_node_id is None:
                self._set_selected_node_id(view.selected_target or view.self_id, notify=False)
            if self.state.selected_node_id is not None and self.state.draft_layout.get_node(self.state.selected_node_id) is None:
                self._set_selected_node_id(None, notify=False)
        self._vars["layout_edit"].set(is_editor or pending)
        self._vars["auto_switch_enabled"].set(self.state.auto_switch_enabled)
        self._vars["layout_hint"].set(build_layout_editor_hint(is_editor, self.state.auto_switch_enabled, editor_id, self.ctx.self_node.node_id, pending))
        self._vars["lock_summary"].set(build_layout_lock_text(editor_id, self.ctx.self_node.node_id, pending))
        self._vars["viewport"].set(build_viewport_summary(self.state.viewport.zoom, self.state.viewport.pan_x, self.state.viewport.pan_y))
        selected = None if self.state.draft_layout is None or self.state.selected_node_id is None else self.state.draft_layout.get_node(self.state.selected_node_id)
        self._vars["selected_node"].set(build_selected_node_text(selected))
        self._render_selection_inspector(view)
        self._update_toggle_buttons()
        self._set_widget_enabled(self._layout_edit_toggle, self.coord_client is not None and editor_id in (None, self.ctx.self_node.node_id))
        self._set_widget_enabled(self._auto_switch_toggle, self._can_edit_layout() and self.state.draft_layout is not None)
        self._set_widget_enabled(self._auto_switch_settings_button, self._can_edit_layout() and self.state.draft_layout is not None)
        self._set_widget_enabled(
            self._monitor_editor_button,
            self._can_edit_layout()
            and self.state.draft_layout is not None
            and self.state.selected_node_id is not None
            and selected is not None
            and selected.monitor_source != "fallback",
        )
        has_layout = self.state.draft_layout is not None
        self._set_widget_enabled(self._fit_button, has_layout)
        self._set_widget_enabled(self._zoom_reset_button, has_layout)
        self._set_widget_enabled(self._view_reset_button, has_layout)
        self.render(view)

    def render(self, view):
        if self._canvas is None:
            return
        signature = self._render_signature(view)
        if signature == self._last_render_signature:
            return
        self._last_render_signature = signature
        layout = self.state.draft_layout
        if layout is None:
            self._clear_layout_items()
            self._clear_background_grid()
            self._render_empty_state()
            return
        self._clear_empty_state()
        if not self._viewport_initialized and self._canvas_width and self._canvas_height:
            self.fit_view()
            return
        self._draw_background_grid()
        online = {peer.node_id: peer.online for peer in view.peers}
        state_by_target = {target.node_id: target for target in view.targets}
        current_node_id = view.selected_target or view.self_id
        self._render_layout_nodes(
            layout=layout,
            view=view,
            online=online,
            state_by_target=state_by_target,
            current_node_id=current_node_id,
        )

    def fit_view(self):
        if self.state.draft_layout is None or not self._canvas_width or not self._canvas_height:
            return
        bounds = layout_world_bounds(self.state.draft_layout, self._spec)
        self.state.viewport = fit_viewport(bounds, self._canvas_width, self._canvas_height, self._spec)
        self._viewport_initialized = True
        self._update_viewport_summary()
        self._invalidate_render_cache()
        self.render(self._fallback_view())

    def reset_view(self):
        if self.state.draft_layout is None or not self._canvas_width or not self._canvas_height:
            return
        bounds = layout_world_bounds(self.state.draft_layout, self._spec)
        self.state.viewport = center_viewport(bounds, self._canvas_width, self._canvas_height, self._spec, zoom=1.0)
        self._viewport_initialized = True
        self._update_viewport_summary()
        self._invalidate_render_cache()
        self.render(self._fallback_view())

    def close(self):
        if self._canvas is not None and self._configure_job is not None and hasattr(self._canvas, "after_cancel"):
            self._canvas.after_cancel(self._configure_job)
            self._configure_job = None
        self._close_auto_switch_editor()
        self._close_monitor_editor()

    def select_node(self, node_id: str | None, view=None):
        self._set_selected_node_id(node_id, notify=False)
        current_view = self._fallback_view() if view is None else view
        self._render_selection_inspector(current_view)
        self.render(current_view)

    def _fallback_view(self):
        if self._last_view is not None:
            return self._last_view
        return build_status_view(self.ctx, self.registry, self.coordinator_resolver, router=self.router, sink=self.sink)

    def _sync_layout_draft(self):
        if self.ctx.layout is None:
            return
        if self.state.drag.kind != "node" or self.coord_client is None or not self.coord_client.is_layout_editor():
            if self.state.draft_layout != self.ctx.layout:
                self.state.draft_layout = self.ctx.layout
                self.state.auto_switch_enabled = self.ctx.layout.auto_switch.enabled
                self._invalidate_render_cache()

    def _publish_layout(self, candidate: LayoutConfig, success_message: str, *, persist: bool = True) -> bool:
        previous = self.state.draft_layout
        self.state.draft_layout = candidate
        self.state.auto_switch_enabled = candidate.auto_switch.enabled
        self._invalidate_render_cache()
        if self.coord_client is None or not self.coord_client.publish_layout(candidate, persist=persist):
            self.state.draft_layout = self.ctx.layout or previous
            if self.state.draft_layout is not None:
                self.state.auto_switch_enabled = self.state.draft_layout.auto_switch.enabled
            self._invalidate_render_cache()
            self._set_message("레이아웃 변경을 전송하지 못했습니다.")
            return False
        self._set_message(success_message)
        return True

    def _render_selection_inspector(self, view):
        detail = build_layout_inspector_detail(
            None if self.state.draft_layout is None or self.state.selected_node_id is None else self.state.draft_layout.get_node(self.state.selected_node_id),
            node_id=self.state.selected_node_id,
            is_self=self.state.selected_node_id == view.self_id,
            is_online=self._is_selected_online(view),
            state=self._selected_target_state(view),
            can_edit=self._can_edit_layout(),
        )
        signature = (
            detail.title,
            detail.subtitle,
            tuple((badge.text, badge.tone) for badge in detail.badges),
            tuple((field.label, field.value) for field in detail.fields),
            detail.action_label,
        )
        if signature == self._last_inspector_signature:
            return
        self._last_inspector_signature = signature
        self._vars["selection_title"].set(detail.title)
        self._vars["selection_subtitle"].set(detail.subtitle)
        self._vars["selection_action"].set(detail.action_label)
        self._render_badges(self._selection_badge_frame, detail.badges)
        self._render_fields(self._selection_field_frame, detail.fields)

    def _render_badges(self, frame, badges):
        if frame is None:
            return
        import tkinter as tk

        for child in frame.winfo_children():
            child.destroy()
        for index, badge in enumerate(badges):
            badge_bg, badge_fg = palette_for_tone(badge.tone)
            tk.Label(frame, text=badge.text, bg=badge_bg, fg=badge_fg, padx=8, pady=3).grid(
                row=0, column=index, sticky="w", padx=(0, 6)
            )

    def _render_fields(self, frame, fields):
        if frame is None:
            return
        from tkinter import ttk

        for child in frame.winfo_children():
            child.destroy()
        for index, info in enumerate(fields):
            ttk.Label(frame, text=info.label, style="SurfaceMuted.TLabel").grid(
                row=index, column=0, sticky="w", pady=3, padx=(0, 10)
            )
            ttk.Label(frame, text=info.value, style="Surface.TLabel").grid(
                row=index, column=1, sticky="w", pady=3
            )

    def _update_viewport_summary(self):
        if "viewport" in self._vars:
            self._vars["viewport"].set(build_viewport_summary(self.state.viewport.zoom, self.state.viewport.pan_x, self.state.viewport.pan_y))

    def _on_canvas_configure(self, event):
        self._canvas_width = max(int(event.width), 1)
        self._canvas_height = max(int(event.height), 1)
        if self._canvas is not None and hasattr(self._canvas, "after_cancel") and self._configure_job is not None:
            self._canvas.after_cancel(self._configure_job)
        if self._canvas is not None and hasattr(self._canvas, "after"):
            self._configure_job = self._canvas.after(40, self._apply_canvas_configure)
            return
        self._apply_canvas_configure()

    def _apply_canvas_configure(self):
        self._configure_job = None
        self._invalidate_render_cache()
        if not self._viewport_initialized and self.state.draft_layout is not None:
            self.fit_view()
            return
        self._update_viewport_summary()
        self.render(self._fallback_view())

    def _on_canvas_press(self, event):
        if self._canvas is not None:
            self._canvas.focus_set()
        node_id = self._node_id_from_canvas_event()
        if node_id is not None:
            self._set_selected_node_id(node_id)
        if node_id is not None and self._can_edit_layout():
            node = None if self.state.draft_layout is None else self.state.draft_layout.get_node(node_id)
            if node is not None:
                self.state.drag = DragState(kind="node", node_id=node_id, origin_screen=(event.x, event.y), origin_grid=(node.x, node.y), start_layout=self.state.draft_layout)
            return
        if node_id is not None:
            self._activate_layout_node(node_id)
            return
        self.state.drag = DragState(kind="pan", origin_screen=(event.x, event.y), origin_pan=(self.state.viewport.pan_x, self.state.viewport.pan_y))

    def _on_canvas_drag(self, event):
        if self._canvas is None or self.state.drag.kind is None:
            return
        if self.state.drag.kind == "pan":
            if self.state.drag.origin_screen is None or self.state.drag.origin_pan is None:
                return
            dx = event.x - self.state.drag.origin_screen[0]
            dy = event.y - self.state.drag.origin_screen[1]
            self.state.viewport = ViewportState(zoom=self.state.viewport.zoom, pan_x=self.state.drag.origin_pan[0] + dx, pan_y=self.state.drag.origin_pan[1] + dy)
            self._update_viewport_summary()
            self._invalidate_render_cache()
            self.render(self._fallback_view())
            return
        if self.state.drag.kind != "node" or not self._can_edit_layout():
            return
        if None in (self.state.draft_layout, self.state.drag.node_id, self.state.drag.origin_screen, self.state.drag.origin_grid):
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
        candidate = replace_layout_node(self.state.draft_layout, self.state.drag.node_id, x=grid_x, y=grid_y)
        overlaps = [pair for pair in find_overlapping_nodes(candidate) if self.state.drag.node_id in pair]
        if overlaps:
            self._set_message("겹치는 배치는 사용할 수 없습니다.")
            return
        if self._publish_layout(candidate, "레이아웃 미리보기를 반영했습니다.", persist=False):
            self.state.drag.preview_dirty = True
            self.render(self._fallback_view())

    def _on_canvas_release(self, _event):
        if self.state.drag.kind == "node" and self.state.drag.preview_dirty and self.state.draft_layout is not None and self.state.draft_layout != self.state.drag.start_layout:
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
        self.state.viewport = zoom_at_point(self.state.viewport, factor=factor, anchor_screen_x=event.x, anchor_screen_y=event.y, spec=self._spec)
        self._viewport_initialized = True
        self._update_viewport_summary()
        self._invalidate_render_cache()
        self.render(self._fallback_view())

    def _on_escape(self, _event):
        if self.state.drag.kind == "node" and self.state.drag.preview_dirty:
            if self.state.drag.start_layout is not None:
                self._publish_layout(self.state.drag.start_layout, "레이아웃 미리보기를 되돌렸습니다.", persist=False)
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
        self.state.viewport = zoom_at_point(self.state.viewport, factor=1.1, anchor_screen_x=anchor_x, anchor_screen_y=anchor_y, spec=self._spec)
        self._viewport_initialized = True
        self._update_viewport_summary()
        self._invalidate_render_cache()
        self.render(self._fallback_view())

    def _zoom_out(self):
        if self.state.draft_layout is None:
            return
        anchor_x = self._canvas_width / 2.0 if self._canvas_width else 0.0
        anchor_y = self._canvas_height / 2.0 if self._canvas_height else 0.0
        self.state.viewport = zoom_at_point(self.state.viewport, factor=1 / 1.1, anchor_screen_x=anchor_x, anchor_screen_y=anchor_y, spec=self._spec)
        self._viewport_initialized = True
        self._update_viewport_summary()
        self._invalidate_render_cache()
        self.render(self._fallback_view())

    def _reset_zoom(self):
        if self.state.draft_layout is None or not self._canvas_width or not self._canvas_height:
            return
        bounds = layout_world_bounds(self.state.draft_layout, self._spec)
        self.state.viewport = center_viewport(bounds, self._canvas_width, self._canvas_height, self._spec, zoom=1.0)
        self._viewport_initialized = True
        self._update_viewport_summary()
        self._invalidate_render_cache()
        self.render(self._fallback_view())

    def _toggle_edit_mode(self):
        self._vars["layout_edit"].set(not self._vars["layout_edit"].get())
        self._update_toggle_buttons()
        self._on_edit_mode_changed()

    def _on_edit_mode_changed(self):
        if self.coord_client is None:
            self._vars["layout_edit"].set(False)
            self._update_toggle_buttons()
            self._set_message("편집 기능을 사용할 수 없습니다.")
            return
        if self._vars["layout_edit"].get():
            editor = self.coord_client.get_layout_editor()
            if editor not in (None, self.ctx.self_node.node_id):
                self._vars["layout_edit"].set(False)
                self._update_toggle_buttons()
                self._set_message(f"{editor} PC가 이미 편집 중입니다.")
                return
            self.coord_client.request_layout_edit()
            pending = self.coord_client.is_layout_edit_pending()
            self._vars["layout_hint"].set(
                build_layout_editor_hint(
                    False,
                    self._vars["auto_switch_enabled"].get(),
                    self.coord_client.get_layout_editor(),
                    self.ctx.self_node.node_id,
                    pending,
                )
            )
            self._vars["lock_summary"].set(
                build_layout_lock_text(
                    self.coord_client.get_layout_editor(),
                    self.ctx.self_node.node_id,
                    pending,
                )
            )
            self._update_toggle_buttons()
            self._set_message("편집 권한을 요청했습니다.")
            return
        self.state.drag.clear()
        self.close()
        self.coord_client.end_layout_edit()
        self._update_toggle_buttons()
        self._set_message("편집 모드를 종료했습니다.")
        self._invalidate_render_cache()

    def _toggle_auto_switch(self):
        self._vars["auto_switch_enabled"].set(not self._vars["auto_switch_enabled"].get())
        self._update_toggle_buttons()
        self._on_auto_switch_toggled()

    def _on_auto_switch_toggled(self):
        if self.state.draft_layout is None:
            return
        if not self._can_edit_layout():
            self._vars["auto_switch_enabled"].set(self.state.draft_layout.auto_switch.enabled)
            self._set_message("편집 권한이 있는 노드만 자동 전환을 바꿀 수 있습니다.")
            return
        candidate = replace_auto_switch_settings(
            self.state.draft_layout,
            enabled=self._vars["auto_switch_enabled"].get(),
        )
        if not self._publish_layout(candidate, "자동 전환 설정을 반영했습니다."):
            self._vars["auto_switch_enabled"].set(self.state.draft_layout.auto_switch.enabled)
            self._update_toggle_buttons()

    def _open_auto_switch_editor(self):
        if not self._can_edit_layout() or self.state.draft_layout is None:
            self._set_message("편집 권한이 있을 때만 자동 전환 설정을 바꿀 수 있습니다.")
            return
        self._close_auto_switch_editor()
        self._auto_switch_dialog = AutoSwitchDialog(
            self._frame.winfo_toplevel(),
            self._current_layout,
            self._publish_layout,
        )

    def _open_monitor_editor(self):
        if not self._can_edit_layout() or self.state.draft_layout is None or self.state.selected_node_id is None:
            self._set_message("편집 모드에서 선택된 PC가 있어야 모니터 맵을 수정할 수 있습니다.")
            return
        selected = self.state.draft_layout.get_node(self.state.selected_node_id)
        if selected is None or selected.monitor_source == "fallback":
            self._set_message("실제 모니터 감지 정보가 있는 PC만 모니터 맵을 수정할 수 있습니다.")
            return
        self._close_monitor_editor()
        self._monitor_dialog = MonitorMapDialog(
            self._frame.winfo_toplevel(),
            self.state.selected_node_id,
            self._current_layout,
            self._publish_layout,
            inventory_provider=lambda node_id: self.ctx.get_monitor_inventory(node_id),
            refresh_inventory=self._refresh_monitor_inventory,
        )

    def _apply_row_preset(self):
        if self.state.draft_layout is None or self.state.selected_node_id is None:
            return
        selected = self.state.draft_layout.get_node(self.state.selected_node_id)
        display_count = 1 if selected is None else max(len(selected.monitors().physical), 1)
        self._apply_monitor_preset(display_count, 1)

    def _apply_monitor_preset(self, width: int, height: int):
        if not self._can_edit_layout() or self.state.draft_layout is None or self.state.selected_node_id is None:
            self._set_message("편집 모드에서 선택된 PC가 있어야 프리셋을 적용할 수 있습니다.")
            return
        preset = build_monitor_preset(width, height)
        rows = [list(row[:width]) for row in preset.cells[:height]]
        candidate = replace_layout_monitors(
            self.state.draft_layout,
            self.state.selected_node_id,
            logical_rows=rows,
            physical_rows=rows,
        )
        overlaps = [pair for pair in find_overlapping_nodes(candidate) if self.state.selected_node_id in pair]
        if overlaps:
            self._set_message("프리셋 적용 결과가 다른 PC와 겹쳐 사용할 수 없습니다.")
            return
        if self._publish_layout(candidate, f"모니터 프리셋 {width}x{height}를 적용했습니다."):
            self.render(self._fallback_view())

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
                self._set_message("이 PC 제어로 돌아왔습니다.")
            return
        node = self.ctx.get_node(node_id)
        if node is None:
            self._set_message(f"{node_id} PC 정보를 찾을 수 없습니다.")
            return
        if not node.has_role("target"):
            self._set_message(f"{node_id} PC는 대상 전환을 지원하지 않습니다.")
            return
        online_ids = {peer.node_id for peer in self._fallback_view().peers if peer.online}
        if node_id not in online_ids:
            self._set_message(f"{node_id} PC가 아직 연결되지 않았습니다.")
            return
        if self.coord_client is not None:
            thread = threading.Thread(
                target=self.coord_client.request_target,
                args=(node_id,),
                daemon=True,
                name=f"layout-target-{node_id}",
            )
            thread.start()
            self._set_message(f"{node_id} PC로 전환을 요청했습니다.")

    def _draw_background_grid(self):
        if self._canvas is None or not self._canvas_width or not self._canvas_height:
            return
        signature = (
            round(self.state.viewport.zoom, 4),
            round(self.state.viewport.pan_x, 2),
            round(self.state.viewport.pan_y, 2),
            self._canvas_width,
            self._canvas_height,
        )
        if signature == self._background_signature:
            return
        self._background_signature = signature
        self._canvas.delete("bg-grid")
        world_left, world_top = screen_to_world(0, 0, self.state.viewport)
        world_right, world_bottom = screen_to_world(self._canvas_width, self._canvas_height, self.state.viewport)
        pitch_x = self._spec.grid_pitch_x
        pitch_y = self._spec.grid_pitch_y
        start_x = math.floor(world_left / pitch_x) * pitch_x
        start_y = math.floor(world_top / pitch_y) * pitch_y
        x = start_x
        while x <= world_right:
            sx1, sy1 = world_to_screen(x, world_top, self.state.viewport)
            sx2, sy2 = world_to_screen(x, world_bottom, self.state.viewport)
            self._canvas.create_line(sx1, sy1, sx2, sy2, fill="#edf1f6", tags=("bg-grid",))
            x += pitch_x
        y = start_y
        while y <= world_bottom:
            sx1, sy1 = world_to_screen(world_left, y, self.state.viewport)
            sx2, sy2 = world_to_screen(world_right, y, self.state.viewport)
            self._canvas.create_line(sx1, sy1, sx2, sy2, fill="#edf1f6", tags=("bg-grid",))
            y += pitch_y

    def _draw_monitor_overlays(self, node, x1, y1, x2, y2, outline):
        topology = node.monitors()
        if node.monitor_source == "fallback" or len(topology.physical) <= 1:
            return
        rows = monitor_topology_to_rows(topology, logical=False)
        grid_h = len(rows)
        grid_w = max(len(row) for row in rows)
        for display in topology.physical:
            dx1 = x1 + (display.x / grid_w) * (x2 - x1)
            dy1 = y1 + (display.y / grid_h) * (y2 - y1)
            dx2 = x1 + ((display.x + display.width) / grid_w) * (x2 - x1)
            dy2 = y1 + ((display.y + display.height) / grid_h) * (y2 - y1)
            rect_id = self._canvas.create_rectangle(dx1 + 6, dy1 + 6, dx2 - 6, dy2 - 6, outline=outline, width=1, dash=(3, 2), tags=("layout-node", f"node:{node.node_id}"))
            text_id = self._canvas.create_text((dx1 + dx2) / 2, (dy1 + dy2) / 2, text=display.display_id, fill=outline, tags=("layout-node", f"node:{node.node_id}"))
            self._layout_item_to_node_id[rect_id] = node.node_id
            self._layout_item_to_node_id[text_id] = node.node_id

    def _node_id_from_canvas_event(self):
        item = None if self._canvas is None else self._canvas.find_withtag("current")
        return None if not item else self._layout_item_to_node_id.get(item[0])

    def _can_edit_layout(self) -> bool:
        return self.coord_client is not None and self.coord_client.is_layout_editor() and self._vars["layout_edit"].get()

    def _set_selected_node_id(self, node_id: str | None, *, notify: bool = True):
        self.state.selected_node_id = node_id
        self._last_inspector_signature = None
        self._invalidate_render_cache()
        if notify:
            self._on_select_node(node_id)

    def _is_selected_online(self, view) -> bool:
        if self.state.selected_node_id is None:
            return False
        if self.state.selected_node_id == view.self_id:
            return True
        return any(peer.node_id == self.state.selected_node_id and peer.online for peer in view.peers)

    def _selected_target_state(self, view) -> str | None:
        if self.state.selected_node_id is None:
            return None
        for target in view.targets:
            if target.node_id == self.state.selected_node_id:
                return target.state
        return None

    def _set_widget_enabled(self, widget, enabled: bool):
        if widget is not None and hasattr(widget, "state"):
            widget.state(["!disabled"] if enabled else ["disabled"])

    def _update_toggle_buttons(self):
        edit_on = self._vars["layout_edit"].get()
        auto_on = self._vars["auto_switch_enabled"].get()
        if hasattr(self._layout_edit_toggle, "configure"):
            self._layout_edit_toggle.configure(
                text="편집",
                style="ToggleOn.TButton" if edit_on else "ToggleOff.TButton",
            )
        if hasattr(self._auto_switch_toggle, "configure"):
            self._auto_switch_toggle.configure(
                text="자동 전환",
                style="ToggleOn.TButton" if auto_on else "ToggleOff.TButton",
            )

    def _refresh_monitor_inventory(self, node_id: str):
        if (
            self.monitor_inventory_manager is not None
            and node_id == self.ctx.self_node.node_id
        ):
            started = self.monitor_inventory_manager.refresh_async()
            snapshot = self.ctx.get_monitor_inventory(node_id)
            message = (
                "로컬 모니터를 다시 감지하는 중입니다."
                if started
                else "로컬 모니터 재감지가 이미 진행 중입니다."
            )
            return snapshot, message
        if self.coord_client is None:
            return self.ctx.get_monitor_inventory(node_id), "원격 재감지를 요청할 수 없습니다."
        sent = self.coord_client.request_monitor_inventory_refresh(node_id)
        refresh_state = self.coord_client.get_monitor_inventory_refresh_state(node_id) or {}
        if sent:
            message = refresh_state.get("detail") or "원격 PC에 모니터 재감지를 요청했습니다."
        else:
            message = refresh_state.get("detail") or "원격 PC에 모니터 재감지를 요청하지 못했습니다."
        return self.ctx.get_monitor_inventory(node_id), message

    def _render_signature(self, view):
        return (
            self._layout_signature(self.state.draft_layout),
            self.state.selected_node_id,
            view.selected_target,
            tuple((peer.node_id, peer.online) for peer in view.peers),
            tuple((target.node_id, target.state) for target in view.targets),
            round(self.state.viewport.zoom, 4),
            round(self.state.viewport.pan_x, 2),
            round(self.state.viewport.pan_y, 2),
            self._canvas_width,
            self._canvas_height,
        )

    def _layout_signature(self, layout: LayoutConfig | None):
        if layout is None:
            return None
        nodes = []
        for node in layout.nodes:
            logical = tuple((display.display_id, display.x, display.y, display.width, display.height) for display in node.monitors().logical)
            physical = tuple((display.display_id, display.x, display.y, display.width, display.height) for display in node.monitors().physical)
            nodes.append((node.node_id, node.x, node.y, node.width, node.height, logical, physical))
        return tuple(nodes), layout.auto_switch.enabled

    def _invalidate_render_cache(self):
        self._last_render_signature = None
        self._background_signature = None

    def _set_message(self, message: str):
        self._on_message(message)

    def _render_empty_state(self):
        if self._canvas is None:
            return
        x = max(self._canvas_width / 2, 100)
        y = max(self._canvas_height / 2, 100)
        if self._empty_text_id is None:
            self._empty_text_id = self._canvas.create_text(
                x,
                y,
                text="레이아웃 정보를 사용할 수 없습니다.",
                fill=PALETTE["muted"],
            )
            return
        self._canvas.coords(self._empty_text_id, x, y)

    def _clear_empty_state(self):
        if self._canvas is None or self._empty_text_id is None:
            return
        self._canvas.delete(self._empty_text_id)
        self._empty_text_id = None

    def _clear_background_grid(self):
        if self._canvas is None:
            return
        self._canvas.delete("bg-grid")
        self._background_signature = None

    def _clear_layout_items(self):
        if self._canvas is None:
            return
        for node_id in list(self._node_items):
            self._remove_node_items(node_id)
        self._layout_item_to_node_id.clear()

    def _render_layout_nodes(self, *, layout, view, online, state_by_target, current_node_id):
        seen = set()
        for node in layout.nodes:
            seen.add(node.node_id)
            bounds = node_world_bounds(node, self._spec)
            x1, y1 = world_to_screen(bounds.left, bounds.top, self.state.viewport)
            x2, y2 = world_to_screen(bounds.right, bounds.bottom, self.state.viewport)
            target_view = state_by_target.get(node.node_id)
            state = None if target_view is None else target_view.state
            is_self = node.node_id == view.self_id
            is_online = True if is_self else online.get(node.node_id, False)
            is_selected = node.node_id == current_node_id
            fill, outline = build_layout_node_colors(
                is_self=is_self,
                is_online=is_online,
                is_selected=is_selected,
                state=state,
            )
            width = 4 if node.node_id == self.state.selected_node_id else 3 if is_selected else 2
            label = build_layout_node_label(
                node.node_id,
                is_self=is_self,
                is_online=is_online,
                is_selected=is_selected,
                state=state,
            )
            node_signature = (
                round(x1, 1),
                round(y1, 1),
                round(x2, 1),
                round(y2, 1),
                fill,
                outline,
                width,
                label,
                max(x2 - x1 - 8, 16),
            )
            items = self._node_items.get(node.node_id)
            if items is None:
                items = self._create_node_items(node.node_id)
            if items["signature"] != node_signature:
                self._canvas.coords(items["rect"], x1, y1, x2, y2)
                self._canvas.itemconfigure(
                    items["rect"],
                    fill=fill,
                    outline=outline,
                    width=width,
                )
                self._canvas.coords(
                    items["text"],
                    (x1 + x2) / 2,
                    y1 + min((y2 - y1) * 0.28, 24),
                )
                self._canvas.itemconfigure(
                    items["text"],
                    text=label,
                    width=max(x2 - x1 - 8, 16),
                )
                items["signature"] = node_signature
            self._sync_monitor_overlays(items, node, x1, y1, x2, y2, outline)
        for node_id in list(self._node_items):
            if node_id not in seen:
                self._remove_node_items(node_id)

    def _create_node_items(self, node_id: str):
        rect_id = self._canvas.create_rectangle(
            0,
            0,
            0,
            0,
            tags=("layout-node", f"node:{node_id}"),
        )
        text_id = self._canvas.create_text(
            0,
            0,
            justify="center",
            tags=("layout-node", f"node:{node_id}"),
        )
        self._layout_item_to_node_id[rect_id] = node_id
        self._layout_item_to_node_id[text_id] = node_id
        items = {
            "rect": rect_id,
            "text": text_id,
            "overlay_ids": [],
            "signature": None,
            "overlay_signature": None,
        }
        self._node_items[node_id] = items
        return items

    def _sync_monitor_overlays(self, items, node, x1, y1, x2, y2, outline):
        topology = node.monitors()
        physical = tuple(
            (display.display_id, display.x, display.y, display.width, display.height)
            for display in topology.physical
        )
        overlay_signature = (
            node.monitor_source,
            physical,
            round(x1, 1),
            round(y1, 1),
            round(x2, 1),
            round(y2, 1),
            outline,
        )
        if items["overlay_signature"] == overlay_signature:
            return
        self._delete_overlay_items(items)
        if node.monitor_source == "fallback" or len(topology.physical) <= 1:
            items["overlay_signature"] = overlay_signature
            return
        rows = monitor_topology_to_rows(topology, logical=False)
        grid_h = len(rows)
        grid_w = max(len(row) for row in rows)
        overlay_ids = []
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
            overlay_ids.extend((rect_id, text_id))
            self._layout_item_to_node_id[rect_id] = node.node_id
            self._layout_item_to_node_id[text_id] = node.node_id
        items["overlay_ids"] = overlay_ids
        items["overlay_signature"] = overlay_signature

    def _delete_overlay_items(self, items):
        if self._canvas is None:
            return
        for item_id in items.get("overlay_ids", ()):
            self._layout_item_to_node_id.pop(item_id, None)
            self._canvas.delete(item_id)
        items["overlay_ids"] = []

    def _remove_node_items(self, node_id: str):
        if self._canvas is None:
            return
        items = self._node_items.pop(node_id, None)
        if items is None:
            return
        self._delete_overlay_items(items)
        for key in ("rect", "text"):
            item_id = items.get(key)
            if item_id is None:
                continue
            self._layout_item_to_node_id.pop(item_id, None)
            self._canvas.delete(item_id)
