"""Dialog helpers and validation for layout editing."""

from __future__ import annotations

from dataclasses import dataclass

from runtime.layouts import (
    build_monitor_topology,
    find_overlapping_nodes,
    monitor_topology_to_rows,
    replace_auto_switch_settings,
    replace_layout_monitors,
)
from runtime.monitor_inventory import (
    compare_detected_and_physical_rows,
    describe_monitor_freshness,
    snapshot_to_logical_rows,
    summarize_monitor_diff,
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
        raise ValueError("모니터 맵에는 최소 한 줄이 필요합니다")
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


def monitor_grid_from_rows(rows: list[list[str | None]], *, min_rows: int = 1, min_cols: int = 1) -> MonitorGridModel:
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
        raise ValueError("모니터 맵에는 최소 하나의 디스플레이가 필요합니다")
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


def set_monitor_grid_cell(grid: MonitorGridModel, row: int, col: int, display_id: str | None) -> MonitorGridModel:
    rows = [list(current_row) for current_row in grid.cells]
    displaced = rows[row][col]
    previous_position = None
    if display_id is not None:
        display_id = display_id.strip()
        if not display_id:
            raise ValueError("디스플레이 ID가 비어 있습니다")
        for row_index, current_row in enumerate(rows):
            for col_index, cell in enumerate(current_row):
                if cell == display_id:
                    previous_position = (row_index, col_index)
                    rows[row_index][col_index] = None
    rows[row][col] = display_id
    if (
        display_id is not None
        and displaced is not None
        and displaced != display_id
        and previous_position is not None
    ):
        previous_row, previous_col = previous_position
        rows[previous_row][previous_col] = displaced
    return MonitorGridModel(cells=tuple(tuple(current_row) for current_row in rows))


def remove_display_from_grid(grid: MonitorGridModel, display_id: str | None) -> MonitorGridModel:
    if display_id is None:
        return grid
    rows = []
    for current_row in grid.cells:
        rows.append(tuple(None if cell == display_id else cell for cell in current_row))
    return MonitorGridModel(cells=tuple(rows))


def expand_monitor_grid(grid: MonitorGridModel, *, add_rows: int = 0, add_cols: int = 0) -> MonitorGridModel:
    new_rows = grid.rows + max(add_rows, 0)
    new_cols = grid.cols + max(add_cols, 0)
    return monitor_grid_from_rows([list(row) for row in grid.cells], min_rows=max(new_rows, 1), min_cols=max(new_cols, 1))


def append_monitor_grid_row(grid: MonitorGridModel) -> MonitorGridModel:
    return expand_monitor_grid(grid, add_rows=1)


def append_monitor_grid_col(grid: MonitorGridModel) -> MonitorGridModel:
    return expand_monitor_grid(grid, add_cols=1)


def remove_last_monitor_grid_row(grid: MonitorGridModel) -> MonitorGridModel:
    if grid.rows <= 1:
        raise ValueError("최소 한 행은 남아 있어야 합니다")
    if any(cell is not None for cell in grid.cells[-1]):
        raise ValueError("마지막 행이 비어 있지 않습니다")
    return MonitorGridModel(cells=grid.cells[:-1])


def remove_last_monitor_grid_col(grid: MonitorGridModel) -> MonitorGridModel:
    if grid.cols <= 1:
        raise ValueError("최소 한 열은 남아 있어야 합니다")
    if any(row[-1] is not None for row in grid.cells):
        raise ValueError("마지막 열이 비어 있지 않습니다")
    rows = [tuple(row[:-1]) for row in grid.cells]
    return MonitorGridModel(cells=tuple(rows))


def trim_monitor_grid(grid: MonitorGridModel, *, min_rows: int = 3, min_cols: int = 3) -> MonitorGridModel:
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


def validate_monitor_grids(logical_grid: MonitorGridModel, physical_grid: MonitorGridModel) -> MonitorGridValidation:
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
        errors.append("논리 배치는 끊기지 않고 이어져야 합니다")
    if physical_rows and not _is_contiguous(physical_rows):
        errors.append("물리 배치는 끊기지 않고 이어져야 합니다")
    display_ids = ()
    if logical_rows and physical_rows:
        try:
            topology = build_monitor_topology({"logical": logical_rows, "physical": physical_rows}, fallback_width=1, fallback_height=1)
        except ValueError as exc:
            errors.append(str(exc))
        else:
            display_ids = topology.display_ids()
    return MonitorGridValidation(logical_rows=tuple(tuple(row) for row in logical_rows), physical_rows=tuple(tuple(row) for row in physical_rows), display_ids=tuple(display_ids), errors=tuple(errors))


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
            raise ValueError(f"{key} 값을 입력해 주세요")
        try:
            value = int(raw) if kind == "integer" else float(raw)
        except ValueError as exc:
            raise ValueError(f"{key} 값은 숫자여야 합니다") from exc
        if value < minimum or (maximum is not None and value > maximum):
            if maximum is None:
                raise ValueError(f"{key} 값은 {minimum} 이상이어야 합니다")
            raise ValueError(f"{key} 값은 {minimum}에서 {maximum} 사이여야 합니다")
        parsed[key] = value
    return parsed


class AutoSwitchDialog:
    """Editor for auto-switch settings."""

    FIELDS = [
        ("edge_threshold", "경계 감지 범위"),
        ("warp_margin", "앵커 여백"),
        ("cooldown_ms", "쿨다운 (ms)"),
        ("return_guard_ms", "복귀 보호 (ms)"),
        ("anchor_dead_zone", "앵커 데드존"),
    ]

    def __init__(self, parent, layout_provider, publish_layout):
        import tkinter as tk
        from tkinter import ttk

        self._layout_provider = layout_provider
        self._publish_layout = publish_layout
        self.window = tk.Toplevel(parent)
        self.window.title("자동 전환 설정")
        self.window.geometry("440x340")
        frame = ttk.Frame(self.window, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        self._entries = {}
        layout = layout_provider()
        settings = layout.auto_switch
        for index, (key, label) in enumerate(self.FIELDS):
            ttk.Label(frame, text=label).grid(row=index, column=0, sticky="w", pady=4, padx=(0, 8))
            entry = ttk.Entry(frame)
            entry.grid(row=index, column=1, sticky="ew", pady=4)
            entry.insert(0, str(getattr(settings, key)))
            self._entries[key] = entry
        self.status_var = tk.StringVar(value="먼저 검증한 뒤 적용하면 새 설정이 반영됩니다.")
        ttk.Label(frame, textvariable=self.status_var, foreground="#555555", wraplength=390).grid(row=len(self.FIELDS), column=0, columnspan=2, sticky="w", pady=(12, 0))
        buttons = ttk.Frame(frame)
        buttons.grid(row=len(self.FIELDS) + 1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(buttons, text="검증", command=lambda: self.apply(False)).pack(side="left")
        ttk.Button(buttons, text="적용", command=lambda: self.apply(True)).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="닫기", command=self.close).pack(side="right")
        self.window.protocol("WM_DELETE_WINDOW", self.close)

    def apply(self, commit: bool):
        layout = self._layout_provider()
        if layout is None:
            return
        try:
            parsed = parse_auto_switch_form({key: entry.get() for key, entry in self._entries.items()})
            candidate = replace_auto_switch_settings(layout, **parsed)
        except Exception as exc:
            self.status_var.set(f"검증 실패: {exc}")
            return
        if not commit:
            self.status_var.set("검증이 끝났습니다. 값을 적용할 수 있습니다.")
            return
        if self._publish_layout(candidate, "자동 전환 설정을 반영했습니다."):
            self.status_var.set("자동 전환 설정을 적용했습니다.")
        else:
            self.status_var.set("적용 실패: 변경 사항을 전송하지 못했습니다.")

    def close(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.destroy()


def place_display_on_grid(grid: MonitorGridModel, display_id: str, row: int, col: int) -> MonitorGridModel:
    if row < 0 or col < 0:
        raise ValueError("위쪽이나 왼쪽으로는 행과 열을 추가할 수 없습니다")
    add_bottom = max(row - (grid.rows - 1), 0)
    add_right = max(col - (grid.cols - 1), 0)
    normalized = expand_monitor_grid(grid, add_rows=add_bottom, add_cols=add_right)
    return set_monitor_grid_cell(normalized, row, col, display_id)


class MonitorMapDialog:
    """Drag-first editor for real detected monitor maps."""

    DRAG_THRESHOLD_PX = 6
    CELL_MIN_SIZE = 92
    CELL_TEXT_WIDTH = 12
    CELL_TEXT_HEIGHT = 3
    CELL_BORDER_IDLE = "#d7dee8"
    CELL_BORDER_SELECTED = "#475569"
    CELL_BORDER_PREVIEW = "#1d4ed8"

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

    def __init__(
        self,
        parent,
        node_id: str,
        layout_provider,
        publish_layout,
        inventory_provider=None,
        refresh_inventory=None,
    ):
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self._ttk = ttk
        self._node_id = node_id
        self._layout_provider = layout_provider
        self._publish_layout = publish_layout
        self._inventory_provider = inventory_provider or (lambda _node_id: None)
        self._refresh_inventory = refresh_inventory
        self._selected_display_id = None
        self._drag_display_id = None
        self._board_frames = {}
        self._board_shapes = {}
        self._board_widgets = {}
        self._display_colors = {}
        self._board_control_buttons = []
        self._has_inventory = False
        self._history = []
        self._snapshot = None
        self._inventory_poll_job = None
        self.window = tk.Toplevel(parent)
        self.window.title(f"Monitor Map [{node_id}]")
        self.window.geometry("980x700")
        self.window.minsize(900, 620)
        self._detected_rows = []
        self._base_logical_grid = monitor_grid_from_rows([], min_rows=1, min_cols=1)
        self._base_physical_grid = monitor_grid_from_rows([], min_rows=1, min_cols=1)
        self._logical_grid = self._base_logical_grid
        self._physical_grid = self._base_physical_grid
        self._press_display_id = None
        self._press_origin = None
        self._drag_display_id = None
        self._drag_hover = None

        frame = ttk.Frame(self.window, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        ttk.Label(
            frame,
            text="실제 감지된 모니터만 사용합니다.",
            wraplength=920,
        ).grid(row=0, column=0, sticky="w")
        palette = ttk.LabelFrame(frame, text="디스플레이", padding=10)
        palette.grid(row=1, column=0, sticky="ew", pady=(10, 10))
        palette.columnconfigure(0, weight=1)
        self._palette_frame = ttk.Frame(palette)
        self._palette_frame.grid(row=0, column=0, sticky="ew")
        palette_actions = ttk.Frame(palette)
        palette_actions.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self._reload_detected_button = ttk.Button(
            palette_actions,
            text="실제 감지 다시 불러오기",
            command=self._reload_detected_inventory,
        )
        self._reload_detected_button.pack(side="left")
        self._reset_button = ttk.Button(
            palette_actions,
            text="마지막 저장으로 되돌리기",
            command=self._reset_to_base,
        )
        self._reset_button.pack(side="right")
        boards = ttk.Frame(frame)
        boards.grid(row=2, column=0, sticky="nsew")
        boards.columnconfigure(0, weight=1)
        boards.columnconfigure(1, weight=1)
        boards.rowconfigure(0, weight=1)
        self._build_board(boards, board_id="logical", title="논리 배치", column=0)
        self._build_board(boards, board_id="physical", title="물리 배치", column=1)
        self.status_var = tk.StringVar(value=f"{node_id} PC의 모니터 맵을 편집하는 중입니다.")
        self.diff_var = tk.StringVar()
        self.preview_var = tk.StringVar()
        footer = ttk.Frame(frame)
        footer.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_var, foreground="#555555", wraplength=920).grid(row=0, column=0, sticky="w")
        ttk.Label(footer, textvariable=self.diff_var, foreground="#7c2d12", wraplength=920).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(footer, textvariable=self.preview_var, foreground="#0f172a", wraplength=920).grid(row=2, column=0, sticky="w", pady=(4, 0))
        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(buttons, text="검증", command=lambda: self.apply(False)).pack(side="left")
        self._undo_button = ttk.Button(buttons, text="직전 편집 되돌리기", command=self._undo_last_change)
        self._undo_button.pack(side="left", padx=(8, 0))
        self._apply_button = ttk.Button(buttons, text="적용", command=lambda: self.apply(True))
        self._apply_button.pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="닫기", command=self.close).pack(side="right")
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<B1-Motion>", self._on_drag_motion, add="+")
        self.window.bind("<ButtonRelease-1>", self._on_drag_release, add="+")
        self._load_detected_state()
        self._refresh_ui()
        self._schedule_inventory_poll()

    def apply(self, commit: bool):
        if not self._has_inventory:
            self.status_var.set("실제 모니터 감지 후에만 모니터 맵을 저장할 수 있습니다.")
            return
        layout = self._layout_provider()
        if layout is None:
            return
        validation = validate_monitor_grids(self._logical_grid, self._physical_grid)
        if not validation.is_valid:
            self.status_var.set(f"검증 실패: {validation.errors[0]}")
            return
        try:
            candidate = replace_layout_monitors(layout, self._node_id, logical_rows=[list(row) for row in validation.logical_rows], physical_rows=[list(row) for row in validation.physical_rows])
            overlaps = find_overlapping_nodes(candidate)
            if overlaps:
                raise ValueError("물리 배치 변경 결과가 다른 PC와 겹칩니다")
        except Exception as exc:
            self.status_var.set(f"검증 실패: {exc}")
            self.preview_var.set("")
            return
        node = candidate.get_node(self._node_id)
        self.preview_var.set(f"미리보기: 물리 {node.width}x{node.height} | 논리 {_grid_dimensions(validation.logical_rows)} | 모니터 {len(node.monitors().physical)}개")
        if not commit:
            self.status_var.set("검증이 끝났습니다. 현재 배치를 적용할 수 있습니다.")
            return
        if self._publish_layout(candidate, "모니터 맵을 반영했습니다."):
            logical_rows = monitor_topology_to_rows(node.monitors(), logical=True)
            physical_rows = monitor_topology_to_rows(node.monitors(), logical=False)
            min_rows = max(len(logical_rows), len(physical_rows), 1)
            min_cols = max(
                max((len(row) for row in logical_rows), default=0),
                max((len(row) for row in physical_rows), default=0),
                1,
            )
            self._base_logical_grid = monitor_grid_from_rows(logical_rows, min_rows=min_rows, min_cols=min_cols)
            self._base_physical_grid = monitor_grid_from_rows(physical_rows, min_rows=min_rows, min_cols=min_cols)
            self._logical_grid = self._base_logical_grid
            self._physical_grid = self._base_physical_grid
            self._history.clear()
            self.status_var.set("모니터 맵 변경을 적용했습니다.")
            self._refresh_ui()
        else:
            self.status_var.set("적용 실패: 레이아웃 변경을 전송하지 못했습니다.")

    def close(self):
        if self._inventory_poll_job is not None and hasattr(self.window, "after_cancel"):
            self.window.after_cancel(self._inventory_poll_job)
            self._inventory_poll_job = None
        if self.window is not None and self.window.winfo_exists():
            self.window.destroy()

    def _build_board(self, parent, *, board_id: str, title: str, column: int):
        labelframe = self._ttk.LabelFrame(parent, text=title, padding=10)
        labelframe.grid(row=0, column=column, sticky="nsew", padx=(0, 8) if column == 0 else 0)
        labelframe.columnconfigure(0, weight=1)
        labelframe.rowconfigure(0, weight=1)
        side_controls = self._ttk.Frame(labelframe)
        side_controls.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        bottom_controls = self._ttk.Frame(labelframe)
        bottom_controls.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        add_col = self._ttk.Button(
            side_controls,
            text="열 +",
            command=lambda current=board_id: self._append_col(current),
            width=6,
        )
        add_col.pack(side="top", fill="x")
        add_row = self._ttk.Button(
            bottom_controls,
            text="행 +",
            command=lambda current=board_id: self._append_row(current),
            width=6,
        )
        add_row.pack(side="left")
        remove_col = self._ttk.Button(
            side_controls,
            text="열 -",
            command=lambda current=board_id: self._remove_col(current),
            width=6,
        )
        remove_col.pack(side="top", fill="x", pady=(8, 0))
        remove_row = self._ttk.Button(
            bottom_controls,
            text="행 -",
            command=lambda current=board_id: self._remove_row(current),
            width=6,
        )
        remove_row.pack(side="left", padx=(8, 0))
        self._board_control_buttons.extend((add_col, add_row, remove_col, remove_row))
        grid_frame = self._ttk.Frame(labelframe)
        grid_frame.grid(row=0, column=0, sticky="nsew")
        self._board_frames[board_id] = grid_frame

    def _refresh_ui(self):
        self._refresh_palette()
        self._refresh_board("logical")
        self._refresh_board("physical")
        self._set_controls_enabled(self._has_inventory)
        self._update_preview_state()

    def _update_preview_state(self):
        if not self._has_inventory:
            self.diff_var.set("실제 모니터 감지 정보가 없어 비교할 수 없습니다.")
            self.preview_var.set("실제 모니터 감지 정보가 없어 보드를 열 수 없습니다.")
            self._apply_button.state(["disabled"])
            return
        validation = validate_monitor_grids(self._logical_grid, self._physical_grid)
        self.diff_var.set(self._current_diff_text())
        if validation.is_valid:
            drag_summary = ""
            if self._drag_display_id is not None and self._drag_hover is not None:
                board_id, row, col = self._drag_hover
                board_label = "논리" if board_id == "logical" else "물리"
                drag_summary = f" | 드래그 미리보기: {board_label} ({row + 1}, {col + 1})"
            self.preview_var.set(
                f"미리보기: 물리 {_grid_dimensions(validation.physical_rows)} | 논리 {_grid_dimensions(validation.logical_rows)} | 모니터 {len(validation.display_ids)}개{drag_summary}"
            )
            self._apply_button.state(["!disabled"])
        else:
            self.preview_var.set("검증 필요: " + " / ".join(validation.errors))
            self._apply_button.state(["disabled"])

    def _refresh_drag_preview(self):
        self._refresh_board("logical")
        self._refresh_board("physical")
        self._update_preview_state()

    def _refresh_palette(self):
        for child in self._palette_frame.winfo_children():
            child.destroy()
        if not self._has_inventory:
            self._selected_display_id = None
            self._ttk.Label(
                self._palette_frame,
                text="이 PC의 실제 모니터 감지 정보가 필요합니다.",
            ).pack(side="left")
            return
        display_ids = list(dict.fromkeys(self._logical_grid.display_ids() + self._physical_grid.display_ids()))
        if not display_ids:
            self._selected_display_id = None
            self._ttk.Label(self._palette_frame, text="감지된 모니터가 없습니다.").pack(side="left")
            return
        if self._selected_display_id not in display_ids:
            self._selected_display_id = display_ids[0]
        for display_id in display_ids:
            background, foreground = self._ensure_display_color(display_id)
            border = 3 if display_id == self._selected_display_id else 1
            chip = self._tk.Label(self._palette_frame, text=display_id, bg=background, fg=foreground, bd=border, relief="solid", padx=14, pady=8)
            chip.pack(side="left", padx=(0, 8))
            chip.bind("<ButtonPress-1>", lambda event, current=display_id: self._begin_press(event, current))

    def _refresh_board(self, board_id: str):
        frame = self._board_frames[board_id]
        if not self._has_inventory:
            if self._board_shapes.get(board_id) != ("empty",):
                for child in frame.winfo_children():
                    child.destroy()
                self._ttk.Label(
                    frame,
                    text="실제 모니터 감지 후 편집할 수 있습니다.",
                ).grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
                self._board_shapes[board_id] = ("empty",)
                self._board_widgets[board_id] = {}
            return
        grid = self._preview_grid(board_id)
        shape = (grid.rows, grid.cols)
        if self._board_shapes.get(board_id) != shape:
            for child in frame.winfo_children():
                child.destroy()
            widgets = {}
            for row in range(grid.rows):
                frame.rowconfigure(
                    row,
                    weight=1,
                    uniform=f"{board_id}-rows",
                    minsize=self.CELL_MIN_SIZE,
                )
            for col in range(grid.cols):
                frame.columnconfigure(
                    col,
                    weight=1,
                    uniform=f"{board_id}-cols",
                    minsize=self.CELL_MIN_SIZE,
                )
            for row in range(grid.rows):
                for col in range(grid.cols):
                    label = self._tk.Label(
                        frame,
                        width=self.CELL_TEXT_WIDTH,
                        height=self.CELL_TEXT_HEIGHT,
                        anchor="center",
                        justify="center",
                        padx=14,
                        pady=18,
                        bd=1,
                        relief="solid",
                        highlightthickness=2,
                    )
                    label.grid(row=row, column=col, sticky="nsew", padx=3, pady=3)
                    label._cell_state = None
                    widgets[(row, col)] = label
            self._board_shapes[board_id] = shape
            self._board_widgets[board_id] = widgets
        for (row, col), label in self._board_widgets.get(board_id, {}).items():
            cell = grid.cells[row][col]
            background = "#ffffff"
            foreground = "#334155"
            text = " "
            if cell is not None:
                background, foreground = self._ensure_display_color(cell)
                text = cell
            is_preview_target = (
                self._drag_display_id is not None
                and self._drag_hover == (board_id, row, col)
            )
            border_color = (
                self.CELL_BORDER_PREVIEW
                if is_preview_target
                else self.CELL_BORDER_SELECTED
                if cell is not None and cell == self._selected_display_id
                else self.CELL_BORDER_IDLE
            )
            state = (text, background, foreground, border_color)
            if label._cell_state != state:
                label.configure(
                    text=text,
                    bg=background,
                    fg=foreground,
                    highlightbackground=border_color,
                    highlightcolor=border_color,
                )
                label._cell_state = state
            label._monitor_target = (board_id, row, col)
            label.unbind("<ButtonPress-1>")
            if cell is not None:
                label.bind("<ButtonPress-1>", lambda event, current=cell: self._begin_press(event, current))

    def _begin_press(self, event, display_id: str):
        if not self._has_inventory:
            return
        self._selected_display_id = display_id
        self._press_display_id = display_id
        self._press_origin = (event.x_root, event.y_root)
        self._drag_display_id = None
        self._drag_hover = None
        self.status_var.set(f"디스플레이 {display_id}를 선택했습니다.")

    def _on_drag_motion(self, event):
        if (
            not self._has_inventory
            or self._press_display_id is None
            or self._press_origin is None
        ):
            return
        if self._drag_display_id is None:
            delta_x = abs(event.x_root - self._press_origin[0])
            delta_y = abs(event.y_root - self._press_origin[1])
            if max(delta_x, delta_y) < self.DRAG_THRESHOLD_PX:
                return
            self._drag_display_id = self._press_display_id
        hover = self._resolve_hover_target(event.x_root, event.y_root)
        if hover == self._drag_hover:
            return
        self._drag_hover = hover
        if hover is None:
            self.status_var.set(f"디스플레이 {self._drag_display_id}를 놓을 칸으로 옮기세요.")
        else:
            board_id, row, col = hover
            board_label = "논리" if board_id == "logical" else "물리"
            self.status_var.set(
                f"디스플레이 {self._drag_display_id} 미리보기: {board_label} 배치 {row + 1}행 {col + 1}열"
            )
        self._refresh_drag_preview()

    def _on_drag_release(self, event):
        if not self._has_inventory:
            self._clear_drag_state()
            return
        display_id = self._drag_display_id or self._press_display_id
        if display_id is None:
            return
        was_dragging = self._drag_display_id is not None
        hover = self._resolve_hover_target(event.x_root, event.y_root)
        self._selected_display_id = display_id
        if was_dragging and hover is not None:
            board_id, row, col = hover
            self._set_grid(board_id, place_display_on_grid(self._grid_for(board_id), display_id, row, col))
            board_label = "논리" if board_id == "logical" else "물리"
            self.status_var.set(f"디스플레이 {display_id}를 {board_label} 배치 {row + 1}행 {col + 1}열로 옮겼습니다.")
        elif was_dragging:
            self.status_var.set("디스플레이 이동을 취소했습니다.")
        else:
            self.status_var.set(f"디스플레이 {display_id}를 선택했습니다.")
        self._clear_drag_state()
        self._refresh_ui()

    def _reset_to_base(self):
        self._history.clear()
        self._logical_grid = self._base_logical_grid
        self._physical_grid = self._base_physical_grid
        ids = self._logical_grid.display_ids() or self._physical_grid.display_ids()
        self._selected_display_id = None if not ids else ids[0]
        self.status_var.set("마지막 저장 상태로 되돌렸습니다.")
        self._refresh_ui()

    def _reload_detected_inventory(self):
        snapshot = None
        status_message = None
        if callable(self._refresh_inventory):
            refresh_result = self._refresh_inventory(self._node_id)
            if isinstance(refresh_result, tuple):
                snapshot, status_message = refresh_result
            else:
                snapshot = refresh_result
        else:
            snapshot = self._inventory_provider(self._node_id)
        if snapshot is None:
            snapshot = self._inventory_provider(self._node_id)
        self._load_detected_state(snapshot=snapshot)
        self._history.clear()
        if status_message:
            self.status_var.set(status_message)
        self._refresh_ui()

    def _append_row(self, board_id: str):
        if not self._has_inventory:
            return
        self._set_grid(board_id, append_monitor_grid_row(self._grid_for(board_id)))
        self.status_var.set("아래쪽에 행을 추가했습니다.")
        self._refresh_ui()

    def _append_col(self, board_id: str):
        if not self._has_inventory:
            return
        self._set_grid(board_id, append_monitor_grid_col(self._grid_for(board_id)))
        self.status_var.set("오른쪽에 열을 추가했습니다.")
        self._refresh_ui()

    def _remove_row(self, board_id: str):
        if not self._has_inventory:
            return
        try:
            self._set_grid(board_id, remove_last_monitor_grid_row(self._grid_for(board_id)))
        except ValueError as exc:
            self.status_var.set(f"행 삭제 실패: {exc}")
            return
        self.status_var.set("마지막 행을 삭제했습니다.")
        self._refresh_ui()

    def _remove_col(self, board_id: str):
        if not self._has_inventory:
            return
        try:
            self._set_grid(board_id, remove_last_monitor_grid_col(self._grid_for(board_id)))
        except ValueError as exc:
            self.status_var.set(f"열 삭제 실패: {exc}")
            return
        self.status_var.set("마지막 열을 삭제했습니다.")
        self._refresh_ui()

    def _grid_for(self, board_id: str) -> MonitorGridModel:
        return self._logical_grid if board_id == "logical" else self._physical_grid

    def _set_grid(self, board_id: str, grid: MonitorGridModel):
        current = self._grid_for(board_id)
        if current == grid:
            return
        self._push_history()
        if board_id == "logical":
            self._logical_grid = grid
            return
        self._physical_grid = grid

    def _preview_grid(self, board_id: str) -> MonitorGridModel:
        grid = self._grid_for(board_id)
        if not self._has_inventory or self._drag_display_id is None or self._drag_hover is None:
            return grid
        hover_board_id, row, col = self._drag_hover
        if hover_board_id != board_id:
            return grid
        try:
            return place_display_on_grid(grid, self._drag_display_id, row, col)
        except ValueError:
            return grid

    def _ensure_display_color(self, display_id: str) -> tuple[str, str]:
        if display_id not in self._display_colors:
            color = self.COLORS[len(self._display_colors) % len(self.COLORS)]
            self._display_colors[display_id] = color
        return self._display_colors[display_id]

    def _load_detected_state(self, snapshot=None):
        node = self._layout_provider().get_node(self._node_id)
        snapshot = self._inventory_provider(self._node_id) if snapshot is None else snapshot
        self._snapshot = snapshot
        if snapshot is None or not snapshot.monitors:
            self._has_inventory = False
            self._detected_rows = []
            self._base_logical_grid = monitor_grid_from_rows([], min_rows=1, min_cols=1)
            self._base_physical_grid = monitor_grid_from_rows([], min_rows=1, min_cols=1)
            self._logical_grid = self._base_logical_grid
            self._physical_grid = self._base_physical_grid
            self.status_var.set("실제 모니터 감지 정보가 없습니다. 먼저 이 PC에서 모니터 감지가 필요합니다.")
            return
        self._has_inventory = True
        logical_rows = snapshot_to_logical_rows(snapshot)
        physical_rows = logical_rows
        if node is not None:
            candidate_rows = monitor_topology_to_rows(node.monitors(), logical=False)
            if _display_id_set(candidate_rows) == _display_id_set(logical_rows):
                physical_rows = candidate_rows
        min_rows = max(len(logical_rows), len(physical_rows), 1)
        min_cols = max(
            max((len(row) for row in logical_rows), default=0),
            max((len(row) for row in physical_rows), default=0),
            1,
        )
        self._detected_rows = logical_rows
        self._base_logical_grid = monitor_grid_from_rows(logical_rows, min_rows=min_rows, min_cols=min_cols)
        self._base_physical_grid = monitor_grid_from_rows(physical_rows, min_rows=min_rows, min_cols=min_cols)
        self._logical_grid = self._base_logical_grid
        self._physical_grid = self._base_physical_grid
        ids = self._logical_grid.display_ids() or self._physical_grid.display_ids()
        self._selected_display_id = None if not ids else ids[0]
        self.status_var.set("실제 감지된 모니터 기준으로 모니터 맵을 열었습니다.")

    def _set_controls_enabled(self, enabled: bool):
        for button in self._board_control_buttons:
            if hasattr(button, "state"):
                button.state(["!disabled"] if enabled else ["disabled"])
        self._reload_detected_button.state(["!disabled"])
        self._reset_button.state(["!disabled"] if enabled else ["disabled"])
        self._undo_button.state(["!disabled"] if enabled and self._history else ["disabled"])

    def _resolve_hover_target(self, x_root: int, y_root: int):
        widget = self.window.winfo_containing(x_root, y_root)
        while widget is not None:
            target = getattr(widget, "_monitor_target", None)
            if target is not None:
                return target
            widget = getattr(widget, "master", None)
        for board_id, frame in self._board_frames.items():
            if frame is None or not frame.winfo_exists():
                continue
            left = frame.winfo_rootx()
            top = frame.winfo_rooty()
            width = frame.winfo_width()
            height = frame.winfo_height()
            if not (left <= x_root < left + width and top <= y_root < top + height):
                continue
            shape = self._board_shapes.get(board_id)
            if not isinstance(shape, tuple) or len(shape) != 2:
                return None
            cell = _cell_from_relative_position(
                x=x_root - left,
                y=y_root - top,
                width=width,
                height=height,
                rows=shape[0],
                cols=shape[1],
            )
            if cell is None:
                return None
            row, col = cell
            return board_id, row, col
        return None

    def _clear_drag_state(self):
        self._press_display_id = None
        self._press_origin = None
        self._drag_display_id = None
        self._drag_hover = None

    def _undo_last_change(self):
        if not self._history:
            self.status_var.set("되돌릴 직전 편집이 없습니다.")
            self._refresh_ui()
            return
        logical_grid, physical_grid = self._history.pop()
        self._logical_grid = logical_grid
        self._physical_grid = physical_grid
        ids = self._logical_grid.display_ids() or self._physical_grid.display_ids()
        self._selected_display_id = None if not ids else ids[0]
        self.status_var.set("직전 편집을 되돌렸습니다.")
        self._refresh_ui()

    def _push_history(self):
        snapshot = (self._logical_grid, self._physical_grid)
        if self._history and self._history[-1] == snapshot:
            return
        self._history.append(snapshot)
        if len(self._history) > 20:
            self._history = self._history[-20:]

    def _current_diff_text(self) -> str:
        if self._snapshot is None or not self._snapshot.monitors:
            return "실제 모니터 감지 정보가 없습니다."
        freshness = describe_monitor_freshness(self._snapshot, online=True)
        detected_rows = snapshot_to_logical_rows(self._snapshot)
        try:
            physical_rows = monitor_grid_to_rows(self._physical_grid)
        except ValueError as exc:
            return f"감지 상태: {freshness.label} | {freshness.detail} | {exc}"
        diff = compare_detected_and_physical_rows(detected_rows, physical_rows)
        return f"감지 상태: {freshness.label} | {freshness.detail} | {summarize_monitor_diff(diff)}"

    def _schedule_inventory_poll(self):
        if self.window is None or not self.window.winfo_exists() or not hasattr(self.window, "after"):
            return
        self._inventory_poll_job = self.window.after(1000, self._poll_inventory_snapshot)

    def _poll_inventory_snapshot(self):
        self._inventory_poll_job = None
        if self.window is None or not self.window.winfo_exists():
            return
        latest_snapshot = self._inventory_provider(self._node_id)
        latest_token = None if latest_snapshot is None else (
            latest_snapshot.captured_at,
            tuple(
                (
                    monitor.display_id,
                    monitor.x,
                    monitor.y,
                    monitor.width,
                    monitor.height,
                )
                for monitor in latest_snapshot.monitors
            ),
        )
        current_token = None if self._snapshot is None else (
            self._snapshot.captured_at,
            tuple(
                (
                    monitor.display_id,
                    monitor.x,
                    monitor.y,
                    monitor.width,
                    monitor.height,
                )
                for monitor in self._snapshot.monitors
            ),
        )
        if latest_token != current_token:
            self._load_detected_state(snapshot=latest_snapshot)
            self._history.clear()
            self.status_var.set("최신 모니터 감지 정보로 보드를 다시 맞췄습니다.")
            self._refresh_ui()
        self._schedule_inventory_poll()


def _grid_dimensions(rows: tuple[tuple[str | None, ...], ...]) -> str:
    width = max((len(row) for row in rows), default=0)
    height = len(rows)
    return f"{width}x{height}"


def _cell_from_relative_position(
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    rows: int,
    cols: int,
) -> tuple[int, int] | None:
    if width <= 0 or height <= 0 or rows <= 0 or cols <= 0:
        return None
    clamped_x = min(max(x, 0), width - 1)
    clamped_y = min(max(y, 0), height - 1)
    col = min((clamped_x * cols) // width, cols - 1)
    row = min((clamped_y * rows) // height, rows - 1)
    return row, col


def _display_id_set(rows: list[list[str | None]]) -> set[str]:
    seen = set()
    for row in rows:
        for cell in row:
            if cell not in (None, "", "."):
                seen.add(str(cell).strip())
    return seen


def _is_contiguous(rows: list[list[str | None]]) -> bool:
    occupied = {(row_index, col_index) for row_index, row in enumerate(rows) for col_index, cell in enumerate(row) if cell is not None}
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
