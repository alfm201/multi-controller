"""User-facing status window shell."""

from datetime import datetime

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
        self._peer_frame = None
        self._peer_tree = None
        self._advanced_runtime_frame = None
        self._advanced_peer_frame = None
        self._advanced_peer_var = None
        self._summary_cards_frame = None
        self._target_buttons = {}
        self._peer_rows = {}
        self._selected_node_id = None
        self._last_seen = {}
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
        self._root.title(f"multi-controller [{self.ctx.self_node.node_id}]")
        self._root.geometry("1240x920")
        self._root.minsize(1040, 760)
        self._root.protocol("WM_DELETE_WINDOW", self._handle_close)

        frame = ttk.Frame(self._root, padding=16)
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
        self._vars["selected_title"] = tk.StringVar(value="선택된 PC 없음")
        self._vars["selected_subtitle"] = tk.StringVar(value="상세 정보를 보려면 PC를 선택하세요.")
        self._advanced_peer_var = tk.StringVar()

        ttk.Label(frame, text=f"내 PC: {self.ctx.self_node.node_id}").grid(
            row=0,
            column=0,
            sticky="w",
        )

        notebook = ttk.Notebook(frame)
        notebook.grid(row=1, column=0, sticky="nsew", pady=(12, 0))

        overview_tab = ttk.Frame(notebook, padding=14)
        layout_tab = ttk.Frame(notebook)
        connection_tab = ttk.Frame(notebook, padding=14)
        advanced_tab = ttk.Frame(notebook, padding=14)

        notebook.add(overview_tab, text="요약")
        notebook.add(layout_tab, text="레이아웃")
        notebook.add(connection_tab, text="연결 상태")
        notebook.add(advanced_tab, text="고급 정보")

        self._build_overview_tab(overview_tab, ttk, notebook)
        self._build_connection_tab(connection_tab, ttk)
        self._build_advanced_tab(advanced_tab, ttk)
        layout_tab.columnconfigure(0, weight=1)
        layout_tab.rowconfigure(0, weight=1)
        self._layout_editor.build(layout_tab).grid(row=0, column=0, sticky="nsew")

        ttk.Label(
            frame,
            textvariable=self._vars["message"],
            foreground="#555555",
        ).grid(row=2, column=0, sticky="w", pady=(10, 0))

        self._refresh()
        self._root.mainloop()

    def _build_overview_tab(self, tab, ttk, notebook):
        tab.columnconfigure(0, weight=3)
        tab.columnconfigure(1, weight=2)
        tab.rowconfigure(5, weight=1)

        ttk.Label(tab, textvariable=self._vars["headline"]).grid(
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
            foreground="#555555",
            wraplength=920,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self._summary_cards_frame = ttk.Frame(tab)
        self._summary_cards_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        self._summary_cards_frame.columnconfigure(0, weight=1)
        self._summary_cards_frame.columnconfigure(1, weight=1)
        self._summary_cards_frame.columnconfigure(2, weight=1)
        self._summary_cards_frame.columnconfigure(3, weight=1)

        actions = ttk.Frame(tab)
        actions.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        if self.config_reloader is not None:
            ttk.Button(actions, text="Config Reload", command=self._reload_config).pack(
                side="left"
            )
        if self.coord_client is not None:
            ttk.Button(actions, text="선택 해제", command=self._clear_target).pack(
                side="left",
                padx=(8, 0),
            )
        ttk.Button(
            actions,
            text="레이아웃 탭으로 이동",
            command=lambda: notebook.select(1),
        ).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="닫기", command=self._handle_close).pack(side="right")

        ttk.Label(tab, text="빠른 전환", foreground="#555555").grid(
            row=5,
            column=0,
            sticky="w",
            pady=(18, 0),
        )
        self._target_frame = ttk.Frame(tab)
        self._target_frame.grid(row=6, column=0, sticky="nsew", pady=(8, 0), padx=(0, 12))

        self._overview_inspector_frame = ttk.LabelFrame(tab, text="선택 상세", padding=12)
        self._overview_inspector_frame.grid(row=5, column=1, rowspan=2, sticky="nsew")
        self._overview_inspector_frame.columnconfigure(0, weight=1)

    def _build_connection_tab(self, tab, ttk):
        tab.columnconfigure(0, weight=3)
        tab.columnconfigure(1, weight=2)
        tab.rowconfigure(1, weight=1)
        ttk.Label(
            tab,
            text="연결 상태를 표 형태로 정리했습니다. 문제가 있는 PC는 강조해서 보여줍니다.",
            foreground="#555555",
            wraplength=920,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        self._peer_frame = ttk.Frame(tab)
        self._peer_frame.grid(row=1, column=0, sticky="nsew", pady=(12, 0), padx=(0, 12))
        self._peer_frame.columnconfigure(0, weight=1)
        self._peer_frame.rowconfigure(0, weight=1)

        self._peer_tree = ttk.Treeview(
            self._peer_frame,
            columns=("status", "roles", "layout", "display", "last_seen"),
            show="headings",
            selectmode="browse",
        )
        self._peer_tree.heading("status", text="상태")
        self._peer_tree.heading("roles", text="역할")
        self._peer_tree.heading("layout", text="레이아웃")
        self._peer_tree.heading("display", text="Display")
        self._peer_tree.heading("last_seen", text="최근 갱신")
        self._peer_tree.column("status", width=120, anchor="center")
        self._peer_tree.column("roles", width=170, anchor="w")
        self._peer_tree.column("layout", width=100, anchor="center")
        self._peer_tree.column("display", width=80, anchor="center")
        self._peer_tree.column("last_seen", width=100, anchor="center")
        self._peer_tree.grid(row=0, column=0, sticky="nsew")
        self._peer_tree.tag_configure("offline", foreground="#b91c1c")
        self._peer_tree.tag_configure("warning", foreground="#92400e")
        self._peer_tree.bind("<<TreeviewSelect>>", self._on_peer_tree_select)

        connection_detail = ttk.LabelFrame(tab, text="선택 상세", padding=12)
        connection_detail.grid(row=1, column=1, sticky="nsew", pady=(12, 0))
        connection_detail.columnconfigure(0, weight=1)
        self._connection_inspector_frame = connection_detail

    def _build_advanced_tab(self, tab, ttk):
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)
        runtime_box = ttk.LabelFrame(tab, text="런타임", padding=12)
        runtime_box.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        runtime_box.columnconfigure(1, weight=1)
        peer_box = ttk.LabelFrame(tab, text="Peer 상세", padding=12)
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
        self._advanced_peer_var.set(
            "\n".join(build_advanced_peer_text(peer) for peer in view.peers) or "-"
        )
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
        for child in self._target_frame.winfo_children():
            child.destroy()

        if not targets:
            ttk.Label(self._target_frame, text="사용 가능한 target이 없습니다.").grid(
                row=0,
                column=0,
                sticky="w",
            )
            return

        for index, target in enumerate(targets):
            card = tk.Frame(
                self._target_frame,
                bg="#ffffff",
                bd=1,
                relief="solid",
                highlightthickness=0,
            )
            card.grid(row=index, column=0, sticky="ew", pady=4)
            card.grid_columnconfigure(0, weight=1)
            self._target_frame.columnconfigure(0, weight=1)

            title = ttk.Label(card, text=target.node_id)
            title.grid(row=0, column=0, sticky="w", padx=10, pady=(8, 0))
            subtitle = ttk.Label(card, text=target.subtitle, foreground="#475569")
            subtitle.grid(row=1, column=0, sticky="w", padx=10)
            meta = ttk.Label(
                card,
                text=f"레이아웃 {target.layout_summary} | display {target.display_count}",
                foreground="#64748b",
            )
            meta.grid(row=2, column=0, sticky="w", padx=10, pady=(0, 8))
            action = ttk.Button(
                card,
                text="전환",
                command=lambda node_id=target.node_id: self._request_target(node_id),
            )
            action.grid(row=0, column=1, rowspan=3, sticky="ns", padx=10, pady=8)
            action.state(["!disabled"] if target.online else ["disabled"])
            self._render_badges(card, target.badges, row=3, padx=10, pady=(0, 8))
            self._bind_select_node(card, target.node_id)
            self._bind_select_node(title, target.node_id)
            self._bind_select_node(subtitle, target.node_id)
            self._bind_select_node(meta, target.node_id)

    def _render_peers(self, peers):
        if self._peer_tree is None:
            return
        self._peer_tree.delete(*self._peer_tree.get_children())
        for peer in peers:
            status = "연결됨" if peer.online else "오프라인"
            if peer.is_authorized_controller:
                status = f"{status} / 권한"
            tags = ()
            if not peer.online:
                tags = ("offline",)
            elif peer.is_authorized_controller:
                tags = ("warning",)
            self._peer_tree.insert(
                "",
                "end",
                iid=peer.node_id,
                values=(
                    status,
                    peer.role_summary,
                    peer.layout_summary,
                    peer.display_count,
                    peer.last_seen,
                ),
                tags=tags,
            )
        if self._selected_node_id and self._peer_tree.exists(self._selected_node_id):
            self._peer_tree.selection_set(self._selected_node_id)
            self._peer_tree.focus(self._selected_node_id)

    def _render_summary_cards(self, cards):
        if self._summary_cards_frame is None:
            return
        import tkinter as tk

        for child in self._summary_cards_frame.winfo_children():
            child.destroy()
        for index, card in enumerate(cards):
            background, foreground = self._card_colors(card.tone)
            frame = tk.Frame(
                self._summary_cards_frame,
                bg=background,
                bd=1,
                relief="solid",
            )
            frame.grid(row=0, column=index, sticky="nsew", padx=(0, 10) if index < len(cards) - 1 else 0)
            tk.Label(frame, text=card.title, bg=background, fg=foreground).grid(
                row=0,
                column=0,
                sticky="w",
                padx=10,
                pady=(10, 0),
            )
            tk.Label(
                frame,
                text=card.value,
                bg=background,
                fg="#0f172a",
                font=("", 13, "bold"),
            ).grid(row=1, column=0, sticky="w", padx=10, pady=(4, 0))
            tk.Label(
                frame,
                text=card.detail,
                bg=background,
                fg=foreground,
                wraplength=210,
                justify="left",
            ).grid(row=2, column=0, sticky="w", padx=10, pady=(4, 10))

    def _render_selected_detail(self, view):
        detail = next(
            (
                item
                for item in view.node_details
                if item.node_id == (self._selected_node_id or view.selected_detail.node_id)
            ),
            view.selected_detail,
        )
        if "selected_title" in self._vars:
            self._vars["selected_title"].set(detail.title)
        if "selected_subtitle" in self._vars:
            self._vars["selected_subtitle"].set(detail.subtitle)
        self._render_detail_frame(self._overview_inspector_frame, detail)
        if getattr(self, "_connection_inspector_frame", None) is not None:
            self._render_detail_frame(self._connection_inspector_frame, detail)

    def _render_detail_frame(self, frame, detail):
        if frame is None:
            return
        from tkinter import ttk

        for child in frame.winfo_children():
            child.destroy()
        ttk.Label(frame, text=detail.title, font=("", 12, "bold")).grid(
            row=0,
            column=0,
            sticky="w",
        )
        ttk.Label(
            frame,
            text=detail.subtitle,
            foreground="#555555",
            wraplength=320,
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._render_badges(frame, detail.badges, row=2, padx=0, pady=(10, 0))
        for index, field in enumerate(detail.fields, start=3):
            ttk.Label(frame, text=field.label, foreground="#64748b").grid(
                row=index,
                column=0,
                sticky="w",
                pady=(8 if index == 3 else 4, 0),
            )
            ttk.Label(frame, text=field.value).grid(
                row=index,
                column=1,
                sticky="w",
                pady=(8 if index == 3 else 4, 0),
                padx=(12, 0),
            )
        ttk.Label(
            frame,
            text=detail.action_label,
            foreground="#475569",
            wraplength=320,
        ).grid(row=len(detail.fields) + 3, column=0, columnspan=2, sticky="w", pady=(12, 0))

    def _render_advanced_runtime(self):
        if self._advanced_runtime_frame is None:
            return
        from tkinter import ttk

        for child in self._advanced_runtime_frame.winfo_children():
            child.destroy()
        rows = [
            ("현재 노드", "self_id"),
            ("현재 coordinator", "coordinator"),
            ("라우터 상태", "router"),
            ("허용 controller", "lease"),
            ("config 경로", "config_path"),
        ]
        for index, (label, key) in enumerate(rows):
            ttk.Label(self._advanced_runtime_frame, text=label).grid(
                row=index,
                column=0,
                sticky="w",
                pady=3,
                padx=(0, 10),
            )
            ttk.Label(self._advanced_runtime_frame, textvariable=self._vars[key]).grid(
                row=index,
                column=1,
                sticky="w",
                pady=3,
            )

    def _render_advanced_peers(self, peers):
        if self._advanced_peer_frame is None:
            return
        from tkinter import ttk

        for child in self._advanced_peer_frame.winfo_children():
            child.destroy()
        for index, peer in enumerate(peers):
            box = ttk.Frame(self._advanced_peer_frame)
            box.grid(row=index, column=0, sticky="ew", pady=4)
            ttk.Label(box, text=peer.node_id, font=("", 10, "bold")).grid(
                row=0,
                column=0,
                sticky="w",
            )
            ttk.Label(
                box,
                text=build_advanced_peer_text(peer),
                foreground="#475569",
                wraplength=380,
            ).grid(row=1, column=0, sticky="w", pady=(2, 0))

    def _render_badges(self, parent, badges, *, row, padx, pady):
        import tkinter as tk

        badge_row = tk.Frame(parent, bg=parent.cget("bg") if "bg" in parent.keys() else "#ffffff")
        badge_row.grid(row=row, column=0, columnspan=2, sticky="w", padx=padx, pady=pady)
        for index, badge in enumerate(badges):
            background, foreground = self._card_colors(badge.tone)
            tk.Label(
                badge_row,
                text=badge.text,
                bg=background,
                fg=foreground,
                padx=8,
                pady=3,
            ).grid(row=0, column=index, sticky="w", padx=(0, 6))

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

    def _card_colors(self, tone: str) -> tuple[str, str]:
        mapping = {
            "success": ("#dcfce7", "#166534"),
            "warning": ("#fef3c7", "#92400e"),
            "danger": ("#fee2e2", "#b91c1c"),
            "accent": ("#dbeafe", "#1d4ed8"),
            "neutral": ("#f8fafc", "#475569"),
        }
        return mapping.get(tone, mapping["neutral"])

    def _reload_config(self):
        if self.config_reloader is None:
            return
        try:
            self.config_reloader.reload()
        except Exception as exc:
            self._set_message(f"reload 실패: {exc}")
        else:
            self._set_message("config reload 완료")

    def _clear_target(self):
        if self.coord_client is not None:
            self.coord_client.clear_target()
            self._set_message("target 선택 해제")

    def _request_target(self, node_id: str):
        if self.coord_client is None:
            return
        self.coord_client.request_target(node_id)
        self._set_message(f"{node_id} PC로 전환을 요청했습니다.")

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
