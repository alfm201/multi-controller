"""Qt layout editor widget for the shared PC canvas."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from runtime.gui_style import PALETTE
from runtime.layout_dialogs import MonitorMapDialog
from runtime.layout_geometry import LayoutGeometrySpec, layout_world_bounds, node_world_bounds
from runtime.layouts import find_overlapping_nodes, replace_layout_monitors, replace_layout_node
from runtime.status_view import (
    build_layout_editor_hint,
    build_layout_lock_text,
    build_layout_node_colors,
    build_layout_node_label,
    build_selected_node_text,
)


@dataclass
class DragState:
    node_id: str | None = None
    origin_scene: QPointF | None = None
    origin_grid: tuple[int, int] | None = None
    start_layout: object | None = None
    last_grid: tuple[int, int] | None = None


class LayoutScene(QGraphicsScene):
    def __init__(self, spec: LayoutGeometrySpec, parent=None):
        super().__init__(parent)
        self._spec = spec

    def drawBackground(self, painter: QPainter, rect):  # noqa: N802
        super().drawBackground(painter, rect)
        painter.fillRect(rect, QColor(PALETTE["surface_alt"]))
        painter.setPen(QPen(QColor("#dbe3ef"), 1))
        start_x = int(rect.left() // self._spec.grid_pitch_x) * int(self._spec.grid_pitch_x)
        start_y = int(rect.top() // self._spec.grid_pitch_y) * int(self._spec.grid_pitch_y)
        x = start_x
        while x <= rect.right():
            painter.drawLine(int(x), int(rect.top()), int(x), int(rect.bottom()))
            x += self._spec.grid_pitch_x
        y = start_y
        while y <= rect.bottom():
            painter.drawLine(int(rect.left()), int(y), int(rect.right()), int(y))
            y += self._spec.grid_pitch_y


class LayoutNodeItem(QGraphicsRectItem):
    def __init__(self, editor, node_id: str):
        super().__init__()
        self.editor = editor
        self.node_id = node_id
        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self._label = QGraphicsSimpleTextItem(self)
        self._label.setAcceptedMouseButtons(Qt.NoButton)
        self._tag_bg = QGraphicsRectItem(self)
        self._tag_bg.setPen(QPen(Qt.NoPen))
        self._tag_text = QGraphicsSimpleTextItem(self)
        self._tag_text.setAcceptedMouseButtons(Qt.NoButton)

    def apply_state(
        self,
        rect: QRectF,
        label: str,
        fill: str,
        border: str,
        *,
        highlight: bool,
    ) -> None:
        self.setRect(rect)
        self.setPen(QPen(QColor(border), 4 if highlight else 2))
        self.setBrush(QColor(fill))
        self.setZValue(3 if highlight else 1)
        self._label.setText(label)
        label_rect = self._label.boundingRect()
        self._label.setBrush(QColor(PALETTE["text"]))
        self._label.setPos(
            rect.center().x() - label_rect.width() / 2,
            rect.center().y() - label_rect.height() / 2,
        )
        if highlight:
            self._tag_text.setText("선택")
            self._tag_text.setBrush(QColor("#f8fafc"))
            tag_rect = self._tag_text.boundingRect()
            tag_x = rect.left() + 8
            tag_y = rect.top() + 8
            self._tag_bg.setRect(tag_x - 6, tag_y - 3, tag_rect.width() + 12, tag_rect.height() + 6)
            self._tag_bg.setBrush(QColor(border))
            self._tag_bg.show()
            self._tag_text.setPos(tag_x, tag_y)
            self._tag_text.show()
        else:
            self._tag_bg.hide()
            self._tag_text.hide()

    def mousePressEvent(self, event):  # noqa: N802
        self.editor.on_node_pressed(self.node_id, event)
        event.accept()

    def mouseMoveEvent(self, event):  # noqa: N802
        self.editor.on_node_moved(self.node_id, event)
        event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802
        self.editor.on_node_released(self.node_id, event)
        event.accept()


class LayoutCanvas(QGraphicsView):
    def __init__(self, editor, scene, parent=None):
        super().__init__(scene, parent)
        self.editor = editor
        self.setFrameShape(QFrame.NoFrame)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.NoAnchor)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._panning = False
        self._pan_origin = None

    def wheelEvent(self, event):  # noqa: N802
        factor = 1.12 if event.angleDelta().y() > 0 else 1 / 1.12
        self.editor.zoom_at(factor, event.position())
        event.accept()

    def mousePressEvent(self, event):  # noqa: N802
        item = self.itemAt(event.position().toPoint())
        if item is None and event.button() == Qt.LeftButton:
            self._panning = True
            self._pan_origin = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._panning and self._pan_origin is not None:
            delta = event.position() - self._pan_origin
            self._pan_origin = event.position()
            self.editor.pan_by(delta.x(), delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self._panning:
            self._panning = False
            self._pan_origin = None
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class LayoutEditor(QWidget):
    nodeSelected = Signal(str)
    messageRequested = Signal(str, str)

    def __init__(
        self,
        ctx,
        registry,
        coordinator_resolver,
        *,
        router=None,
        sink=None,
        coord_client=None,
        config_reloader=None,
        monitor_inventory_manager=None,
        parent=None,
    ):
        super().__init__(parent)
        self.ctx = ctx
        self.registry = registry
        self.coordinator_resolver = coordinator_resolver
        self.router = router
        self.sink = sink
        self.coord_client = coord_client
        self.config_reloader = config_reloader
        self.monitor_inventory_manager = monitor_inventory_manager
        self._spec = LayoutGeometrySpec()
        self._current_view = None
        self._selected_node_id = ctx.self_node.node_id
        self._draft_layout = ctx.layout
        self._scene = LayoutScene(self._spec, self)
        self._canvas = LayoutCanvas(self, self._scene, self)
        self._items: dict[str, LayoutNodeItem] = {}
        self._drag = DragState()
        self._monitor_dialog = None
        self._did_initial_fit = False
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        toolbar = QHBoxLayout()
        self._layout_edit_toggle = QPushButton("편집")
        self._layout_edit_toggle.setCheckable(True)
        self._layout_edit_toggle.setFixedWidth(96)
        self._layout_edit_toggle.clicked.connect(self._toggle_edit_mode)
        self._monitor_button = QPushButton("모니터 맵")
        self._monitor_button.setFixedWidth(112)
        self._monitor_button.clicked.connect(self.open_monitor_editor)
        self._fit_button = QPushButton("맞춤")
        self._fit_button.setFixedWidth(84)
        self._fit_button.clicked.connect(lambda: self.fit_view())
        self._zoom_reset_button = QPushButton("100%")
        self._zoom_reset_button.setFixedWidth(84)
        self._zoom_reset_button.clicked.connect(self.reset_zoom)
        self._view_reset_button = QPushButton("초기화")
        self._view_reset_button.setFixedWidth(100)
        self._view_reset_button.clicked.connect(self.reset_view)
        for widget in (
            self._layout_edit_toggle,
            self._monitor_button,
            self._fit_button,
            self._zoom_reset_button,
            self._view_reset_button,
        ):
            toolbar.addWidget(widget)
        toolbar.addStretch(1)
        root.addLayout(toolbar)

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setObjectName("subtle")
        root.addWidget(self._hint)

        self._lock = QLabel()
        self._lock.setObjectName("subtle")
        root.addWidget(self._lock)

        self._selected = QLabel()
        self._selected.setObjectName("subtle")
        root.addWidget(self._selected)

        self._canvas.setObjectName("panel")
        root.addWidget(self._canvas, 1)

    def close(self) -> None:
        if self._monitor_dialog is not None:
            self._monitor_dialog.close()
            self._monitor_dialog = None

    def select_node(self, node_id: str, *, emit_signal: bool = False) -> None:
        if not node_id:
            return
        self._selected_node_id = node_id
        self._selected.setText(build_selected_node_text(self._current_layout_node()))
        self._update_action_buttons()
        self._render_scene()
        if emit_signal:
            self.nodeSelected.emit(node_id)

    def current_selected_node_id(self) -> str:
        return self._selected_node_id

    def refresh(self, view) -> None:
        self._current_view = view
        if self._drag.node_id is None:
            self._draft_layout = self.ctx.layout
        if self._selected_node_id not in {detail.node_id for detail in view.node_details}:
            self._selected_node_id = view.self_id
        self._update_controls()
        self._update_action_buttons()
        self._render_scene()
        self._selected.setText(build_selected_node_text(self._current_layout_node()))
        if self._monitor_dialog is not None and self._monitor_dialog.isVisible():
            self._refresh_monitor_dialog()

    def _update_controls(self) -> None:
        is_editor = False if self.coord_client is None else self.coord_client.is_layout_editor()
        editor_id = None if self.coord_client is None else self.coord_client.get_layout_editor()
        pending = False if self.coord_client is None else self.coord_client.is_layout_edit_pending()
        self._layout_edit_toggle.blockSignals(True)
        self._layout_edit_toggle.setChecked(is_editor)
        self._layout_edit_toggle.blockSignals(False)
        self._hint.setText(
            build_layout_editor_hint(
                is_editor,
                editor_id,
                self.ctx.self_node.node_id,
                pending=pending,
            )
        )
        self._lock.setText(build_layout_lock_text(editor_id, self.ctx.self_node.node_id, pending=pending))

    def _update_action_buttons(self) -> None:
        self._monitor_button.setEnabled(self.can_open_monitor_editor())

    def _current_layout_node(self):
        layout = self._draft_layout or self.ctx.layout
        if layout is None:
            return None
        return layout.get_node(self._selected_node_id)

    def _render_scene(self) -> None:
        layout = self._draft_layout or self.ctx.layout
        if layout is None:
            self._scene.clear()
            return
        self._scene.setSceneRect(self._unbounded_scene_rect())
        online = {peer.node_id: peer.online for peer in self._current_view.peers} if self._current_view else {}
        selected_target = None if self._current_view is None else self._current_view.selected_target
        router_state = None if self._current_view is None else self._current_view.router_state
        for node in layout.nodes:
            item = self._items.get(node.node_id)
            if item is None:
                item = LayoutNodeItem(self, node.node_id)
                self._items[node.node_id] = item
                self._scene.addItem(item)
            rect_bounds = node_world_bounds(node, self._spec)
            fill, border = build_layout_node_colors(
                is_self=node.node_id == self.ctx.self_node.node_id,
                is_online=online.get(node.node_id, node.node_id == self.ctx.self_node.node_id),
                is_selected=node.node_id == selected_target or node.node_id == self._selected_node_id,
                state=router_state if node.node_id == selected_target else None,
            )
            label = build_layout_node_label(
                node.node_id,
                is_self=node.node_id == self.ctx.self_node.node_id,
                is_online=online.get(node.node_id, node.node_id == self.ctx.self_node.node_id),
                is_selected=node.node_id == selected_target or node.node_id == self._selected_node_id,
                state=router_state if node.node_id == selected_target else None,
            )
            item.apply_state(
                QRectF(rect_bounds.left, rect_bounds.top, rect_bounds.width, rect_bounds.height),
                label,
                fill,
                border,
                highlight=node.node_id == self._selected_node_id,
            )
        visible_ids = {node.node_id for node in layout.nodes}
        for node_id in list(self._items):
            if node_id not in visible_ids:
                item = self._items.pop(node_id)
                self._scene.removeItem(item)
        if not self._did_initial_fit and self._canvas.transform().isIdentity():
            self.fit_view(min_zoom=0.9)
            self._did_initial_fit = True

    def fit_view(self, *, min_zoom: float | None = None) -> None:
        if not self._scene.items():
            return
        rect = self._content_rect_for_fit()
        self._canvas.fitInView(rect, Qt.KeepAspectRatio)
        if min_zoom is not None and self._canvas.transform().m11() < min_zoom:
            self._canvas.resetTransform()
            self._canvas.scale(min_zoom, min_zoom)
            self._canvas.centerOn(rect.center())

    def reset_zoom(self) -> None:
        center = self._canvas.mapToScene(self._canvas.viewport().rect().center())
        self._canvas.resetTransform()
        self._canvas.centerOn(center)

    def reset_view(self) -> None:
        self._canvas.resetTransform()
        self.fit_view(min_zoom=0.9)

    def zoom_at(self, factor: float, pos) -> None:
        old_scene = self._canvas.mapToScene(pos.toPoint())
        self._canvas.scale(factor, factor)
        new_scene = self._canvas.mapToScene(pos.toPoint())
        delta = new_scene - old_scene
        self._canvas.translate(delta.x(), delta.y())

    def pan_by(self, dx: float, dy: float) -> None:
        scale = max(self._canvas.transform().m11(), 0.0001)
        self._canvas.translate(dx / scale, dy / scale)

    def on_node_pressed(self, node_id: str, event) -> None:
        self.select_node(node_id, emit_signal=True)
        if not self._can_drag_nodes():
            return
        current_layout = self._draft_layout or self.ctx.layout
        node = current_layout.get_node(node_id)
        if node is None:
            return
        self._drag = DragState(
            node_id=node_id,
            origin_scene=event.scenePos(),
            origin_grid=(node.x, node.y),
            start_layout=current_layout,
            last_grid=(node.x, node.y),
        )

    def on_node_moved(self, node_id: str, event) -> None:
        if self._drag.node_id != node_id or self._drag.origin_scene is None or self._drag.origin_grid is None:
            return
        delta = event.scenePos() - self._drag.origin_scene
        next_x = self._drag.origin_grid[0] + round(delta.x() / self._spec.grid_pitch_x)
        next_y = self._drag.origin_grid[1] + round(delta.y() / self._spec.grid_pitch_y)
        next_grid = (next_x, next_y)
        if next_grid == self._drag.last_grid:
            return
        candidate = replace_layout_node(self._drag.start_layout, node_id, x=next_x, y=next_y)
        if find_overlapping_nodes(candidate):
            return
        self._drag.last_grid = next_grid
        self._draft_layout = candidate
        self._publish_layout(candidate, persist=False)
        self._render_scene()

    def on_node_released(self, node_id: str, _event) -> None:
        if self._drag.node_id != node_id:
            return
        changed = self._drag.last_grid != self._drag.origin_grid
        if changed:
            self._publish_layout(self._draft_layout, persist=True)
            self.messageRequested.emit("레이아웃 변경을 저장했습니다.", "success")
        self._drag = DragState()
        self._draft_layout = self.ctx.layout
        self._render_scene()

    def _publish_layout(self, layout, *, persist: bool) -> None:
        if layout is None:
            return
        if self.coord_client is not None and self.coord_client.is_layout_editor():
            self.coord_client.publish_layout(layout, persist=persist)
            if persist:
                self.ctx.replace_layout(layout)
            return
        if self.config_reloader is not None:
            self.config_reloader.apply_layout(layout, persist=persist, debounce_persist=not persist)

    def _can_drag_nodes(self) -> bool:
        return self.coord_client is not None and self.coord_client.is_layout_editor()

    def deactivate_edit_mode(self, *, notify: bool = False) -> None:
        if self.coord_client is None:
            return
        if not self.coord_client.is_layout_editor() and not self.coord_client.is_layout_edit_pending():
            return
        self.coord_client.end_layout_edit()
        if notify:
            self.messageRequested.emit("레이아웃 탭을 벗어나 편집 모드를 종료했습니다.", "neutral")
        self._update_controls()
        self._update_action_buttons()

    def _toggle_edit_mode(self, checked: bool) -> None:
        if self.coord_client is None:
            return
        if checked:
            self.coord_client.request_layout_edit()
            self.messageRequested.emit("편집 권한을 요청했습니다.", "warning")
            self._update_controls()
            self._update_action_buttons()
            return
        self.coord_client.end_layout_edit()
        self.messageRequested.emit("편집 권한을 반납했습니다.", "neutral")
        self._update_controls()
        self._update_action_buttons()

    def can_open_monitor_editor(self) -> bool:
        if not self._can_drag_nodes():
            return False
        node = self._current_layout_node()
        snapshot = None if node is None else self.ctx.get_monitor_inventory(node.node_id)
        return node is not None and snapshot is not None and bool(snapshot.monitors)

    def request_selected_target(self) -> None:
        node_id = self._selected_node_id
        if not node_id or self.coord_client is None:
            return
        if node_id == self.ctx.self_node.node_id:
            self.coord_client.clear_target()
            self.messageRequested.emit("내 PC를 선택했습니다.", "neutral")
            return
        if not self._is_node_online(node_id):
            self.messageRequested.emit("오프라인 PC는 제어 대상으로 선택할 수 없습니다.", "warning")
            return
        self.messageRequested.emit(f"{node_id} PC로 전환을 요청했습니다.", "accent")
        self.coord_client.request_target(node_id)

    def open_monitor_editor(self) -> None:
        node = self._current_layout_node()
        if node is None:
            self.messageRequested.emit("먼저 PC를 선택해 주세요.", "warning")
            return
        if not self._can_drag_nodes():
            self.messageRequested.emit("편집 권한을 얻은 뒤에 모니터 맵을 수정할 수 있습니다.", "warning")
            return
        snapshot = self.ctx.get_monitor_inventory(node.node_id)
        if snapshot is None or not snapshot.monitors:
            self.messageRequested.emit("실제 감지된 모니터 정보가 아직 없습니다.", "warning")
            return

        def _apply(*, logical_rows, physical_rows):
            layout = self._draft_layout or self.ctx.layout
            next_layout = replace_layout_monitors(
                layout,
                node.node_id,
                logical_rows=logical_rows,
                physical_rows=physical_rows,
            )
            self._publish_layout(next_layout, persist=True)
            self.ctx.replace_layout(next_layout)
            self._draft_layout = next_layout
            self._render_scene()
            self.messageRequested.emit(f"{node.node_id} 모니터 맵을 저장했습니다.", "success")

        self._monitor_dialog = MonitorMapDialog(
            self,
            node_id=node.node_id,
            snapshot=snapshot,
            topology=node.monitors(),
            on_apply=_apply,
            on_refresh_detected=lambda: self._refresh_detected_snapshot(node.node_id),
        )
        self._monitor_dialog.exec()
        self._monitor_dialog = None

    def _refresh_detected_snapshot(self, node_id: str):
        if node_id == self.ctx.self_node.node_id and self.monitor_inventory_manager is not None:
            snapshot = self.monitor_inventory_manager.refresh()
            self.ctx.replace_monitor_inventory(snapshot)
            return snapshot
        if self.coord_client is not None:
            self.coord_client.request_monitor_inventory_refresh(node_id)
        return None

    def _refresh_monitor_dialog(self) -> None:
        if self._monitor_dialog is None:
            return
        node = self._current_layout_node()
        if node is None:
            return
        snapshot = self.ctx.get_monitor_inventory(node.node_id)
        if snapshot is None or not snapshot.monitors:
            return
        if hasattr(self._monitor_dialog, "update_detected_snapshot"):
            self._monitor_dialog.update_detected_snapshot(snapshot, node.monitors())

    def _is_node_online(self, node_id: str) -> bool:
        if node_id == self.ctx.self_node.node_id:
            return True
        if self._current_view is None:
            return False
        peer = next((item for item in self._current_view.peers if item.node_id == node_id), None)
        return False if peer is None else peer.online

    def _content_rect_for_fit(self) -> QRectF:
        layout = self._draft_layout or self.ctx.layout
        if layout is None:
            return self._scene.sceneRect()
        bounds = layout_world_bounds(layout, self._spec)
        return QRectF(bounds.left, bounds.top, bounds.width, bounds.height)

    def _unbounded_scene_rect(self) -> QRectF:
        return QRectF(-10_000_000.0, -10_000_000.0, 20_000_000.0, 20_000_000.0)
