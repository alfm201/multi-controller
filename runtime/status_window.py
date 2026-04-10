"""User-facing status window shell."""

from __future__ import annotations

from datetime import datetime

from runtime.gui_style import PALETTE, apply_gui_theme, palette_for_tone
from runtime.layout_editor import LayoutEditor
from runtime.status_view import (
    build_advanced_peer_text,
    build_connection_summary_text,
    build_primary_status_text,
    build_selection_hint_text,
    build_status_view,
)


class StatusWindow:
    """Notebook-based status window for runtime monitoring and layout editing."""

    def __init__(
        self,
        ctx,
        registry,
        coordinator_resolver,
        router=None,
        sink=None,
        coord_client=None,
        config_reloader=None,
        refresh_ms=500,
    ):
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
        self._target_frame = None
        self._overview_inspector_frame = None
        self._connection_inspector_frame = None
        self._peer_frame = None
        self._peer_tree = None
        self._advanced_runtime_frame = None
        self._advanced_peer_frame = None
        self._advanced_peer_var = None
        self._summary_cards_frame = None
        self._selected_node_id = None
        self._last_seen = {}
        self._summary_card_widgets = []
        self._target_widgets = {}
        self._detail_widgets = {}
        self._advanced_runtime_widgets = {}
        self._advanced_peer_widgets = {}
        self._peer_signature = None
        self._advanced_peer_signature = None
        self._layout_editor = LayoutEditor(
            ctx,
            registry,
            coordinator_resolver,
            router=router,
            sink=sink,
            coord_client=coord_client,
            on_message=self._set_message,
            on_select_node=self._set_selected_node,
        )
        self._on_close = None

    def run(self, on_close):
        import tkinter as tk
        from tkinter import ttk

        self._on_close = on_close
        self._root = tk.Tk()
        apply_gui_theme(self._root)
        self._root.title(f"multi-controller [{self.ctx.self_node.node_id}]")
        self._root.geometry("1240x920")
        self._root.minsize(1040, 760)
        self._root.protocol("WM_DELETE_WINDOW", self._handle_close)

        frame = ttk.Frame(self._root, padding=16, style="App.TFrame")
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        self._vars["headline"] = tk.StringVar()
        self._vars["summary"] = tk.StringVar()
        self._vars["hint"] = tk.StringVar()
        self._vars["self_id"] = tk.StringVar()
        self._vars["coordinator"] = tk.StringVar()
        self._vars["router"] = tk.StringVar()
        self._vars["lease"] = tk.StringVar()
        self._vars["config_path"] = tk.StringVar()
        self._vars["message"] = tk.StringVar()

        ttk.Label(
            frame,
            text=f"Self PC: {self.ctx.self_node.node_id}",
            style="Heading.TLabel",
        ).grid(row=0, column=0, sticky="w")

        notebook = ttk.Notebook(frame)
        notebook.grid(row=1, column=0, sticky="nsew", pady=(12, 0))

        overview_tab = ttk.Frame(notebook, padding=14, style="App.TFrame")
        layout_tab = ttk.Frame(notebook, style="App.TFrame")
        connection_tab = ttk.Frame(notebook, padding=14, style="App.TFrame")
        advanced_tab = ttk.Frame(notebook, padding=14, style="App.TFrame")

        notebook.add(overview_tab, text="Overview")
        notebook.add(layout_tab, text="Layout")
        notebook.add(connection_tab, text="Connections")
        notebook.add(advanced_tab, text="Advanced")

        self._build_overview_tab(overview_tab, ttk, notebook)
        self._build_connection_tab(connection_tab, ttk)
        self._build_advanced_tab(advanced_tab, ttk)
        layout_tab.columnconfigure(0, weight=1)
        layout_tab.rowconfigure(0, weight=1)
        self._layout_editor.build(layout_tab).grid(row=0, column=0, sticky="nsew")

        ttk.Label(
            frame,
            textvariable=self._vars["message"],
            style="Muted.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(10, 0))

        self._refresh()
        self._root.mainloop()

    def _build_overview_tab(self, tab, ttk, notebook):
        tab.columnconfigure(0, weight=3)
        tab.columnconfigure(1, weight=2)
        tab.rowconfigure(5, weight=1)

        ttk.Label(tab, textvariable=self._vars["headline"], style="Heading.TLabel").grid(
            row=0,
            column=0,
            columnspan=2,
            sticky="w",
        )
        ttk.Label(tab, textvariable=self._vars["summary"]).grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(6, 0),
        )
        ttk.Label(
            tab,
            textvariable=self._vars["hint"],
            style="Muted.TLabel",
            wraplength=920,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self._summary_cards_frame = ttk.Frame(tab, style="App.TFrame")
        self._summary_cards_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        for index in range(4):
            self._summary_cards_frame.columnconfigure(index, weight=1)

        actions = ttk.Frame(tab, style="Toolbar.TFrame")
        actions.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        if self.config_reloader is not None:
            ttk.Button(actions, text="Config Reload", command=self._reload_config).pack(
                side="left"
            )
        if self.coord_client is not None:
            ttk.Button(actions, text="Clear Target", command=self._clear_target).pack(
                side="left",
                padx=(8, 0),
            )
        ttk.Button(
            actions,
            text="Go To Layout",
            command=lambda: notebook.select(1),
        ).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Close", command=self._handle_close).pack(side="right")

        ttk.Label(tab, text="Quick Targets", style="Muted.TLabel").grid(
            row=5,
            column=0,
            sticky="w",
            pady=(18, 0),
        )
        self._target_frame = ttk.Frame(tab, style="App.TFrame")
        self._target_frame.grid(row=6, column=0, sticky="nsew", pady=(8, 0), padx=(0, 12))
        self._target_frame.columnconfigure(0, weight=1)

        self._overview_inspector_frame = ttk.LabelFrame(
            tab,
            text="Selection",
            padding=12,
            style="Panel.TLabelframe",
        )
        self._overview_inspector_frame.grid(row=5, column=1, rowspan=2, sticky="nsew")
        self._overview_inspector_frame.columnconfigure(1, weight=1)

    def _build_connection_tab(self, tab, ttk):
        tab.columnconfigure(0, weight=3)
        tab.columnconfigure(1, weight=2)
        tab.rowconfigure(1, weight=1)
        ttk.Label(
            tab,
            text="Connected peers are grouped in a stable table so the window stops flashing.",
            style="Muted.TLabel",
            wraplength=920,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        self._peer_frame = ttk.Frame(tab, style="Surface.TFrame")
        self._peer_frame.grid(row=1, column=0, sticky="nsew", pady=(12, 0), padx=(0, 12))
        self._peer_frame.columnconfigure(0, weight=1)
        self._peer_frame.rowconfigure(0, weight=1)

        self._peer_tree = ttk.Treeview(
            self._peer_frame,
            columns=("status", "roles", "layout", "display", "last_seen"),
            show="headings",
            selectmode="browse",
        )
        self._peer_tree.heading("status", text="Status")
        self._peer_tree.heading("roles", text="Roles")
        self._peer_tree.heading("layout", text="Layout")
        self._peer_tree.heading("display", text="Displays")
        self._peer_tree.heading("last_seen", text="Last Seen")
        self._peer_tree.column("status", width=140, anchor="center")
        self._peer_tree.column("roles", width=170, anchor="w")
        self._peer_tree.column("layout", width=100, anchor="center")
        self._peer_tree.column("display", width=80, anchor="center")
        self._peer_tree.column("last_seen", width=100, anchor="center")
        self._peer_tree.grid(row=0, column=0, sticky="nsew")
        self._peer_tree.tag_configure("offline", foreground=PALETTE["danger_fg"])
        self._peer_tree.tag_configure("warning", foreground=PALETTE["warning_fg"])
        self._peer_tree.bind("<<TreeviewSelect>>", self._on_peer_tree_select)

        self._connection_inspector_frame = ttk.LabelFrame(
            tab,
            text="Selection",
            padding=12,
            style="Panel.TLabelframe",
        )
        self._connection_inspector_frame.grid(row=1, column=1, sticky="nsew", pady=(12, 0))
        self._connection_inspector_frame.columnconfigure(1, weight=1)

    def _build_advanced_tab(self, tab, ttk):
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)
        runtime_box = ttk.LabelFrame(tab, text="Runtime", padding=12, style="Panel.TLabelframe")
        runtime_box.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        runtime_box.columnconfigure(1, weight=1)
        peer_box = ttk.LabelFrame(tab, text="Peer Detail", padding=12, style="Panel.TLabelframe")
        peer_box.grid(row=0, column=1, sticky="nsew")
        peer_box.columnconfigure(0, weight=1)
        self._advanced_runtime_frame = runtime_box
        self._advanced_peer_frame = peer_box

    def _refresh(self):
        if self._root is None:
            return
        view = self._current_status_view()
        self._vars["headline"].set(build_primary_status_text(view))
        self._vars["summary"].set(build_connection_summary_text(view))
        self._vars["hint"].set(build_selection_hint_text(view))
        self._vars["self_id"].set(view.self_id)
        self._vars["coordinator"].set(view.coordinator_id or "-")
        self._vars["router"].set(f"{view.router_state or '-'} / {view.selected_target or '-'}")
        self._vars["lease"].set(view.authorized_controller or "-")
        self._vars["config_path"].set(view.config_path or "-")
        if self._selected_node_id is None:
            self._selected_node_id = view.selected_target or view.self_id

        self._render_summary_cards(view.summary_cards)
        self._render_targets(view.targets)
        self._render_peers(view.peers)
        self._render_selected_detail(view)
        self._render_advanced_runtime()
        self._render_advanced_peers(view.peers)
        self._layout_editor.refresh(view)
        self._root.after(self.refresh_ms, self._refresh)

    def _current_status_view(self):
        refreshed_at = datetime.now().strftime("%H:%M:%S")
        self._last_seen[self.ctx.self_node.node_id] = refreshed_at
        for node_id, conn in self.registry.all():
            if conn and not conn.closed:
                self._last_seen[node_id] = refreshed_at
        return build_status_view(
            self.ctx,
            self.registry,
            self.coordinator_resolver,
            router=self.router,
            sink=self.sink,
            last_seen=self._last_seen,
        )

    def _render_targets(self, targets):
        import tkinter as tk
        from tkinter import ttk

        if self._target_frame is None:
            return

        seen = set()
        if not targets:
            if "__empty__" not in self._target_widgets:
                label = ttk.Label(self._target_frame, text="No target PC is available.")
                label.grid(row=0, column=0, sticky="w")
                self._target_widgets["__empty__"] = {"widget": label}
            return
        empty = self._target_widgets.pop("__empty__", None)
        if empty is not None:
            empty["widget"].destroy()

        for index, target in enumerate(targets):
            seen.add(target.node_id)
            widgets = self._target_widgets.get(target.node_id)
            if widgets is None:
                card = tk.Frame(
                    self._target_frame,
                    bg=PALETTE["surface"],
                    bd=1,
                    relief="solid",
                    highlightthickness=0,
                )
                card.grid_columnconfigure(0, weight=1)
                title_var = tk.StringVar()
                subtitle_var = tk.StringVar()
                meta_var = tk.StringVar()
                title = tk.Label(
                    card,
                    textvariable=title_var,
                    bg=PALETTE["surface"],
                    fg=PALETTE["text"],
                    font=("", 11, "bold"),
                )
                subtitle = tk.Label(
                    card,
                    textvariable=subtitle_var,
                    bg=PALETTE["surface"],
                    fg=PALETTE["muted"],
                )
                meta = tk.Label(
                    card,
                    textvariable=meta_var,
                    bg=PALETTE["surface"],
                    fg=PALETTE["muted"],
                )
                badge_row = tk.Frame(card, bg=PALETTE["surface"])
                action = ttk.Button(
                    card,
                    text="Switch",
                    command=lambda node_id=target.node_id: self._request_target(node_id),
                )
                title.grid(row=0, column=0, sticky="w", padx=10, pady=(8, 0))
                subtitle.grid(row=1, column=0, sticky="w", padx=10)
                meta.grid(row=2, column=0, sticky="w", padx=10)
                badge_row.grid(row=3, column=0, sticky="w", padx=10, pady=(6, 8))
                action.grid(row=0, column=1, rowspan=4, sticky="ns", padx=10, pady=8)
                self._bind_select_node(card, target.node_id)
                self._bind_select_node(title, target.node_id)
                self._bind_select_node(subtitle, target.node_id)
                self._bind_select_node(meta, target.node_id)
                widgets = {
                    "card": card,
                    "title_var": title_var,
                    "subtitle_var": subtitle_var,
                    "meta_var": meta_var,
                    "badge_row": badge_row,
                    "action": action,
                }
                self._target_widgets[target.node_id] = widgets
            widgets["card"].grid(row=index, column=0, sticky="ew", pady=4)
            widgets["title_var"].set(target.node_id)
            widgets["subtitle_var"].set(target.subtitle)
            widgets["meta_var"].set(
                f"layout {target.layout_summary} | displays {target.display_count}"
            )
            self._set_widget_enabled(widgets["action"], target.online)
            self._sync_badges(widgets["badge_row"], target.badges, background=PALETTE["surface"])

        removed = [node_id for node_id in self._target_widgets if node_id not in seen]
        for node_id in removed:
            self._target_widgets[node_id]["card"].destroy()
            del self._target_widgets[node_id]

    def _render_peers(self, peers):
        if self._peer_tree is None:
            return
        signature = tuple(
            (
                peer.node_id,
                peer.online,
                peer.is_authorized_controller,
                peer.role_summary,
                peer.layout_summary,
                peer.display_count,
                peer.last_seen,
            )
            for peer in peers
        )
        if signature == self._peer_signature:
            return
        self._peer_signature = signature

        existing = set(self._peer_tree.get_children())
        for peer in peers:
            status = "Connected" if peer.online else "Offline"
            if peer.is_authorized_controller:
                status = f"{status} / Lease"
            tags = ()
            if not peer.online:
                tags = ("offline",)
            elif peer.is_authorized_controller:
                tags = ("warning",)
            values = (
                status,
                peer.role_summary,
                peer.layout_summary,
                peer.display_count,
                peer.last_seen,
            )
            if self._peer_tree.exists(peer.node_id):
                self._peer_tree.item(peer.node_id, values=values, tags=tags)
            else:
                self._peer_tree.insert("", "end", iid=peer.node_id, values=values, tags=tags)
            existing.discard(peer.node_id)
        for node_id in existing:
            self._peer_tree.delete(node_id)
        if self._selected_node_id and self._peer_tree.exists(self._selected_node_id):
            self._peer_tree.selection_set(self._selected_node_id)
            self._peer_tree.focus(self._selected_node_id)

    def _render_summary_cards(self, cards):
        import tkinter as tk

        if self._summary_cards_frame is None:
            return

        while len(self._summary_card_widgets) < len(cards):
            card = tk.Frame(
                self._summary_cards_frame,
                bg=PALETTE["surface"],
                bd=1,
                relief="solid",
                highlightthickness=0,
            )
            title_var = tk.StringVar()
            value_var = tk.StringVar()
            detail_var = tk.StringVar()
            tk.Label(card, textvariable=title_var, anchor="w").grid(
                row=0, column=0, sticky="w", padx=10, pady=(10, 0)
            )
            tk.Label(card, textvariable=value_var, anchor="w", font=("", 13, "bold")).grid(
                row=1, column=0, sticky="w", padx=10, pady=(4, 0)
            )
            tk.Label(card, textvariable=detail_var, anchor="w", justify="left", wraplength=210).grid(
                row=2, column=0, sticky="w", padx=10, pady=(4, 10)
            )
            self._summary_card_widgets.append(
                {
                    "frame": card,
                    "title_var": title_var,
                    "value_var": value_var,
                    "detail_var": detail_var,
                }
            )

        for index, card_data in enumerate(cards):
            widgets = self._summary_card_widgets[index]
            background, foreground = palette_for_tone(card_data.tone)
            widgets["frame"].grid(
                row=0,
                column=index,
                sticky="nsew",
                padx=(0, 10) if index < len(cards) - 1 else 0,
            )
            self._set_frame_palette(widgets["frame"], background, foreground)
            widgets["title_var"].set(card_data.title)
            widgets["value_var"].set(card_data.value)
            widgets["detail_var"].set(card_data.detail)

        for widgets in self._summary_card_widgets[len(cards) :]:
            widgets["frame"].grid_remove()

    def _render_selected_detail(self, view):
        detail = next(
            (
                item
                for item in view.node_details
                if item.node_id == (self._selected_node_id or view.selected_detail.node_id)
            ),
            view.selected_detail,
        )
        self._render_detail_frame(self._overview_inspector_frame, detail, "overview")
        self._render_detail_frame(self._connection_inspector_frame, detail, "connections")

    def _render_detail_frame(self, frame, detail, cache_key):
        import tkinter as tk
        from tkinter import ttk

        if frame is None:
            return
        signature = (
            detail.node_id,
            detail.title,
            detail.subtitle,
            tuple((badge.text, badge.tone) for badge in detail.badges),
            tuple((field.label, field.value) for field in detail.fields),
            detail.action_label,
        )
        widgets = self._detail_widgets.get(cache_key)
        if widgets is None:
            title_var = tk.StringVar()
            subtitle_var = tk.StringVar()
            action_var = tk.StringVar()
            title = ttk.Label(frame, textvariable=title_var, style="InspectorTitle.TLabel")
            subtitle = ttk.Label(
                frame,
                textvariable=subtitle_var,
                style="SurfaceMuted.TLabel",
                wraplength=320,
            )
            badge_row = tk.Frame(frame, bg=PALETTE["surface"])
            fields = ttk.Frame(frame, style="Surface.TFrame")
            action = ttk.Label(
                frame,
                textvariable=action_var,
                style="SurfaceMuted.TLabel",
                wraplength=320,
            )
            title.grid(row=0, column=0, columnspan=2, sticky="w")
            subtitle.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
            badge_row.grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
            fields.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
            fields.columnconfigure(1, weight=1)
            action.grid(row=4, column=0, columnspan=2, sticky="w", pady=(12, 0))
            widgets = {
                "title_var": title_var,
                "subtitle_var": subtitle_var,
                "badge_row": badge_row,
                "fields": fields,
                "action_var": action_var,
                "signature": None,
            }
            self._detail_widgets[cache_key] = widgets
        if widgets["signature"] == signature:
            return
        widgets["signature"] = signature
        widgets["title_var"].set(detail.title)
        widgets["subtitle_var"].set(detail.subtitle)
        widgets["action_var"].set(detail.action_label)
        self._sync_badges(widgets["badge_row"], detail.badges, background=PALETTE["surface"])
        for child in widgets["fields"].winfo_children():
            child.destroy()
        for index, field in enumerate(detail.fields):
            ttk.Label(
                widgets["fields"],
                text=field.label,
                style="SurfaceMuted.TLabel",
            ).grid(row=index, column=0, sticky="w", pady=3, padx=(0, 10))
            ttk.Label(
                widgets["fields"],
                text=field.value,
                style="Surface.TLabel",
            ).grid(row=index, column=1, sticky="w", pady=3)

    def _render_advanced_runtime(self):
        from tkinter import ttk

        if self._advanced_runtime_frame is None:
            return
        rows = [
            ("Self Node", "self_id"),
            ("Coordinator", "coordinator"),
            ("Router", "router"),
            ("Lease", "lease"),
            ("Config Path", "config_path"),
        ]
        for index, (label, key) in enumerate(rows):
            widgets = self._advanced_runtime_widgets.get(key)
            if widgets is None:
                left = ttk.Label(self._advanced_runtime_frame, text=label, style="SurfaceMuted.TLabel")
                right = ttk.Label(self._advanced_runtime_frame, textvariable=self._vars[key], style="Surface.TLabel")
                left.grid(row=index, column=0, sticky="w", pady=3, padx=(0, 10))
                right.grid(row=index, column=1, sticky="w", pady=3)
                self._advanced_runtime_widgets[key] = (left, right)

    def _render_advanced_peers(self, peers):
        import tkinter as tk

        if self._advanced_peer_frame is None:
            return
        signature = tuple(
            (peer.node_id, build_advanced_peer_text(peer))
            for peer in peers
        )
        if signature == self._advanced_peer_signature:
            return
        self._advanced_peer_signature = signature

        for child in self._advanced_peer_frame.winfo_children():
            child.destroy()
        for index, peer in enumerate(peers):
            box = tk.Frame(
                self._advanced_peer_frame,
                bg=PALETTE["surface"],
                bd=1,
                relief="solid",
                highlightthickness=0,
            )
            box.grid(row=index, column=0, sticky="ew", pady=4)
            tk.Label(
                box,
                text=peer.node_id,
                bg=PALETTE["surface"],
                fg=PALETTE["text"],
                font=("", 10, "bold"),
            ).grid(row=0, column=0, sticky="w", padx=10, pady=(8, 0))
            tk.Label(
                box,
                text=build_advanced_peer_text(peer),
                bg=PALETTE["surface"],
                fg=PALETTE["muted"],
                wraplength=380,
                justify="left",
            ).grid(row=1, column=0, sticky="w", padx=10, pady=(2, 8))

    def _sync_badges(self, parent, badges, *, background):
        import tkinter as tk

        for child in parent.winfo_children():
            child.destroy()
        parent.configure(bg=background)
        for index, badge in enumerate(badges):
            badge_bg, badge_fg = palette_for_tone(badge.tone)
            tk.Label(
                parent,
                text=badge.text,
                bg=badge_bg,
                fg=badge_fg,
                padx=8,
                pady=3,
            ).grid(row=0, column=index, sticky="w", padx=(0, 6))

    def _set_frame_palette(self, frame, background, foreground):
        import tkinter as tk

        frame.configure(bg=background)
        for child in frame.winfo_children():
            if isinstance(child, tk.Label):
                child.configure(bg=background)
                if child.cget("font") == "":
                    child.configure(fg=foreground)
            if child.winfo_class() == "Label":
                if child.cget("font") == "":
                    child.configure(fg=foreground)

    def _bind_select_node(self, widget, node_id: str):
        widget.bind("<Button-1>", lambda _event, current=node_id: self._set_selected_node(current))

    def _set_selected_node(self, node_id: str | None):
        self._selected_node_id = node_id
        if self._peer_tree is not None and node_id and self._peer_tree.exists(node_id):
            self._peer_tree.selection_set(node_id)
            self._peer_tree.focus(node_id)

    def _on_peer_tree_select(self, _event):
        if self._peer_tree is None:
            return
        selection = self._peer_tree.selection()
        if selection:
            self._selected_node_id = selection[0]

    def _reload_config(self):
        if self.config_reloader is None:
            return
        try:
            self.config_reloader.reload()
        except Exception as exc:
            self._set_message(f"reload failed: {exc}")
        else:
            self._set_message("config reload complete")

    def _clear_target(self):
        if self.coord_client is not None:
            self.coord_client.clear_target()
            self._set_message("target 선택 해제")

    def _request_target(self, node_id: str):
        if self.coord_client is None:
            return
        self.coord_client.request_target(node_id)
        self._set_message(f"{node_id} PC로 전환을 요청했습니다.")

    def _set_widget_enabled(self, widget, enabled: bool):
        if widget is not None and hasattr(widget, "state"):
            widget.state(["!disabled"] if enabled else ["disabled"])

    def _set_message(self, message: str):
        if "message" in self._vars:
            self._vars["message"].set(message)

    def _handle_close(self):
        self._layout_editor.close()
        if self.coord_client is not None and self.coord_client.is_layout_editor():
            self.coord_client.end_layout_edit()
        if self._on_close is not None:
            self._on_close()
        if self._root is not None:
            self._root.destroy()
            self._root = None
