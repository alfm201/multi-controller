"""Dialog helpers and validation for layout editing."""

from dataclasses import dataclass

from runtime.layouts import (
    build_monitor_topology,
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


@dataclass(frozen=True)
class MonitorGridModel:
    cells: tuple[tuple[str | None, ...], ...]

    @property
    def rows(self) -> int:
        return len(self.cells)

    @property
    def cols(self) -> int:
        return 0 if not self.cells else len(self.cells[0])

    def display_ids(self) -> tuple[str, ...]:
        seen = []
        for row in self.cells:
            for cell in row:
                if cell is not None and cell not in seen:
                    seen.append(cell)
        return tuple(seen)


@dataclass(frozen=True)
class MonitorGridValidation:
    logical_rows: tuple[tuple[str | None, ...], ...]
    physical_rows: tuple[tuple[str | None, ...], ...]
    display_ids: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return not self.errors


def monitor_grid_from_rows(
    rows: list[list[str | None]], *, min_rows: int = 1, min_cols: int = 1
) -> MonitorGridModel:
    normalized_rows = []
    max_cols = max((len(row) for row in rows), default=0)
    final_rows = max(len(rows), min_rows, 1)
    final_cols = max(max_cols, min_cols, 1)
    for row_index in range(final_rows):
        row = rows[row_index] if row_index < len(rows) else []
        normalized_row = []
        for col_index in range(final_cols):
            value = row[col_index] if col_index < len(row) else None
            normalized_row.append(None if value in (None, "", ".") else str(value).strip())
        normalized_rows.append(tuple(normalized_row))
    return MonitorGridModel(cells=tuple(normalized_rows))


def monitor_grid_to_rows(grid: MonitorGridModel) -> list[list[str | None]]:
    occupied = [
        (row_index, col_index)
        for row_index, row in enumerate(grid.cells)
        for col_index, cell in enumerate(row)
        if cell is not None
    ]
    if not occupied:
        raise ValueError("모니터 맵에는 최소 1개의 display가 필요합니다.")
    min_row = min(row for row, _col in occupied)
    max_row = max(row for row, _col in occupied)
    min_col = min(col for _row, col in occupied)
    max_col = max(col for _row, col in occupied)
    rows = []
    for row_index in range(min_row, max_row + 1):
        row = []
        for col_index in range(min_col, max_col + 1):
            row.append(grid.cells[row_index][col_index])
        rows.append(row)
    return rows


def set_monitor_grid_cell(
    grid: MonitorGridModel, row: int, col: int, display_id: str | None
) -> MonitorGridModel:
    rows = [list(current_row) for current_row in grid.cells]
    if display_id is not None:
        display_id = display_id.strip()
        if not display_id:
            raise ValueError("display id가 비어 있습니다.")
        for row_index, current_row in enumerate(rows):
            for col_index, cell in enumerate(current_row):
                if cell == display_id:
                    rows[row_index][col_index] = None
    rows[row][col] = display_id
    return MonitorGridModel(cells=tuple(tuple(current_row) for current_row in rows))


def remove_display_from_grid(
    grid: MonitorGridModel, display_id: str | None
) -> MonitorGridModel:
    if display_id is None:
        return grid
    rows = []
    for current_row in grid.cells:
        rows.append(
            tuple(None if cell == display_id else cell for cell in current_row)
        )
    return MonitorGridModel(cells=tuple(rows))


def expand_monitor_grid(
    grid: MonitorGridModel, *, add_rows: int = 0, add_cols: int = 0
) -> MonitorGridModel:
    new_rows = grid.rows + max(add_rows, 0)
    new_cols = grid.cols + max(add_cols, 0)
    return monitor_grid_from_rows(
        [list(row) for row in grid.cells],
        min_rows=max(new_rows, 1),
        min_cols=max(new_cols, 1),
    )


def trim_monitor_grid(
    grid: MonitorGridModel, *, min_rows: int = 3, min_cols: int = 3
) -> MonitorGridModel:
    occupied = [
        (row_index, col_index)
        for row_index, row in enumerate(grid.cells)
        for col_index, cell in enumerate(row)
        if cell is not None
    ]
    if not occupied:
        return monitor_grid_from_rows([], min_rows=min_rows, min_cols=min_cols)
    min_row = min(row for row, _col in occupied)
    max_row = max(row for row, _col in occupied)
    min_col = min(col for _row, col in occupied)
    max_col = max(col for _row, col in occupied)
    rows = []
    for row_index in range(min_row, max_row + 1):
        row = []
        for col_index in range(min_col, max_col + 1):
            row.append(grid.cells[row_index][col_index])
        rows.append(row)
    return monitor_grid_from_rows(rows, min_rows=min_rows, min_cols=min_cols)


def build_monitor_preset(width: int, height: int) -> MonitorGridModel:
    display_ids = [str(index + 1) for index in range(width * height)]
    rows = []
    cursor = 0
    for _row in range(height):
        rows.append(display_ids[cursor : cursor + width])
        cursor += width
    return monitor_grid_from_rows(rows, min_rows=max(height, 3), min_cols=max(width, 3))


def next_monitor_display_id(display_ids: tuple[str, ...]) -> str:
    numeric = []
    for display_id in display_ids:
        if display_id.isdigit():
            numeric.append(int(display_id))
    if numeric:
        return str(max(numeric) + 1)
    return f"D{len(display_ids) + 1}"


def validate_monitor_grids(
    logical_grid: MonitorGridModel,
    physical_grid: MonitorGridModel,
) -> MonitorGridValidation:
    errors = []
    try:
        logical_rows = monitor_grid_to_rows(logical_grid)
    except ValueError as exc:
        logical_rows = []
        errors.append(f"논리 배치: {exc}")
    try:
        physical_rows = monitor_grid_to_rows(physical_grid)
    except ValueError as exc:
        physical_rows = []
        errors.append(f"물리 배치: {exc}")

    if logical_rows and not _is_contiguous(logical_rows):
        errors.append("논리 배치는 끊기지 않아야 합니다.")
    if physical_rows and not _is_contiguous(physical_rows):
        errors.append("물리 배치는 끊기지 않아야 합니다.")

    display_ids = ()
    if logical_rows and physical_rows:
        try:
            topology = build_monitor_topology(
                {"logical": logical_rows, "physical": physical_rows},
                fallback_width=1,
                fallback_height=1,
            )
        except ValueError as exc:
            errors.append(str(exc))
        else:
            display_ids = topology.display_ids()

    return MonitorGridValidation(
        logical_rows=tuple(tuple(row) for row in logical_rows),
        physical_rows=tuple(tuple(row) for row in physical_rows),
        display_ids=tuple(display_ids),
        errors=tuple(errors),
    )


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
    """Visual editor for logical and physical monitor maps."""

    COLORS = (
        ("#e0f2fe", "#075985"),
        ("#dcfce7", "#166534"),
        ("#fef3c7", "#92400e"),
        ("#fce7f3", "#9d174d"),
        ("#ede9fe", "#5b21b6"),
        ("#dbeafe", "#1d4ed8"),
        ("#fee2e2", "#b91c1c"),
        ("#ecfccb", "#4d7c0f"),
    )

    def __init__(self, parent, node_id: str, layout_provider, publish_layout):
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self._ttk = ttk
        self._node_id = node_id
        self._layout_provider = layout_provider
        self._publish_layout = publish_layout
        self._selected_display_id = None
        self._selected_cell = None
        self._board_frames = {}
        self._board_buttons = {"logical": [], "physical": []}
        self._display_buttons = {}
        self._display_colors = {}

        self.window = tk.Toplevel(parent)
        self.window.title(f"모니터 맵 편집 [{node_id}]")
        self.window.geometry("980x680")
        self.window.minsize(880, 620)

        node = layout_provider().get_node(node_id)
        logical_rows = monitor_topology_to_rows(node.monitors(), logical=True)
        physical_rows = monitor_topology_to_rows(node.monitors(), logical=False)
        min_rows = max(len(logical_rows), len(physical_rows), 3)
        min_cols = max(
            max((len(row) for row in logical_rows), default=0),
            max((len(row) for row in physical_rows), default=0),
            3,
        )
        self._base_logical_grid = monitor_grid_from_rows(
            logical_rows,
            min_rows=min_rows,
            min_cols=min_cols,
        )
        self._base_physical_grid = monitor_grid_from_rows(
            physical_rows,
            min_rows=min_rows,
            min_cols=min_cols,
        )
        self._logical_grid = self._base_logical_grid
        self._physical_grid = self._base_physical_grid
        ids = self._logical_grid.display_ids() or self._physical_grid.display_ids()
        self._selected_display_id = None if not ids else ids[0]

        frame = ttk.Frame(self.window, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)

        ttk.Label(
            frame,
            text="클릭으로 display를 선택하고 빈 칸을 눌러 이동하세요. 두 보드는 같은 display 집합을 사용합니다.",
            wraplength=920,
        ).grid(row=0, column=0, sticky="w")

        palette = ttk.LabelFrame(frame, text="Display", padding=10)
        palette.grid(row=1, column=0, sticky="ew", pady=(10, 10))
        palette.columnconfigure(0, weight=1)
        self._palette_frame = ttk.Frame(palette)
        self._palette_frame.grid(row=0, column=0, sticky="ew")

        palette_actions = ttk.Frame(palette)
        palette_actions.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(palette_actions, text="Display 추가", command=self._add_display).pack(
            side="left"
        )
        ttk.Button(
            palette_actions,
            text="선택 Display 제거",
            command=self._remove_selected_display,
        ).pack(side="left", padx=(8, 0))
        ttk.Button(palette_actions, text="1xN", command=self._apply_preset_row).pack(
            side="left",
            padx=(16, 0),
        )
        ttk.Button(
            palette_actions,
            text="2x2",
            command=lambda: self._apply_preset_grid(2, 2),
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            palette_actions,
            text="3x2",
            command=lambda: self._apply_preset_grid(3, 2),
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            palette_actions,
            text="초기값으로 되돌리기",
            command=self._reset_to_base,
        ).pack(side="right")

        boards = ttk.Frame(frame)
        boards.grid(row=2, column=0, sticky="nsew")
        boards.columnconfigure(0, weight=1)
        boards.columnconfigure(1, weight=1)
        boards.rowconfigure(0, weight=1)
        self._build_board(boards, board_id="logical", title="논리 배치", column=0)
        self._build_board(boards, board_id="physical", title="물리 배치", column=1)

        self.status_var = tk.StringVar(value=f"{node_id} PC의 모니터 맵을 편집 중입니다.")
        self.preview_var = tk.StringVar()
        footer = ttk.Frame(frame)
        footer.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(
            footer,
            textvariable=self.status_var,
            foreground="#555555",
            wraplength=920,
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            footer,
            textvariable=self.preview_var,
            foreground="#0f172a",
            wraplength=920,
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(buttons, text="검증", command=lambda: self.apply(False)).pack(
            side="left"
        )
        self._apply_button = ttk.Button(buttons, text="적용", command=lambda: self.apply(True))
        self._apply_button.pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="닫기", command=self.close).pack(side="right")
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self._refresh_ui()

    def apply(self, commit: bool):
        layout = self._layout_provider()
        if layout is None:
            return
        validation = validate_monitor_grids(self._logical_grid, self._physical_grid)
        if not validation.is_valid:
            self.status_var.set(f"검증 실패: {validation.errors[0]}")
            return
        try:
            candidate = replace_layout_monitors(
                layout,
                self._node_id,
                logical_rows=[list(row) for row in validation.logical_rows],
                physical_rows=[list(row) for row in validation.physical_rows],
            )
            overlaps = find_overlapping_nodes(candidate)
            if overlaps:
                raise ValueError("물리 배치 변경으로 PC가 겹칩니다.")
        except Exception as exc:
            self.status_var.set(f"검증 실패: {exc}")
            self.preview_var.set("")
            return

        node = candidate.get_node(self._node_id)
        self.preview_var.set(
            f"적용 전 미리보기: 물리 {node.width}x{node.height} | 논리 "
            f"{_grid_dimensions(validation.logical_rows)} | display {len(node.monitors().physical)}개"
        )
        if not commit:
            self.status_var.set("검증 성공: 현재 그리드 상태를 적용할 수 있습니다.")
            return
        if self._publish_layout(candidate, "모니터 맵을 실시간으로 적용했습니다."):
            logical_rows = monitor_topology_to_rows(node.monitors(), logical=True)
            physical_rows = monitor_topology_to_rows(node.monitors(), logical=False)
            min_rows = max(len(logical_rows), len(physical_rows), 3)
            min_cols = max(
                max((len(row) for row in logical_rows), default=0),
                max((len(row) for row in physical_rows), default=0),
                3,
            )
            self._base_logical_grid = monitor_grid_from_rows(
                logical_rows,
                min_rows=min_rows,
                min_cols=min_cols,
            )
            self._base_physical_grid = monitor_grid_from_rows(
                physical_rows,
                min_rows=min_rows,
                min_cols=min_cols,
            )
            self._logical_grid = self._base_logical_grid
            self._physical_grid = self._base_physical_grid
            self.status_var.set("적용 완료: 모니터 맵을 반영했습니다.")
            self._refresh_ui()
        else:
            self.status_var.set("적용 실패: 변경사항을 전송하지 못했습니다.")

    def close(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.destroy()

    def _build_board(self, parent, *, board_id: str, title: str, column: int):
        labelframe = self._ttk.LabelFrame(parent, text=title, padding=10)
        labelframe.grid(row=0, column=column, sticky="nsew", padx=(0, 8) if column == 0 else 0)
        labelframe.columnconfigure(0, weight=1)
        labelframe.rowconfigure(1, weight=1)

        actions = self._ttk.Frame(labelframe)
        actions.grid(row=0, column=0, sticky="ew")
        self._ttk.Button(
            actions,
            text="+ 행",
            command=lambda current=board_id: self._expand_board(current, rows=1),
        ).pack(side="left")
        self._ttk.Button(
            actions,
            text="+ 열",
            command=lambda current=board_id: self._expand_board(current, cols=1),
        ).pack(side="left", padx=(8, 0))
        self._ttk.Button(
            actions,
            text="선택 셀 비우기",
            command=lambda current=board_id: self._clear_selected_cell(current),
        ).pack(side="left", padx=(8, 0))
        self._ttk.Button(
            actions,
            text="빈칸 정리",
            command=lambda current=board_id: self._trim_board(current),
        ).pack(side="right")

        grid_frame = self._ttk.Frame(labelframe)
        grid_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        self._board_frames[board_id] = grid_frame

    def _refresh_ui(self):
        self._refresh_palette()
        self._refresh_board("logical", self._logical_grid)
        self._refresh_board("physical", self._physical_grid)
        validation = validate_monitor_grids(self._logical_grid, self._physical_grid)
        if validation.is_valid:
            physical_size = _grid_dimensions(validation.physical_rows)
            logical_size = _grid_dimensions(validation.logical_rows)
            self.preview_var.set(
                f"적용 전 미리보기: 물리 {physical_size} | 논리 {logical_size} | "
                f"display {len(validation.display_ids)}개"
            )
            self._apply_button.state(["!disabled"])
        else:
            self.preview_var.set("검증 필요: " + " / ".join(validation.errors))
            self._apply_button.state(["disabled"])

    def _refresh_palette(self):
        for child in self._palette_frame.winfo_children():
            child.destroy()
        self._display_buttons.clear()
        display_ids = list(
            dict.fromkeys(
                self._logical_grid.display_ids() + self._physical_grid.display_ids()
            )
        )
        if not display_ids:
            self._selected_display_id = None
            self._ttk.Label(
                self._palette_frame,
                text="display가 없습니다. 'Display 추가'를 눌러 시작하세요.",
            ).pack(side="left")
            return
        if self._selected_display_id not in display_ids:
            self._selected_display_id = display_ids[0]
        for display_id in display_ids:
            background, foreground = self._ensure_display_color(display_id)
            relief = "sunken" if display_id == self._selected_display_id else "raised"
            button = self._tk.Button(
                self._palette_frame,
                text=display_id,
                bg=background,
                fg=foreground,
                relief=relief,
                bd=2,
                width=6,
                command=lambda current=display_id: self._select_display(current),
            )
            button.pack(side="left", padx=(0, 6))
            self._display_buttons[display_id] = button

    def _refresh_board(self, board_id: str, grid: MonitorGridModel):
        frame = self._board_frames[board_id]
        for child in frame.winfo_children():
            child.destroy()
        self._board_buttons[board_id] = []
        for row_index, row in enumerate(grid.cells):
            frame.rowconfigure(row_index, weight=1)
            row_buttons = []
            for col_index, cell in enumerate(row):
                frame.columnconfigure(col_index, weight=1)
                background = "#ffffff"
                foreground = "#334155"
                text = "·"
                if cell is not None:
                    background, foreground = self._ensure_display_color(cell)
                    text = cell
                is_selected = self._selected_cell == (board_id, row_index, col_index)
                relief = "sunken" if is_selected else "raised"
                button = self._tk.Button(
                    frame,
                    text=text,
                    bg=background,
                    fg=foreground,
                    relief=relief,
                    bd=2,
                    width=6,
                    height=3,
                    command=lambda current_board=board_id, r=row_index, c=col_index: self._on_cell_click(
                        current_board, r, c
                    ),
                )
                button.grid(row=row_index, column=col_index, sticky="nsew", padx=3, pady=3)
                row_buttons.append(button)
            self._board_buttons[board_id].append(row_buttons)

    def _select_display(self, display_id: str):
        self._selected_display_id = display_id
        self.status_var.set(f"display {display_id}를 선택했습니다. 빈 칸을 눌러 이동하세요.")
        self._refresh_ui()

    def _on_cell_click(self, board_id: str, row: int, col: int):
        grid = self._grid_for(board_id)
        cell = grid.cells[row][col]
        self._selected_cell = (board_id, row, col)
        if cell is not None:
            self._selected_display_id = cell
            self.status_var.set(f"{board_id} 배치의 display {cell}를 선택했습니다.")
            self._refresh_ui()
            return
        if self._selected_display_id is None:
            self.status_var.set("먼저 이동할 display를 선택하세요.")
            self._refresh_ui()
            return
        self._set_grid(
            board_id,
            set_monitor_grid_cell(grid, row, col, self._selected_display_id),
        )
        self.status_var.set(
            f"{board_id} 배치에서 display {self._selected_display_id}를 ({row + 1}, {col + 1})로 이동했습니다."
        )
        self._refresh_ui()

    def _add_display(self):
        display_ids = tuple(
            dict.fromkeys(self._logical_grid.display_ids() + self._physical_grid.display_ids())
        )
        new_display_id = next_monitor_display_id(display_ids)
        self._logical_grid = self._place_in_first_empty(self._logical_grid, new_display_id)
        self._physical_grid = self._place_in_first_empty(self._physical_grid, new_display_id)
        self._selected_display_id = new_display_id
        self.status_var.set(f"display {new_display_id}를 추가했습니다.")
        self._refresh_ui()

    def _remove_selected_display(self):
        if self._selected_display_id is None:
            self.status_var.set("제거할 display를 먼저 선택하세요.")
            return
        removed = self._selected_display_id
        self._logical_grid = remove_display_from_grid(self._logical_grid, removed)
        self._physical_grid = remove_display_from_grid(self._physical_grid, removed)
        self._selected_cell = None
        remaining = self._logical_grid.display_ids() + self._physical_grid.display_ids()
        self._selected_display_id = None if not remaining else remaining[0]
        self.status_var.set(f"display {removed}를 제거했습니다.")
        self._refresh_ui()

    def _apply_preset_row(self):
        display_count = max(len(self._logical_grid.display_ids()), 1)
        self._apply_preset_grid(display_count, 1)

    def _apply_preset_grid(self, width: int, height: int):
        preset = build_monitor_preset(width, height)
        self._logical_grid = preset
        self._physical_grid = preset
        self._selected_cell = None
        ids = preset.display_ids()
        self._selected_display_id = None if not ids else ids[0]
        self.status_var.set(f"{width}x{height} preset을 적용했습니다.")
        self._refresh_ui()

    def _reset_to_base(self):
        self._logical_grid = self._base_logical_grid
        self._physical_grid = self._base_physical_grid
        self._selected_cell = None
        ids = self._logical_grid.display_ids() or self._physical_grid.display_ids()
        self._selected_display_id = None if not ids else ids[0]
        self.status_var.set("현재 저장된 모니터 맵으로 되돌렸습니다.")
        self._refresh_ui()

    def _expand_board(self, board_id: str, *, rows: int = 0, cols: int = 0):
        self._set_grid(
            board_id,
            expand_monitor_grid(self._grid_for(board_id), add_rows=rows, add_cols=cols),
        )
        self.status_var.set(f"{board_id} 배치 크기를 늘렸습니다.")
        self._refresh_ui()

    def _trim_board(self, board_id: str):
        self._set_grid(board_id, trim_monitor_grid(self._grid_for(board_id)))
        self.status_var.set(f"{board_id} 배치의 바깥 빈칸을 정리했습니다.")
        self._refresh_ui()

    def _clear_selected_cell(self, board_id: str):
        if self._selected_cell is None or self._selected_cell[0] != board_id:
            self.status_var.set(f"{board_id} 배치에서 먼저 셀을 선택하세요.")
            return
        _board, row, col = self._selected_cell
        self._set_grid(
            board_id,
            set_monitor_grid_cell(self._grid_for(board_id), row, col, None),
        )
        self.status_var.set(f"{board_id} 배치의 선택 셀을 비웠습니다.")
        self._refresh_ui()

    def _grid_for(self, board_id: str) -> MonitorGridModel:
        return self._logical_grid if board_id == "logical" else self._physical_grid

    def _set_grid(self, board_id: str, grid: MonitorGridModel):
        if board_id == "logical":
            self._logical_grid = grid
            return
        self._physical_grid = grid

    def _place_in_first_empty(
        self, grid: MonitorGridModel, display_id: str
    ) -> MonitorGridModel:
        current = grid
        while True:
            for row_index, row in enumerate(current.cells):
                for col_index, cell in enumerate(row):
                    if cell is None:
                        return set_monitor_grid_cell(current, row_index, col_index, display_id)
            current = expand_monitor_grid(current, add_cols=1)

    def _ensure_display_color(self, display_id: str) -> tuple[str, str]:
        if display_id not in self._display_colors:
            color = self.COLORS[len(self._display_colors) % len(self.COLORS)]
            self._display_colors[display_id] = color
        return self._display_colors[display_id]


def _grid_dimensions(rows: tuple[tuple[str | None, ...], ...]) -> str:
    width = max((len(row) for row in rows), default=0)
    height = len(rows)
    return f"{width}x{height}"


def _is_contiguous(rows: list[list[str | None]]) -> bool:
    occupied = {
        (row_index, col_index)
        for row_index, row in enumerate(rows)
        for col_index, cell in enumerate(row)
        if cell is not None
    }
    if not occupied:
        return False
    start = next(iter(occupied))
    seen = set()
    stack = [start]
    while stack:
        row, col = stack.pop()
        if (row, col) in seen:
            continue
        seen.add((row, col))
        for delta_row, delta_col in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            neighbor = (row + delta_row, col + delta_col)
            if neighbor in occupied and neighbor not in seen:
                stack.append(neighbor)
    return seen == occupied
