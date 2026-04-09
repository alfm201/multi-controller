"""간단한 상태 창 기반 운영 UI."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TargetView:
    """상태 창에 표시할 target 정보."""

    node_id: str
    online: bool
    selected: bool
    state: str | None


@dataclass(frozen=True)
class StatusView:
    """상태 창 전체에 필요한 읽기 전용 상태."""

    self_id: str
    coordinator_id: str | None
    online_peers: tuple[str, ...]
    router_state: str | None
    selected_target: str | None
    authorized_controller: str | None
    targets: tuple[TargetView, ...]


def build_status_view(ctx, registry, coordinator_resolver, router=None, sink=None):
    """상태 창 표시용 스냅샷을 만든다."""
    coordinator = coordinator_resolver()
    coordinator_id = None if coordinator is None else coordinator.node_id
    online_peers = tuple(
        sorted(node_id for node_id, conn in registry.all() if conn is not None and not conn.closed)
    )
    router_state = None if router is None else router.get_target_state()
    selected_target = None if router is None else router.get_selected_target()
    authorized_controller = None if sink is None else sink.get_authorized_controller()

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
        router_state=router_state,
        selected_target=selected_target,
        authorized_controller=authorized_controller,
        targets=targets,
    )


class StatusWindow:
    """tkinter 기반의 간단한 상태 창."""

    def __init__(
        self,
        ctx,
        registry,
        coordinator_resolver,
        router=None,
        sink=None,
        coord_client=None,
        refresh_ms=500,
    ):
        self.ctx = ctx
        self.registry = registry
        self.coordinator_resolver = coordinator_resolver
        self.router = router
        self.sink = sink
        self.coord_client = coord_client
        self.refresh_ms = refresh_ms

        self._root = None
        self._vars = {}
        self._target_frame = None
        self._target_buttons = {}
        self._on_close = None

    def run(self, on_close):
        """상태 창 메인 루프를 실행한다."""
        import tkinter as tk
        from tkinter import ttk

        self._on_close = on_close
        self._root = tk.Tk()
        self._root.title(f"multi-controller [{self.ctx.self_node.node_id}]")
        self._root.geometry("420x320")
        self._root.resizable(False, False)
        self._root.protocol("WM_DELETE_WINDOW", self._handle_close)

        frame = ttk.Frame(self._root, padding=16)
        frame.pack(fill="both", expand=True)

        self._vars["self_id"] = tk.StringVar()
        self._vars["coordinator"] = tk.StringVar()
        self._vars["online"] = tk.StringVar()
        self._vars["router"] = tk.StringVar()
        self._vars["lease"] = tk.StringVar()

        rows = [
            ("현재 노드", self._vars["self_id"]),
            ("현재 coordinator", self._vars["coordinator"]),
            ("온라인 peer", self._vars["online"]),
            ("라우터 상태", self._vars["router"]),
            ("허용 controller", self._vars["lease"]),
        ]

        for index, (label, var) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=index, column=0, sticky="w", pady=2)
            ttk.Label(frame, textvariable=var).grid(row=index, column=1, sticky="w", pady=2)

        ttk.Separator(frame).grid(row=len(rows), column=0, columnspan=2, sticky="ew", pady=10)
        ttk.Label(frame, text="Target 전환").grid(row=len(rows) + 1, column=0, sticky="w")

        self._target_frame = ttk.Frame(frame)
        self._target_frame.grid(row=len(rows) + 2, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        ttk.Button(frame, text="닫기", command=self._handle_close).grid(
            row=len(rows) + 3,
            column=1,
            sticky="e",
            pady=(16, 0),
        )

        self._refresh()
        self._root.mainloop()

    def _refresh(self):
        if self._root is None:
            return

        view = build_status_view(
            self.ctx,
            self.registry,
            self.coordinator_resolver,
            router=self.router,
            sink=self.sink,
        )

        self._vars["self_id"].set(view.self_id)
        self._vars["coordinator"].set(view.coordinator_id or "-")
        self._vars["online"].set(", ".join(view.online_peers) if view.online_peers else "-")
        self._vars["router"].set(
            f"{view.router_state or '-'} / {view.selected_target or '-'}"
        )
        self._vars["lease"].set(view.authorized_controller or "-")

        self._render_targets(view.targets)
        self._root.after(self.refresh_ms, self._refresh)

    def _render_targets(self, targets):
        import tkinter as tk
        from tkinter import ttk

        existing = set(self._target_buttons)
        current = {target.node_id for target in targets}

        for node_id in existing - current:
            self._target_buttons[node_id].destroy()
            del self._target_buttons[node_id]

        for index, target in enumerate(targets):
            text = self._format_target_text(target)
            if target.node_id not in self._target_buttons:
                button = ttk.Button(
                    self._target_frame,
                    command=lambda node_id=target.node_id: self._select_target(node_id),
                )
                button.grid(row=index, column=0, sticky="ew", pady=2)
                self._target_buttons[target.node_id] = button
            else:
                button = self._target_buttons[target.node_id]
                button.grid_configure(row=index)

            state = tk.NORMAL if self.coord_client is not None and target.online else tk.DISABLED
            button.configure(text=text, state=state)

    def _format_target_text(self, target):
        parts = [target.node_id]
        parts.append("online" if target.online else "offline")
        if target.selected:
            parts.append(target.state or "selected")
        return " | ".join(parts)

    def _select_target(self, node_id):
        if self.coord_client is None:
            return
        self.coord_client.request_target(node_id)

    def _handle_close(self):
        if self._on_close is not None:
            self._on_close()
        if self._root is not None:
            self._root.destroy()
            self._root = None
