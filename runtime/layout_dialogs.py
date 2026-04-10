"""Dialog helpers and validation for layout editing."""

from runtime.layouts import (
    find_overlapping_nodes,
    monitor_topology_to_rows,
    replace_auto_switch_settings,
    replace_layout_monitors,
)


def format_monitor_grid_text(rows: list[list[str | None]]) -> str:
    return "\n".join(
        " ".join(cell if cell is not None else "." for cell in row) for row in rows
    )


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


class AutoSwitchDialog:
    """Editor for auto-switch settings."""

    FIELDS = [
        ("edge_threshold", "경계 감도"),
        ("warp_margin", "anchor margin"),
        ("cooldown_ms", "cooldown(ms)"),
        ("return_guard_ms", "return guard(ms)"),
        ("anchor_dead_zone", "anchor dead-zone"),
    ]

    def __init__(self, parent, layout_provider, publish_layout):
        import tkinter as tk
        from tkinter import ttk

        self._layout_provider = layout_provider
        self._publish_layout = publish_layout
        self.window = tk.Toplevel(parent)
        self.window.title("자동 전환 세부 설정")
        self.window.geometry("440x340")
        frame = ttk.Frame(self.window, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        self._entries = {}
        layout = layout_provider()
        settings = layout.auto_switch
        for index, (key, label) in enumerate(self.FIELDS):
            ttk.Label(frame, text=label).grid(
                row=index, column=0, sticky="w", pady=4, padx=(0, 8)
            )
            entry = ttk.Entry(frame)
            entry.grid(row=index, column=1, sticky="ew", pady=4)
            entry.insert(0, str(getattr(settings, key)))
            self._entries[key] = entry

        self.status_var = tk.StringVar(
            value="값을 검증한 뒤 적용하면 전체 노드에 즉시 반영됩니다."
        )
        ttk.Label(
            frame,
            textvariable=self.status_var,
            foreground="#555555",
            wraplength=390,
        ).grid(
            row=len(self.FIELDS),
            column=0,
            columnspan=2,
            sticky="w",
            pady=(12, 0),
        )
        buttons = ttk.Frame(frame)
        buttons.grid(
            row=len(self.FIELDS) + 1,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(12, 0),
        )
        ttk.Button(buttons, text="검증", command=lambda: self.apply(False)).pack(
            side="left"
        )
        ttk.Button(buttons, text="적용", command=lambda: self.apply(True)).pack(
            side="left",
            padx=(8, 0),
        )
        ttk.Button(buttons, text="닫기", command=self.close).pack(side="right")
        self.window.protocol("WM_DELETE_WINDOW", self.close)

    def apply(self, commit: bool):
        layout = self._layout_provider()
        if layout is None:
            return
        try:
            parsed = parse_auto_switch_form(
                {key: entry.get() for key, entry in self._entries.items()}
            )
            candidate = replace_auto_switch_settings(layout, **parsed)
        except Exception as exc:
            self.status_var.set(f"검증 실패: {exc}")
            return
        if not commit:
            self.status_var.set("검증 성공: 자동 전환 세부 설정 값을 사용할 수 있습니다.")
            return
        if self._publish_layout(candidate, "자동 전환 세부 설정을 실시간으로 적용했습니다."):
            self.status_var.set("적용 완료: 자동 전환 세부 설정을 반영했습니다.")
        else:
            self.status_var.set("적용 실패: 변경사항을 전송하지 못했습니다.")

    def close(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.destroy()


class MonitorMapDialog:
    """Editor for logical and physical monitor maps."""

    def __init__(self, parent, node_id: str, layout_provider, publish_layout):
        import tkinter as tk
        from tkinter import ttk

        self._node_id = node_id
        self._layout_provider = layout_provider
        self._publish_layout = publish_layout
        self.window = tk.Toplevel(parent)
        self.window.title(f"모니터 맵 편집 [{node_id}]")
        self.window.geometry("760x500")
        frame = ttk.Frame(self.window, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(2, weight=1)
        ttk.Label(
            frame,
            text="공백으로 셀을 구분하고 빈 칸은 . 으로 입력하세요.",
            wraplength=700,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, text="논리 모니터 배치").grid(
            row=1,
            column=0,
            sticky="w",
            pady=(10, 4),
        )
        ttk.Label(frame, text="물리 모니터 배치").grid(
            row=1,
            column=1,
            sticky="w",
            pady=(10, 4),
        )
        self.logical = tk.Text(frame, width=36, height=16)
        self.physical = tk.Text(frame, width=36, height=16)
        self.logical.grid(row=2, column=0, sticky="nsew", padx=(0, 8))
        self.physical.grid(row=2, column=1, sticky="nsew")

        node = layout_provider().get_node(node_id)
        self.logical.insert(
            "1.0",
            format_monitor_grid_text(monitor_topology_to_rows(node.monitors(), logical=True)),
        )
        self.physical.insert(
            "1.0",
            format_monitor_grid_text(monitor_topology_to_rows(node.monitors(), logical=False)),
        )

        self.status_var = tk.StringVar(
            value=f"{node_id} PC의 논리/물리 모니터 맵을 별도로 편집할 수 있습니다."
        )
        ttk.Label(frame, textvariable=self.status_var, foreground="#555555").grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(8, 0),
        )
        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(buttons, text="검증", command=lambda: self.apply(False)).pack(
            side="left"
        )
        ttk.Button(buttons, text="적용", command=lambda: self.apply(True)).pack(
            side="left",
            padx=(8, 0),
        )
        ttk.Button(buttons, text="닫기", command=self.close).pack(side="right")
        self.window.protocol("WM_DELETE_WINDOW", self.close)

    def apply(self, commit: bool):
        layout = self._layout_provider()
        if layout is None:
            return
        try:
            candidate = replace_layout_monitors(
                layout,
                self._node_id,
                logical_rows=parse_monitor_grid_text(self.logical.get("1.0", "end")),
                physical_rows=parse_monitor_grid_text(self.physical.get("1.0", "end")),
            )
            overlaps = find_overlapping_nodes(candidate)
            if overlaps:
                raise ValueError("물리 배치 변경으로 PC가 겹칩니다.")
        except Exception as exc:
            self.status_var.set(f"검증 실패: {exc}")
            return

        node = candidate.get_node(self._node_id)
        self.logical.delete("1.0", "end")
        self.physical.delete("1.0", "end")
        self.logical.insert(
            "1.0",
            format_monitor_grid_text(monitor_topology_to_rows(node.monitors(), logical=True)),
        )
        self.physical.insert(
            "1.0",
            format_monitor_grid_text(monitor_topology_to_rows(node.monitors(), logical=False)),
        )

        if not commit:
            self.status_var.set(
                f"검증 성공: 물리 {node.width}x{node.height}, display {len(node.monitors().physical)}개"
            )
            return
        if self._publish_layout(candidate, "모니터 맵을 실시간으로 적용했습니다."):
            self.status_var.set(
                f"적용 완료: 물리 {node.width}x{node.height}, display {len(node.monitors().physical)}개"
            )
        else:
            self.status_var.set("적용 실패: 변경사항을 전송하지 못했습니다.")

    def close(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.destroy()
