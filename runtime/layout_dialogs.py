"""Qt dialogs and pure helpers for layout and monitor editing."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from runtime.gui_style import PALETTE
from runtime.layouts import AutoSwitchSettings, monitor_topology_to_rows
from runtime.monitor_inventory import (
    compare_detected_and_physical_rows,
    describe_monitor_freshness,
    snapshot_to_logical_rows,
    summarize_monitor_diff,
)

EMPTY_TOKENS = {None, "", "."}


@dataclass(frozen=True)
class MonitorGrid:
    cells: tuple[tuple[str | None, ...], ...]
    rows: int
    cols: int
    min_rows: int
    min_cols: int


@dataclass(frozen=True)
class MonitorGridValidation:
    is_valid: bool
    errors: tuple[str, ...]
    display_ids: tuple[str, ...]


def parse_monitor_grid_text(text: str) -> list[list[str | None]]:
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        row = []
        for token in line.split():
            value = token.strip()
            row.append(None if value in ("", ".") else value)
        rows.append(row)
    return rows


def format_monitor_grid_text(rows: list[list[str | None]]) -> str:
    formatted = []
    for row in rows:
        formatted.append(" ".join("." if cell in EMPTY_TOKENS else str(cell) for cell in row))
    return "\n".join(formatted)


def monitor_grid_from_rows(
    rows: list[list[str | None]],
    *,
    min_rows: int | None = None,
    min_cols: int | None = None,
) -> MonitorGrid:
    normalized = [[None if cell in EMPTY_TOKENS else str(cell).strip() for cell in row] for row in rows]
    row_count = max(len(normalized), min_rows or 0, 1)
    col_count = max([*(len(row) for row in normalized), min_cols or 0, 1])
    padded = []
    for row_index in range(row_count):
        base = list(normalized[row_index]) if row_index < len(normalized) else []
        base.extend([None] * (col_count - len(base)))
        padded.append(tuple(base[:col_count]))
    return MonitorGrid(
        cells=tuple(padded),
        rows=row_count,
        cols=col_count,
        min_rows=max(min_rows or row_count, 1),
        min_cols=max(min_cols or col_count, 1),
    )


def build_monitor_preset(cols: int, rows: int) -> MonitorGrid:
    next_id = 1
    payload = []
    for _row in range(rows):
        current = []
        for _col in range(cols):
            current.append(str(next_id))
            next_id += 1
        payload.append(current)
    return monitor_grid_from_rows(payload, min_rows=rows, min_cols=cols)


def append_monitor_grid_row(grid: MonitorGrid) -> MonitorGrid:
    rows = [list(row) for row in grid.cells]
    rows.append([None] * grid.cols)
    return monitor_grid_from_rows(rows, min_rows=grid.min_rows, min_cols=grid.min_cols)


def append_monitor_grid_col(grid: MonitorGrid) -> MonitorGrid:
    rows = [list(row) + [None] for row in grid.cells]
    return monitor_grid_from_rows(rows, min_rows=grid.min_rows, min_cols=grid.min_cols)


def remove_last_monitor_grid_row(grid: MonitorGrid) -> MonitorGrid:
    if grid.rows <= grid.min_rows:
        raise ValueError("마지막 행을 더 이상 줄일 수 없습니다.")
    if any(cell not in EMPTY_TOKENS for cell in grid.cells[-1]):
        raise ValueError("마지막 행이 비어 있을 때만 삭제할 수 있습니다.")
    rows = [list(row) for row in grid.cells[:-1]]
    return monitor_grid_from_rows(rows, min_rows=grid.min_rows, min_cols=grid.min_cols)


def remove_last_monitor_grid_col(grid: MonitorGrid) -> MonitorGrid:
    if grid.cols <= grid.min_cols:
        raise ValueError("마지막 열을 더 이상 줄일 수 없습니다.")
    if any(row[-1] not in EMPTY_TOKENS for row in grid.cells):
        raise ValueError("마지막 열이 비어 있을 때만 삭제할 수 있습니다.")
    rows = [list(row[:-1]) for row in grid.cells]
    return monitor_grid_from_rows(rows, min_rows=grid.min_rows, min_cols=grid.min_cols)


def set_monitor_grid_cell(grid: MonitorGrid, row: int, col: int, value: str | None) -> MonitorGrid:
    rows = [list(current) for current in grid.cells]
    next_value = None if value in EMPTY_TOKENS else str(value)
    if next_value:
        for r_index, current_row in enumerate(rows):
            for c_index, current in enumerate(current_row):
                if current == next_value:
                    rows[r_index][c_index] = rows[row][col]
    rows[row][col] = next_value
    return monitor_grid_from_rows(rows, min_rows=grid.min_rows, min_cols=grid.min_cols)


def place_display_on_grid(grid: MonitorGrid, display_id: str, row: int, col: int) -> MonitorGrid:
    if row < 0 or col < 0:
        raise ValueError("위쪽이나 왼쪽으로는 확장할 수 없습니다.")
    next_grid = grid
    while row >= next_grid.rows:
        next_grid = append_monitor_grid_row(next_grid)
    while col >= next_grid.cols:
        next_grid = append_monitor_grid_col(next_grid)
    return set_monitor_grid_cell(next_grid, row, col, display_id)


def _normalize_rows(rows: list[list[str | None]]) -> list[list[str | None]]:
    return [[None if cell in EMPTY_TOKENS else str(cell).strip() for cell in row] for row in rows]


def _display_id_positions(rows: list[list[str | None]]) -> dict[str, tuple[int, int]]:
    positions = {}
    for row_index, row in enumerate(rows):
        for col_index, cell in enumerate(row):
            if cell in EMPTY_TOKENS:
                continue
            positions[str(cell)] = (row_index, col_index)
    return positions


def _grid_is_contiguous(rows: list[list[str | None]]) -> bool:
    positions = _display_id_positions(rows)
    if not positions:
        return True
    seen = set()
    pending = [next(iter(positions.values()))]
    occupied = set(positions.values())
    while pending:
        cell = pending.pop()
        if cell in seen:
            continue
        seen.add(cell)
        row, col = cell
        for neighbor in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)):
            if neighbor in occupied and neighbor not in seen:
                pending.append(neighbor)
    return seen == occupied


def validate_monitor_grids(logical: MonitorGrid, physical: MonitorGrid) -> MonitorGridValidation:
    errors: list[str] = []
    logical_rows = [list(row) for row in logical.cells]
    physical_rows = [list(row) for row in physical.cells]
    logical_ids = [cell for row in logical_rows for cell in row if cell not in EMPTY_TOKENS]
    physical_ids = [cell for row in physical_rows for cell in row if cell not in EMPTY_TOKENS]
    duplicates = [display_id for display_id, count in Counter(logical_ids + physical_ids).items() if count > 2]
    if duplicates:
        errors.append(f"중복된 display id가 있습니다: {', '.join(sorted(duplicates))}")
    if set(logical_ids) != set(physical_ids):
        errors.append("논리 배치와 물리 배치는 같은 display id 집합을 사용해야 합니다.")
    if not _grid_is_contiguous(logical_rows):
        errors.append("논리 배치는 끊기지 않고 이어져야 합니다.")
    if not _grid_is_contiguous(physical_rows):
        errors.append("물리 배치는 끊기지 않고 이어져야 합니다.")
    return MonitorGridValidation(
        is_valid=not errors,
        errors=tuple(errors),
        display_ids=tuple(sorted(set(logical_ids))),
    )


def _cell_from_relative_position(
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    rows: int,
    cols: int,
) -> tuple[int, int]:
    row = min(max(int((max(y, 0.0) / max(height, 1.0)) * rows), 0), max(rows - 1, 0))
    col = min(max(int((max(x, 0.0) / max(width, 1.0)) * cols), 0), max(cols - 1, 0))
    return row, col


def parse_auto_switch_form(values: dict[str, str]) -> dict[str, float | int]:
    parsed = {
        "cooldown_ms": int(values["cooldown_ms"]),
        "return_guard_ms": int(values["return_guard_ms"]),
    }
    if parsed["cooldown_ms"] < 0:
        raise ValueError("cooldown_ms must be non-negative")
    if parsed["return_guard_ms"] < 0:
        raise ValueError("return_guard_ms must be non-negative")
    return parsed


class AutoSwitchDialog(QDialog):
    def __init__(self, parent, settings: AutoSwitchSettings, on_apply):
        super().__init__(parent)
        self.setWindowTitle("자동 전환 설정")
        self._on_apply = on_apply
        self._cooldown = QSpinBox()
        self._cooldown.setRange(0, 5000)
        self._cooldown.setValue(settings.cooldown_ms)
        self._return_guard = QSpinBox()
        self._return_guard.setRange(0, 5000)
        self._return_guard.setValue(settings.return_guard_ms)
        root = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("전환 대기(ms)", self._cooldown)
        form.addRow("복귀 보호(ms)", self._return_guard)
        root.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _accept(self):
        self._on_apply(
            parse_auto_switch_form(
                {
                    "cooldown_ms": str(self._cooldown.value()),
                    "return_guard_ms": str(self._return_guard.value()),
                }
            )
        )
        self.accept()


class MonitorBoardView(QGraphicsView):
    commitRequested = Signal(object)
    statusChanged = Signal(str)

    CELL_SIZE = 84
    GAP = 8
    PADDING = 12

    def __init__(self, title: str, *, editable: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("panel")
        self._title = title
        self._editable = editable
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)
        self.setMinimumSize(320, 240)
        self._grid = monitor_grid_from_rows([[]], min_rows=1, min_cols=1)
        self._cell_items: dict[tuple[int, int], tuple[QGraphicsRectItem, QGraphicsSimpleTextItem]] = {}
        self._press_display_id: str | None = None
        self._press_pos: QPointF | None = None
        self._dragging = False
        self._hover_cell: tuple[int, int] | None = None
        self._last_shape: tuple[int, int] = (0, 0)

    @property
    def grid(self) -> MonitorGrid:
        return self._grid

    def set_grid(self, grid: MonitorGrid) -> None:
        shape_changed = (grid.rows, grid.cols) != self._last_shape
        self._grid = grid
        self._sync_scene(shape_changed=shape_changed)

    def _scene_pos_for_cell(self, row: int, col: int) -> tuple[float, float]:
        x = self.PADDING + col * (self.CELL_SIZE + self.GAP)
        y = self.PADDING + row * (self.CELL_SIZE + self.GAP)
        return x, y

    def _display_at(self, row: int, col: int) -> str | None:
        if row < 0 or row >= self._grid.rows or col < 0 or col >= self._grid.cols:
            return None
        return self._grid.cells[row][col]

    def _sync_scene(self, *, shape_changed: bool = False) -> None:
        if shape_changed:
            self._scene.clear()
            self._cell_items.clear()
        width = self.PADDING * 2 + self._grid.cols * self.CELL_SIZE + max(self._grid.cols - 1, 0) * self.GAP
        height = self.PADDING * 2 + self._grid.rows * self.CELL_SIZE + max(self._grid.rows - 1, 0) * self.GAP
        self._scene.setSceneRect(0, 0, width, height)
        self._last_shape = (self._grid.rows, self._grid.cols)
        for row_index, row in enumerate(self._grid.cells):
            for col_index, cell in enumerate(row):
                x, y = self._scene_pos_for_cell(row_index, col_index)
                rect = QRectF(x, y, self.CELL_SIZE, self.CELL_SIZE)
                is_hover = self._hover_cell == (row_index, col_index)
                border = QColor(PALETTE["accent"] if is_hover else PALETTE["border"])
                fill = QColor(PALETTE["surface"] if cell in EMPTY_TOKENS else PALETTE["accent_soft"])
                item, text = self._cell_items.get((row_index, col_index), (None, None))
                if item is None or text is None:
                    item = self._scene.addRect(rect, QPen(border, 2), fill)
                    text = self._scene.addSimpleText("")
                    text.setBrush(QColor(PALETTE["text"]))
                    self._cell_items[(row_index, col_index)] = (item, text)
                item.setRect(rect)
                item.setPen(QPen(border, 2))
                item.setBrush(fill)
                label = "" if cell in EMPTY_TOKENS else str(cell)
                text.setText(label)
                text_rect = text.boundingRect()
                text.setPos(
                    rect.center().x() - text_rect.width() / 2,
                    rect.center().y() - text_rect.height() / 2,
                )
        self._fit_scene()

    def _fit_scene(self) -> None:
        if not self._scene.sceneRect().isNull():
            self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._fit_scene()

    def _board_cell_from_point(self, pos: QPointF) -> tuple[int, int] | None:
        scene_pos = self.mapToScene(pos.toPoint())
        local_x = scene_pos.x() - self.PADDING
        local_y = scene_pos.y() - self.PADDING
        if local_x < 0 or local_y < 0:
            return None
        pitch = self.CELL_SIZE + self.GAP
        row = int(local_y // pitch)
        col = int(local_x // pitch)
        if row < 0 or col < 0:
            return None
        if row > self._grid.rows or col > self._grid.cols:
            return None
        max_x = self._grid.cols * pitch - self.GAP
        max_y = self._grid.rows * pitch - self.GAP
        if local_x > max_x + self.GAP or local_y > max_y + self.GAP:
            return None
        return min(row, self._grid.rows - 1), min(col, self._grid.cols - 1)

    def mousePressEvent(self, event):  # noqa: N802
        if not self._editable or event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        cell = self._board_cell_from_point(event.position())
        if cell is None:
            self._press_display_id = None
            self._press_pos = None
            return super().mousePressEvent(event)
        display_id = self._display_at(*cell)
        self._press_display_id = display_id
        self._press_pos = event.position()
        self._dragging = False
        self._hover_cell = None
        event.accept()

    def mouseMoveEvent(self, event):  # noqa: N802
        if not self._editable or self._press_display_id is None or self._press_pos is None:
            return super().mouseMoveEvent(event)
        delta = event.position() - self._press_pos
        if not self._dragging and math.hypot(delta.x(), delta.y()) < 8.0:
            return
        self._dragging = True
        cell = self._board_cell_from_point(event.position())
        if cell is not None and cell != self._hover_cell:
            self._hover_cell = cell
            self._sync_scene()
        event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if not self._editable or self._press_display_id is None:
            self._reset_drag()
            return super().mouseReleaseEvent(event)
        try:
            if self._dragging and self._hover_cell is not None:
                next_grid = place_display_on_grid(
                    self._grid,
                    self._press_display_id,
                    self._hover_cell[0],
                    self._hover_cell[1],
                )
                self.commitRequested.emit(next_grid)
        except Exception as exc:
            self.statusChanged.emit(str(exc))
        finally:
            self._reset_drag()
        event.accept()

    def _reset_drag(self) -> None:
        self._press_display_id = None
        self._press_pos = None
        self._dragging = False
        self._hover_cell = None
        self._sync_scene()


class MonitorMapDialog(QDialog):
    def __init__(
        self,
        parent,
        *,
        node_id: str,
        snapshot,
        topology,
        on_apply,
        on_refresh_detected=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"{node_id} 모니터 맵")
        self.resize(1080, 720)
        self._node_id = node_id
        self._snapshot = snapshot
        self._topology = topology
        self._on_apply = on_apply
        self._on_refresh_detected = on_refresh_detected
        self._undo_grid: MonitorGrid | None = None
        self._logical_grid = monitor_grid_from_rows(snapshot_to_logical_rows(snapshot), min_rows=1, min_cols=1)
        physical_rows = monitor_topology_to_rows(topology, logical=False)
        self._physical_grid = monitor_grid_from_rows(
            physical_rows or snapshot_to_logical_rows(snapshot),
            min_rows=self._logical_grid.rows,
            min_cols=self._logical_grid.cols,
        )

        root = QHBoxLayout(self)
        left = QVBoxLayout()
        right = QVBoxLayout()
        root.addLayout(left, 3)
        root.addLayout(right, 1)

        heading = QLabel("실제 감지된 논리 배치를 기준으로 물리 배치만 보정합니다.")
        heading.setObjectName("subtle")
        left.addWidget(heading)

        actions = QHBoxLayout()
        self._reload_button = QPushButton("감지 다시 불러오기")
        self._reload_button.clicked.connect(self._refresh_detected)
        self._reset_button = QPushButton("감지값으로 초기화")
        self._reset_button.clicked.connect(self._reset_to_detected)
        self._undo_button = QPushButton("직전 편집 되돌리기")
        self._undo_button.clicked.connect(self._undo_last_change)
        actions.addWidget(self._reload_button)
        actions.addWidget(self._reset_button)
        actions.addWidget(self._undo_button)
        actions.addStretch(1)
        left.addLayout(actions)

        boards = QHBoxLayout()
        left.addLayout(boards, 1)
        self._logical_board = MonitorBoardView("논리 배치", editable=False)
        self._logical_board.set_grid(self._logical_grid)
        boards.addWidget(self._wrap_board("논리 배치", self._logical_board, editable=False), 1)
        self._physical_board = MonitorBoardView("물리 배치", editable=True)
        self._physical_board.set_grid(self._physical_grid)
        self._physical_board.commitRequested.connect(self._commit_grid_change)
        self._physical_board.statusChanged.connect(self._set_status)
        boards.addWidget(self._wrap_board("물리 배치", self._physical_board, editable=True), 1)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setObjectName("subtle")
        left.addWidget(self._status)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        left.addWidget(buttons)

        right_panel = QFrame()
        right_panel.setObjectName("panel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.addWidget(QLabel("상태"))
        self._freshness = QLabel()
        self._diff = QLabel()
        self._preview = QLabel()
        for label in (self._freshness, self._diff, self._preview):
            label.setWordWrap(True)
            right_layout.addWidget(label)
        right_layout.addStretch(1)
        root.addWidget(right_panel, 1)

        self._refresh_status_text()

    def _wrap_board(self, title: str, board: MonitorBoardView, *, editable: bool):
        container = QVBoxLayout()
        label = QLabel(title)
        label.setObjectName("heading")
        label.setStyleSheet("font-size: 16px;")
        container.addWidget(label)
        if editable:
            controls = QGridLayout()
            add_col = QPushButton("오른쪽 열 추가")
            add_col.clicked.connect(lambda: self._commit_grid_change(append_monitor_grid_col(self._physical_grid)))
            remove_col = QPushButton("마지막 열 삭제")
            remove_col.clicked.connect(lambda: self._remove_edge("col"))
            add_row = QPushButton("아래 행 추가")
            add_row.clicked.connect(lambda: self._commit_grid_change(append_monitor_grid_row(self._physical_grid)))
            remove_row = QPushButton("마지막 행 삭제")
            remove_row.clicked.connect(lambda: self._remove_edge("row"))
            controls.addWidget(add_col, 0, 0)
            controls.addWidget(remove_col, 0, 1)
            controls.addWidget(add_row, 1, 0)
            controls.addWidget(remove_row, 1, 1)
            container.addLayout(controls)
        container.addWidget(board, 1)
        wrapper = QWidget()
        wrapper.setLayout(container)
        return wrapper

    def _remove_edge(self, axis: str) -> None:
        try:
            if axis == "row":
                self._commit_grid_change(remove_last_monitor_grid_row(self._physical_grid))
            else:
                self._commit_grid_change(remove_last_monitor_grid_col(self._physical_grid))
        except Exception as exc:
            self._set_status(str(exc))

    def _commit_grid_change(self, grid: MonitorGrid) -> None:
        self._undo_grid = self._physical_grid
        self._physical_grid = grid
        self._physical_board.set_grid(grid)
        self._refresh_status_text()

    def _undo_last_change(self) -> None:
        if self._undo_grid is None:
            self._set_status("되돌릴 직전 편집이 없습니다.")
            return
        self._physical_grid, self._undo_grid = self._undo_grid, self._physical_grid
        self._physical_board.set_grid(self._physical_grid)
        self._refresh_status_text()

    def _reset_to_detected(self) -> None:
        self._commit_grid_change(
            monitor_grid_from_rows(
                [list(row) for row in self._logical_grid.cells],
                min_rows=self._logical_grid.rows,
                min_cols=self._logical_grid.cols,
            )
        )
        self._set_status("감지된 논리 배치와 같은 형태로 초기화했습니다.")

    def _refresh_detected(self) -> None:
        if not callable(self._on_refresh_detected):
            self._set_status("재감지 기능을 사용할 수 없습니다.")
            return
        refreshed = self._on_refresh_detected()
        if refreshed is None:
            self._set_status("재감지 요청을 보냈습니다. 새 감지 결과가 오면 자동으로 반영됩니다.")
            return
        self._snapshot = refreshed
        self._logical_grid = monitor_grid_from_rows(snapshot_to_logical_rows(refreshed), min_rows=1, min_cols=1)
        self._logical_board.set_grid(self._logical_grid)
        logical_ids = set(self._logical_grid_validation().display_ids)
        physical_ids = set(cell for row in self._physical_grid.cells for cell in row if cell not in EMPTY_TOKENS)
        if logical_ids != physical_ids:
            self._physical_grid = monitor_grid_from_rows(
                [list(row) for row in self._logical_grid.cells],
                min_rows=self._logical_grid.rows,
                min_cols=self._logical_grid.cols,
            )
            self._physical_board.set_grid(self._physical_grid)
        self._refresh_status_text()
        self._set_status("감지된 논리 배치를 다시 불러왔습니다.")

    def update_detected_snapshot(self, snapshot, topology) -> None:
        if snapshot is None or snapshot.captured_at == getattr(self._snapshot, "captured_at", None):
            return
        self._snapshot = snapshot
        self._topology = topology
        self._logical_grid = monitor_grid_from_rows(snapshot_to_logical_rows(snapshot), min_rows=1, min_cols=1)
        self._logical_board.set_grid(self._logical_grid)
        logical_ids = {
            cell
            for row in self._logical_grid.cells
            for cell in row
            if cell not in EMPTY_TOKENS
        }
        physical_ids = {
            cell
            for row in self._physical_grid.cells
            for cell in row
            if cell not in EMPTY_TOKENS
        }
        if logical_ids != physical_ids:
            self._physical_grid = monitor_grid_from_rows(
                [list(row) for row in self._logical_grid.cells],
                min_rows=self._logical_grid.rows,
                min_cols=self._logical_grid.cols,
            )
            self._physical_board.set_grid(self._physical_grid)
        self._refresh_status_text()

    def _logical_grid_validation(self) -> MonitorGridValidation:
        return validate_monitor_grids(self._logical_grid, self._physical_grid)

    def _refresh_status_text(self) -> None:
        validation = self._logical_grid_validation()
        freshness = describe_monitor_freshness(self._snapshot, online=True)
        diff = compare_detected_and_physical_rows(
            [list(row) for row in self._logical_grid.cells],
            [list(row) for row in self._physical_grid.cells],
        )
        self._freshness.setText(f"감지 상태: {freshness.label}\n{freshness.detail}")
        self._diff.setText(f"배치 차이: {summarize_monitor_diff(diff)}")
        self._preview.setText(
            f"현재 물리 배치: {self._physical_grid.cols}열 x {self._physical_grid.rows}행"
        )
        if validation.errors:
            self._status.setText("\n".join(validation.errors))
        else:
            self._status.setText("")

    def _set_status(self, text: str) -> None:
        self._status.setText(text)

    def _apply(self) -> None:
        validation = validate_monitor_grids(self._logical_grid, self._physical_grid)
        if not validation.is_valid:
            self._set_status("\n".join(validation.errors))
            return
        self._on_apply(
            logical_rows=[list(row) for row in self._logical_grid.cells],
            physical_rows=[list(row) for row in self._physical_grid.cells],
        )
        self.accept()
