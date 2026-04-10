"""User-facing status window shell."""

from runtime.layout_editor import LayoutEditor
from runtime.status_view import (
    build_advanced_peer_text,
    build_connection_summary_text,
    build_peer_summary_text,
    build_primary_status_text,
    build_selection_hint_text,
    build_status_view,
    build_target_button_text,
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
        self._peer_frame = None
        self._advanced_peer_var = None
        self._target_buttons = {}
        self._peer_labels = {}
        self._layout_editor = LayoutEditor(
            ctx,
            registry,
            coordinator_resolver,
            router=router,
            sink=sink,
            coord_client=coord_client,
            on_message=self._set_message,
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
        tab.columnconfigure(0, weight=1)

        ttk.Label(tab, textvariable=self._vars["headline"]).grid(
            row=0,
            column=0,
            sticky="w",
        )
        ttk.Label(tab, textvariable=self._vars["summary"]).grid(
            row=1,
            column=0,
            sticky="w",
            pady=(6, 0),
        )
        ttk.Label(
            tab,
            textvariable=self._vars["hint"],
            foreground="#555555",
            wraplength=920,
        ).grid(row=2, column=0, sticky="w", pady=(6, 0))

        actions = ttk.Frame(tab)
        actions.grid(row=3, column=0, sticky="ew", pady=(14, 0))
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
            row=4,
            column=0,
            sticky="w",
            pady=(18, 0),
        )
        self._target_frame = ttk.Frame(tab)
        self._target_frame.grid(row=5, column=0, sticky="nsew", pady=(8, 0))
        tab.rowconfigure(5, weight=1)

    def _build_connection_tab(self, tab, ttk):
        tab.columnconfigure(0, weight=1)
        self._peer_frame = ttk.Frame(tab)
        self._peer_frame.grid(row=0, column=0, sticky="nsew")

    def _build_advanced_tab(self, tab, ttk):
        tab.columnconfigure(1, weight=1)
        rows = [
            ("현재 노드", "self_id"),
            ("현재 coordinator", "coordinator"),
            ("라우터 상태", "router"),
            ("허용 controller", "lease"),
            ("config 경로", "config_path"),
        ]
        for index, (label, key) in enumerate(rows):
            ttk.Label(tab, text=label).grid(
                row=index,
                column=0,
                sticky="w",
                pady=2,
                padx=(0, 10),
            )
            ttk.Label(tab, textvariable=self._vars[key]).grid(
                row=index,
                column=1,
                sticky="w",
                pady=2,
            )
        ttk.Label(tab, text="peer 상세").grid(
            row=len(rows),
            column=0,
            sticky="nw",
            pady=(10, 0),
            padx=(0, 10),
        )
        ttk.Label(
            tab,
            textvariable=self._advanced_peer_var,
            justify="left",
        ).grid(row=len(rows), column=1, sticky="w", pady=(10, 0))

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
        self._render_targets(view.targets)
        self._render_peers(view.peers)
        self._layout_editor.refresh(view)
        self._root.after(self.refresh_ms, self._refresh)

    def _current_status_view(self):
        return build_status_view(
            self.ctx,
            self.registry,
            self.coordinator_resolver,
            router=self.router,
            sink=self.sink,
        )

    def _render_targets(self, targets):
        from tkinter import ttk

        target_ids = {target.node_id for target in targets}
        for node_id in set(self._target_buttons) - target_ids:
            self._target_buttons.pop(node_id).destroy()

        if not targets and not self._target_buttons:
            label = ttk.Label(self._target_frame, text="사용 가능한 target이 없습니다.")
            label.grid(row=0, column=0, sticky="w")
            self._target_buttons["_empty"] = label
            return

        empty = self._target_buttons.pop("_empty", None)
        if empty is not None:
            empty.destroy()

        for index, target in enumerate(targets):
            button = self._target_buttons.get(target.node_id)
            if button is None:
                button = ttk.Button(
                    self._target_frame,
                    command=lambda node_id=target.node_id: self._request_target(node_id),
                )
                self._target_buttons[target.node_id] = button
            button.grid(row=index, column=0, sticky="ew", pady=4)
            button.configure(text=build_target_button_text(target))
            button.state(["!disabled"] if target.online else ["disabled"])

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
