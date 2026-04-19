"""Qt widgets for GUI-driven node management."""

from __future__ import annotations

import threading

from PySide6.QtCore import QEvent, QRect, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QHeaderView,
    QSizePolicy,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
)
from shiboken6 import isValid

from control.coordination.election import DEFAULT_COORDINATOR_PRIORITY
from app.config.config_loader import (
    DEFAULT_LISTEN_PORT,
    format_config_persist_error,
    generate_unique_node_id,
    is_valid_ipv4_address,
)
from app.config.group_join import request_group_join_state
from model.display.layouts import build_layout_config
from app.ui.scroll_utils import attach_horizontal_scroll_interaction
from app.ui.hover_tooltip import HoverTooltip

CHECK_STATE_ROLE = Qt.UserRole + 1
NODE_ID_ROLE = Qt.UserRole + 2
NODE_TABLE_COLUMN_TOOLTIPS = (
    "체크박스로 수정하거나 삭제할 노드를 선택합니다.",
    "다른 PC와 상태 화면에 표시되는 노드 이름입니다.",
    "각 노드에 연결할 IP 주소입니다.",
    "숫자가 낮을수록 코디네이터로 먼저 선발됩니다. 비우거나 0이면 가장 후순위입니다.",
    "노드 관리에서만 쓰는 메모입니다.",
)
PRIORITY_HELP_TEXT = "숫자가 낮을수록 코디네이터로 먼저 선발됩니다. 비우거나 0이면 가장 후순위입니다."
IP_INPUT_STYLE = """
QLineEdit {
    padding: 4px 6px;
    border: 1px solid palette(mid);
    border-radius: 4px;
    background: palette(base);
}
QLineEdit[invalid="true"] {
    border: 1px solid #c84b4b;
    background: rgba(200, 75, 75, 0.08);
}
"""


def _priority_input_text(value) -> str:
    try:
        priority = DEFAULT_COORDINATOR_PRIORITY if value in (None, "") else int(value)
    except (TypeError, ValueError):
        return str(value)
    return "" if priority <= 0 else str(priority)


def _priority_display_text(value) -> str:
    try:
        priority = DEFAULT_COORDINATOR_PRIORITY if value in (None, "") else int(value)
    except (TypeError, ValueError):
        return "후순위"
    return "후순위" if priority <= 0 else str(priority)


class _IPv4SegmentEdit(QLineEdit):
    advanceRequested = Signal()
    retreatRequested = Signal()
    pasteRequested = Signal(str)
    selectAllRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaxLength(3)
        self.setAlignment(Qt.AlignCenter)
        self.setInputMethodHints(Qt.ImhDigitsOnly)
        self.setFixedWidth(52)
        self.setStyleSheet(IP_INPUT_STYLE)
        self.textEdited.connect(self._sanitize_live_text)

    def _sanitize_live_text(self, text: str) -> None:
        digits = "".join(char for char in text if char.isdigit())[:3]
        if digits != text:
            cursor = min(len(digits), self.cursorPosition())
            self.blockSignals(True)
            self.setText(digits)
            self.blockSignals(False)
            self.setCursorPosition(cursor)
        if len(digits) == 3:
            QTimer.singleShot(0, self.advanceRequested.emit)

    def keyPressEvent(self, event) -> None:  # noqa: D401
        if event.matches(QKeySequence.SelectAll):
            self.selectAllRequested.emit()
            event.accept()
            return
        if event.matches(QKeySequence.Paste):
            self.pasteRequested.emit(QApplication.clipboard().text())
            event.accept()
            return
        if event.text() == "." or event.key() in {Qt.Key_Period, Qt.Key_Comma}:
            QTimer.singleShot(0, self.advanceRequested.emit)
            event.accept()
            return
        if bool(event.modifiers() & Qt.KeypadModifier) and event.text() in {".", ","}:
            QTimer.singleShot(0, self.advanceRequested.emit)
            event.accept()
            return
        if event.key() == Qt.Key_Left and not self.selectedText() and self.cursorPosition() == 0:
            QTimer.singleShot(0, self.retreatRequested.emit)
            event.accept()
            return
        if event.key() == Qt.Key_Right and not self.selectedText() and self.cursorPosition() == len(self.text()):
            QTimer.singleShot(0, self.advanceRequested.emit)
            event.accept()
            return
        if (
            event.key() == Qt.Key_Backspace
            and not self.selectedText()
            and self.cursorPosition() == 0
            and not self.text()
        ):
            QTimer.singleShot(0, self.retreatRequested.emit)
            event.accept()
            return
        if event.text() and not event.text().isdigit():
            event.accept()
            return
        super().keyPressEvent(event)

    def insertFromMimeData(self, source) -> None:  # noqa: D401
        self.pasteRequested.emit(source.text())

    def focusOutEvent(self, event) -> None:  # noqa: N802
        parent = self.parent()
        super().focusOutEvent(event)
        if parent is not None and hasattr(parent, "_schedule_clear_if_focus_left"):
            parent._schedule_clear_if_focus_left()  # type: ignore[attr-defined]


class IPv4AddressInput(QWidget):
    textChanged = Signal(str)

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._force_required_invalid = False
        self._segments: list[_IPv4SegmentEdit] = []
        self._suppress_focus_clear = False
        self._build()
        self.setText(text)

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for index in range(4):
            edit = _IPv4SegmentEdit(self)
            edit.setPlaceholderText("0")
            edit.textChanged.connect(self._on_segment_changed)
            edit.advanceRequested.connect(lambda idx=index: self._focus_segment(idx + 1, select_all=True))
            edit.retreatRequested.connect(lambda idx=index: self._focus_segment(idx - 1, select_all=False))
            edit.pasteRequested.connect(self._handle_paste)
            edit.selectAllRequested.connect(self.selectAll)
            edit.installEventFilter(self)
            self._segments.append(edit)
            layout.addWidget(edit)
            if index < 3:
                dot = QLabel(".")
                dot.setAlignment(Qt.AlignCenter)
                dot.setObjectName("subtle")
                layout.addWidget(dot)
        self.setFocusProxy(self._segments[0])

    def text(self) -> str:  # noqa: D401
        values = [segment.text() for segment in self._segments]
        if not any(values):
            return ""
        return ".".join(values)

    def normalized_text(self) -> str:
        if not self.is_complete():
            return self.text()
        return ".".join(str(int(segment.text())) for segment in self._segments)

    def setText(self, value: str) -> None:
        parts = str(value or "").strip().split(".")
        normalized_parts = parts if len(parts) == 4 else ["", "", "", ""]
        for segment, part in zip(self._segments, normalized_parts):
            digits = "".join(char for char in str(part) if char.isdigit())[:3]
            segment.blockSignals(True)
            segment.setText(digits)
            segment.blockSignals(False)
        self._force_required_invalid = False
        self._refresh_segment_states()
        self.textChanged.emit(self.text())

    def clear(self) -> None:
        self.setText("")

    def selectAll(self) -> None:  # noqa: D401
        self._suppress_focus_clear = True
        for segment in self._segments:
            segment.selectAll()
        self._segments[0].setFocus()

    def is_complete(self) -> bool:
        return all(segment.text() for segment in self._segments)

    def is_valid(self) -> bool:
        return is_valid_ipv4_address(self.text())

    def mark_required_invalid(self) -> None:
        self._force_required_invalid = True
        self._refresh_segment_states()

    def _focus_segment(self, index: int, *, select_all: bool) -> None:
        if 0 <= index < len(self._segments):
            target = self._segments[index]
            self._clear_selection()
            if select_all:
                self._suppress_focus_clear = True
            target.setFocus()
            if select_all:
                target.selectAll()
            else:
                target.setCursorPosition(len(target.text()))

    def _handle_paste(self, text: str) -> None:
        parts = str(text or "").strip().split(".")
        if len(parts) != 4:
            return
        if not all(part.isdigit() and len(part) <= 3 and int(part) <= 255 for part in parts):
            return
        self.setText(".".join(parts))
        self._segments[-1].setFocus()
        self._segments[-1].setCursorPosition(len(self._segments[-1].text()))

    def _on_segment_changed(self) -> None:
        self._force_required_invalid = False
        self._refresh_segment_states()
        self.textChanged.emit(self.text())

    def _refresh_segment_states(self) -> None:
        for segment in self._segments:
            text = segment.text()
            invalid = False
            if text:
                invalid = not text.isdigit() or int(text) > 255
            elif self._force_required_invalid:
                invalid = True
            segment.setProperty("invalid", invalid)
            segment.style().unpolish(segment)
            segment.style().polish(segment)

    def eventFilter(self, watched, event):  # noqa: N802
        if watched in self._segments and event.type() == QEvent.Type.FocusIn:
            if self._suppress_focus_clear:
                self._suppress_focus_clear = False
            else:
                self._clear_selection()
        return super().eventFilter(watched, event)

    def _clear_selection(self, *, except_segment: _IPv4SegmentEdit | None = None) -> None:
        if not isValid(self):
            return
        for segment in self._segments:
            if not isValid(segment):
                continue
            if segment is except_segment:
                continue
            cursor = segment.cursorPosition()
            segment.deselect()
            segment.setCursorPosition(min(cursor, len(segment.text())))

    def _schedule_clear_if_focus_left(self) -> None:
        QTimer.singleShot(0, self._clear_if_focus_left)

    def _clear_if_focus_left(self) -> None:
        if not isValid(self):
            return
        focus_widget = QApplication.focusWidget()
        if focus_widget in self._segments:
            return
        self._clear_selection()


def _node_to_payload(node) -> dict:
    return {
        "node_id": node.node_id,
        "name": node.name,
        "ip": node.ip,
        "port": DEFAULT_LISTEN_PORT,
        "note": getattr(node, "note", "") or "",
        "priority": getattr(node, "priority", DEFAULT_COORDINATOR_PRIORITY),
    }


class NodeEditorDialog(QDialog):
    def __init__(self, *, title: str, payload: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(360, 0)
        self._payload = None
        self._build(payload or {})

    def payload(self) -> dict:
        if self._payload is None:
            raise RuntimeError("payload is unavailable before the dialog is accepted")
        return dict(self._payload)

    def accept(self) -> None:  # noqa: D401
        try:
            self._payload = self._collect_payload()
        except ValueError as exc:
            QMessageBox.warning(self, "입력 확인", str(exc))
            return
        super().accept()

    def _build(self, payload: dict) -> None:
        self._node_id = str(payload.get("node_id") or "").strip()
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        intro = QLabel("노드 이름과 IP를 입력해 주세요.")
        intro.setObjectName("subtle")
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)

        form.addWidget(QLabel("이름"), 0, 0)
        self._name = QLineEdit(payload.get("name", ""))
        form.addWidget(self._name, 0, 1)

        form.addWidget(QLabel("IP"), 1, 0)
        self._ip = IPv4AddressInput(payload.get("ip", ""))
        form.addWidget(self._ip, 1, 1)

        form.addWidget(QLabel("비고"), 2, 0)
        self._note = QLineEdit(payload.get("note", ""))
        self._note.setPlaceholderText("선택 사항")
        form.addWidget(self._note, 2, 1)

        port_hint = QLabel(f"포트는 항상 {DEFAULT_LISTEN_PORT}을 사용합니다.")
        port_hint.setObjectName("subtle")
        port_hint.setWordWrap(True)
        form.addWidget(port_hint, 5, 0, 1, 2)
        port_detail = QLabel("포트는 앱이 자동으로 관리하므로 이 창에서는 따로 수정하지 않습니다.")
        port_detail.setObjectName("subtle")
        port_detail.setWordWrap(True)
        form.addWidget(port_detail, 6, 0, 1, 2)
        priority_label = QLabel("우선순위")
        priority_label.setToolTip(PRIORITY_HELP_TEXT)
        form.addWidget(priority_label, 3, 0)
        self._priority = QLineEdit(_priority_input_text(payload.get("priority", DEFAULT_COORDINATOR_PRIORITY)))
        self._priority.setPlaceholderText("비우거나 0이면 가장 후순위")
        self._priority.setToolTip(PRIORITY_HELP_TEXT)
        form.addWidget(self._priority, 3, 1)

        priority_hint = QLabel(PRIORITY_HELP_TEXT)
        priority_hint.setObjectName("subtle")
        priority_hint.setWordWrap(True)
        form.addWidget(priority_hint, 4, 0, 1, 2)

        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("저장")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _collect_payload(self) -> dict:
        name = self._name.text().strip()
        ip = self._ip.normalized_text().strip()
        if not name:
            raise ValueError("이름을 입력해 주세요.")
        if not ip:
            self._ip.mark_required_invalid()
            raise ValueError("IP를 입력해 주세요.")
        if not self._ip.is_complete() or not self._ip.is_valid():
            self._ip.mark_required_invalid()
            raise ValueError("IP는 x.x.x.x 형식의 IPv4 주소로 입력해 주세요.")
        try:
            priority = int(self._priority.text().strip() or DEFAULT_COORDINATOR_PRIORITY)
        except ValueError as exc:
            raise ValueError("우선순위는 비우거나 0 이상의 정수로 입력해 주세요.") from exc
        if priority < 0:
            raise ValueError("우선순위는 비우거나 0 이상의 정수로 입력해 주세요.")
        payload = {
            "name": name,
            "ip": ip,
            "port": DEFAULT_LISTEN_PORT,
            "note": self._note.text().strip(),
            "priority": priority,
        }
        if self._node_id:
            payload["node_id"] = self._node_id
        return payload


class GroupJoinDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("그룹 참여")
        self.setModal(True)
        self.resize(340, 0)
        self._build()

    def target_ip(self) -> str:
        return self._ip.normalized_text().strip()

    def accept(self) -> None:  # noqa: D401
        if not self.target_ip():
            self._ip.mark_required_invalid()
            QMessageBox.warning(self, "입력 확인", "대상 IP를 입력해 주세요.")
            return
        if not self._ip.is_complete() or not self._ip.is_valid():
            self._ip.mark_required_invalid()
            QMessageBox.warning(self, "입력 확인", "IP는 x.x.x.x 형식의 IPv4 주소로 입력해 주세요.")
            return
        super().accept()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        intro = QLabel("참여할 노드 그룹에 속한 PC의 IP를 입력해 주세요.")
        intro.setWordWrap(True)
        intro.setObjectName("subtle")
        root.addWidget(intro)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        form.addWidget(QLabel("대상 IP"), 0, 0)
        self._ip = IPv4AddressInput("")
        form.addWidget(self._ip, 0, 1)
        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_button = buttons.button(QDialogButtonBox.Ok)
        cancel_button = buttons.button(QDialogButtonBox.Cancel)
        if ok_button is not None:
            ok_button.setText("참여")
        if cancel_button is not None:
            cancel_button.setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)


class NodeTableHeaderView(QHeaderView):
    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self.setMouseTracking(True)
        self._hover_tooltip = HoverTooltip(self)
        self.viewport().installEventFilter(self)
        self._tooltips: dict[int, str] = {}

    def set_section_tooltip(self, section: int, text: str) -> None:
        self._tooltips[int(section)] = text or ""

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self.viewport():
            if event.type() == QEvent.Type.MouseMove:
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                section = self.logicalIndexAt(pos)
                tooltip_text = self._tooltips.get(section, "")
                if tooltip_text:
                    self._hover_tooltip.show_text(tooltip_text, self.viewport().mapToGlobal(pos))
                else:
                    self._hover_tooltip.hide()
            elif event.type() in {QEvent.Type.Leave, QEvent.Type.HoverLeave}:
                self._hover_tooltip.hide()
        return super().eventFilter(watched, event)


class NodeTableWidget(QTableWidget):
    TOOLTIP_ROLE = Qt.UserRole + 10

    def __init__(self, rows: int, columns: int, parent=None):
        super().__init__(rows, columns, parent)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.ElideRight)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        attach_horizontal_scroll_interaction(self)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self._hover_tooltip = HoverTooltip(self)
        self.viewport().installEventFilter(self)
        self.setHorizontalHeader(NodeTableHeaderView(Qt.Horizontal, self))

    def set_hover_tooltip(self, item: QTableWidgetItem, text: str) -> None:
        item.setData(self.TOOLTIP_ROLE, text or "")

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self.viewport():
            if event.type() == QEvent.Type.MouseMove:
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                item = self.itemAt(pos)
                tooltip_text = "" if item is None else (item.data(self.TOOLTIP_ROLE) or "")
                if tooltip_text:
                    self._hover_tooltip.show_text(tooltip_text, self.viewport().mapToGlobal(pos))
                else:
                    self._hover_tooltip.hide()
            elif event.type() in {QEvent.Type.Leave, QEvent.Type.HoverLeave}:
                self._hover_tooltip.hide()
        return super().eventFilter(watched, event)


class CenteredCheckboxDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index) -> None:
        if index.column() != 0:
            super().paint(painter, option, index)
            return
        style = option.widget.style() if option.widget is not None else QApplication.style()
        item_option = QStyleOptionViewItem(option)
        self.initStyleOption(item_option, index)
        item_option.text = ""
        item_option.icon = QIcon()
        style.drawPrimitive(QStyle.PE_PanelItemViewItem, item_option, painter, option.widget)
        check_state = index.data(Qt.CheckStateRole)
        if check_state is None:
            check_state = Qt.Unchecked
        elif isinstance(check_state, int):
            check_state = Qt.CheckState(check_state)
        indicator_size = max(
            style.pixelMetric(QStyle.PM_IndicatorWidth, None, option.widget),
            style.pixelMetric(QStyle.PM_IndicatorHeight, None, option.widget),
            16,
        )
        indicator_rect = QRect(0, 0, indicator_size, indicator_size)
        indicator_rect.moveCenter(option.rect.center())
        self._paint_checkbox_indicator(
            painter,
            indicator_rect,
            check_state,
            hovered=bool(option.state & QStyle.State_MouseOver),
            enabled=bool(option.state & QStyle.State_Enabled),
            option=option,
        )

    @staticmethod
    def _paint_checkbox_indicator(
        painter: QPainter,
        rect: QRect,
        check_state,
        *,
        hovered: bool,
        enabled: bool,
        option,
    ) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        palette = option.palette
        border = palette.color(palette.ColorRole.Mid)
        background = palette.color(palette.ColorRole.Base)
        check_fill = palette.color(palette.ColorRole.Highlight)
        check_mark = palette.color(palette.ColorRole.HighlightedText)

        if hovered:
            border = border.lighter(110)
        if not enabled:
            border.setAlpha(140)
            background.setAlpha(180)
            check_fill.setAlpha(150)
            check_mark.setAlpha(180)

        box_rect = rect.adjusted(1, 1, -1, -1)
        radius = min(box_rect.width(), box_rect.height()) / 4

        painter.setPen(QPen(border, 1.2))
        painter.setBrush(background)
        painter.drawRoundedRect(box_rect, radius, radius)

        if check_state == Qt.CheckState.Checked:
            fill_rect = box_rect.adjusted(1, 1, -1, -1)
            painter.setPen(Qt.NoPen)
            painter.setBrush(check_fill)
            painter.drawRoundedRect(fill_rect, max(radius - 1, 2), max(radius - 1, 2))

            tick = QPainterPath()
            tick.moveTo(fill_rect.left() + fill_rect.width() * 0.22, fill_rect.top() + fill_rect.height() * 0.56)
            tick.lineTo(fill_rect.left() + fill_rect.width() * 0.43, fill_rect.top() + fill_rect.height() * 0.76)
            tick.lineTo(fill_rect.left() + fill_rect.width() * 0.78, fill_rect.top() + fill_rect.height() * 0.30)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(check_mark, max(2.0, fill_rect.width() / 7.0), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPath(tick)
        elif check_state == Qt.CheckState.PartiallyChecked:
            dash_color = QColor(check_fill)
            painter.setPen(Qt.NoPen)
            painter.setBrush(dash_color)
            dash_rect = QRect(
                box_rect.left() + box_rect.width() // 4,
                box_rect.center().y() - 1,
                max(box_rect.width() // 2, 6),
                3,
            )
            painter.drawRoundedRect(dash_rect, 1.5, 1.5)

        painter.restore()


class NodeManagerPage(QWidget):
    messageRequested = Signal(str, str)
    groupJoinSucceeded = Signal(object, str)
    groupJoinFailed = Signal(str, str)

    def __init__(
        self,
        ctx,
        save_nodes,
        apply_layout=None,
        restore_nodes=None,
        latest_backup=None,
        coord_client=None,
        parent=None,
    ):
        super().__init__(parent)
        self.ctx = ctx
        self._save_nodes = save_nodes
        self._apply_layout = apply_layout
        self._restore_nodes = restore_nodes
        self._latest_backup = latest_backup or (lambda: None)
        self._coord_client = coord_client
        self._last_status_text = ""
        self._group_join_in_progress = False
        self.groupJoinSucceeded.connect(self._handle_group_join_payload)
        self.groupJoinFailed.connect(self._handle_group_join_failure)
        if self._coord_client is not None and hasattr(self._coord_client, "add_node_list_change_listener"):
            self._coord_client.add_node_list_change_listener(self._handle_node_list_change)
        self._build()
        self.refresh()

    def refresh(self) -> None:
        checked_ids = set(self._checked_node_ids())
        display_nodes = self._display_nodes()
        self._table.blockSignals(True)
        self._table.setRowCount(len(display_nodes))
        for row, node in enumerate(display_nodes):
            check_item = self._table.item(row, 0)
            if check_item is None:
                check_item = QTableWidgetItem()
                check_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                self._table.setItem(row, 0, check_item)
            check_item.setData(NODE_ID_ROLE, node.node_id)
            check_item.setCheckState(Qt.Checked if node.node_id in checked_ids else Qt.Unchecked)
            check_item.setText("")
            check_item.setTextAlignment(Qt.AlignCenter)
            check_item.setToolTip(NODE_TABLE_COLUMN_TOOLTIPS[0])
            self._table.set_hover_tooltip(check_item, NODE_TABLE_COLUMN_TOOLTIPS[0])

            self._set_text_item(row, 1, node.name, node.node_id)
            self._set_text_item(row, 2, node.ip, node.node_id)
            self._set_text_item(row, 3, _priority_display_text(getattr(node, "priority", DEFAULT_COORDINATOR_PRIORITY)), node.node_id)
            self._set_text_item(row, 4, getattr(node, "note", "") or "", node.node_id)
        self._table.blockSignals(False)
        self._table.resizeColumnsToContents()
        self._table.resizeRowsToContents()
        self._update_action_state()

    def _display_nodes(self) -> list:
        self_id = self.ctx.self_node.node_id
        return sorted(
            self.ctx.nodes,
            key=lambda node: (0 if node.node_id == self_id else 1, node.name.lower(), node.node_id.lower()),
        )

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        intro = QLabel("체크박스로 노드를 선택해 수정하거나 삭제할 수 있습니다. 새 노드 추가와 편집은 별도 창에서 진행됩니다.")
        intro.setWordWrap(True)
        intro.setObjectName("subtle")
        root.addWidget(intro)

        panel = QFrame()
        panel.setObjectName("panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(12, 12, 12, 12)
        panel_layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("노드 목록")
        title.setObjectName("heading")
        title.setStyleSheet("font-size: 16px;")
        self._selection_summary = QLabel("")
        self._selection_summary.setObjectName("subtle")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._selection_summary)
        panel_layout.addLayout(header)

        self._table = NodeTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(("선택", "이름", "IP", "우선순위", "비고"))
        self._table.verticalHeader().hide()
        self._table.setSelectionMode(QAbstractItemView.NoSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setFocusPolicy(Qt.NoFocus)
        self._table.setItemDelegateForColumn(0, CenteredCheckboxDelegate(self._table))
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        header_view = self._table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header_view.setStretchLastSection(False)
        self._apply_column_tooltips()
        panel_layout.addWidget(self._table, 1)

        actions = QHBoxLayout()
        self._new_button = QPushButton("새 노드")
        self._new_button.clicked.connect(self._create_node)
        self._join_button = QPushButton("그룹 참여")
        self._join_button.clicked.connect(self._join_group)
        self._edit_button = QPushButton("수정")
        self._edit_button.clicked.connect(self._edit_selected)
        self._delete_button = QPushButton("삭제")
        self._delete_button.clicked.connect(self._delete_selected)
        self._restore_button = QPushButton("직전 상태 복구")
        self._restore_button.clicked.connect(self._restore_latest_backup)
        actions.addWidget(self._new_button)
        actions.addWidget(self._join_button)
        actions.addWidget(self._edit_button)
        actions.addWidget(self._delete_button)
        actions.addStretch(1)
        actions.addWidget(self._restore_button)
        panel_layout.addLayout(actions)

        root.addWidget(panel, 1)

    def _set_text_item(self, row: int, column: int, text: str, node_id: str) -> None:
        item = self._table.item(row, column)
        if item is None:
            item = QTableWidgetItem()
            self._table.setItem(row, column, item)
        item.setText(text)
        item.setData(NODE_ID_ROLE, node_id)
        item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        item.setToolTip(self._column_tooltip(column))
        self._table.set_hover_tooltip(item, self._column_tooltip(column))

    def _apply_column_tooltips(self) -> None:
        for column, tooltip in enumerate(NODE_TABLE_COLUMN_TOOLTIPS):
            header_item = self._table.horizontalHeaderItem(column)
            if header_item is not None:
                header_item.setToolTip(tooltip)
            header_view = self._table.horizontalHeader()
            if isinstance(header_view, NodeTableHeaderView):
                header_view.set_section_tooltip(column, tooltip)

    def _column_tooltip(self, column: int) -> str:
        if 0 <= column < len(NODE_TABLE_COLUMN_TOOLTIPS):
            return NODE_TABLE_COLUMN_TOOLTIPS[column]
        return ""

    def _set_status(self, text: str) -> None:
        self._last_status_text = text

    def _checked_node_ids(self) -> list[str]:
        checked = []
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item is None or item.checkState() != Qt.Checked:
                continue
            node_id = item.data(NODE_ID_ROLE)
            if node_id:
                checked.append(str(node_id))
        return checked

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        self._update_action_state()

    def _on_cell_clicked(self, row: int, column: int) -> None:
        if column not in {0, 1, 2, 3, 4}:
            return
        self._toggle_row_checked(row)

    def _on_cell_double_clicked(self, row: int, column: int) -> None:
        if column not in {1, 2, 3, 4}:
            return
        node_id = self._node_id_for_row(row)
        if node_id:
            self._edit_node_by_id(node_id)

    def _update_action_state(self) -> None:
        checked = self._checked_node_ids()
        count = len(checked)
        self._selection_summary.setText("선택 없음" if count == 0 else f"{count}개 선택")
        self._edit_button.setEnabled(count > 0)
        self._delete_button.setEnabled(count > 0)
        self._restore_button.setEnabled(callable(self._restore_nodes))
        self._join_button.setEnabled(not self._group_join_in_progress)

    def _node_id_for_row(self, row: int) -> str | None:
        item = self._table.item(row, 0)
        if item is None:
            return None
        node_id = item.data(NODE_ID_ROLE)
        return None if not node_id else str(node_id)

    def _toggle_row_checked(self, row: int) -> None:
        item = self._table.item(row, 0)
        if item is None:
            return
        next_state = Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked
        self._table.blockSignals(True)
        item.setCheckState(next_state)
        self._table.blockSignals(False)
        self._update_action_state()

    def _open_node_editor(self, *, title: str, payload: dict | None = None) -> dict | None:
        dialog = NodeEditorDialog(title=title, payload=payload, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return None
        return dialog.payload()

    def _create_node(self) -> None:
        payload = self._open_node_editor(title="새 노드 추가")
        if payload is None:
            return
        self._save_payload(selected_name=None, payload=payload)

    def _edit_selected(self) -> None:
        checked = self._checked_node_ids()
        if len(checked) != 1:
            self._show_quiet_notice(
                "수정 안내",
                "노드 수정은 체크박스로 하나의 노드만 선택했을 때 사용할 수 있습니다.",
            )
            return
        self._edit_node_by_id(checked[0])

    def _edit_node_by_id(self, node_id: str) -> None:
        node = self.ctx.get_node(node_id)
        if node is None:
            self._set_status("선택한 노드를 찾을 수 없습니다.")
            return
        payload = self._open_node_editor(
            title=f"{node.name} 수정",
            payload=_node_to_payload(node),
        )
        if payload is None:
            return
        self._save_payload(selected_name=node.node_id, payload=payload)

    def _save_payload(self, *, selected_name: str | None, payload: dict) -> None:
        try:
            requires_restart, impact_text = self._describe_save_impact(selected_name, payload)
            if impact_text == "변경된 내용이 없습니다.":
                self._set_status(impact_text)
                return
            nodes, rename_map = self._build_nodes_payload(selected_name, payload)
            self._save_nodes(nodes, rename_map=rename_map, apply_runtime=not requires_restart)
        except Exception as exc:
            self._set_status(format_config_persist_error(exc, action="노드 저장"))
            return

        self._table.blockSignals(True)
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item is not None:
                item.setCheckState(Qt.Unchecked)
        self._table.blockSignals(False)

        if requires_restart:
            self._set_status("현재 PC 변경은 앱을 다시 시작한 뒤 반영됩니다.")
            self.messageRequested.emit("현재 PC 정보 변경은 앱 다시 시작 후 반영됩니다.", "warning")
            QMessageBox.information(
                self,
                "재시작 필요",
                impact_text + "\n\n설정 파일에는 저장되었고, 프로그램을 다시 시작하면 변경 내용이 반영됩니다.",
            )
            self._update_action_state()
            return

        self._sync_nodes_via_coordinator(
            nodes,
            rename_map=rename_map,
            allow_runtime_sync=not requires_restart,
        )

        self._set_status("노드 목록을 저장했습니다.")
        self.messageRequested.emit("노드 목록을 저장했습니다.", "success")
        self.refresh()

    def _build_nodes_payload(
        self,
        selected_name: str | None,
        payload: dict,
    ) -> tuple[list[dict], dict[str, str]]:
        nodes = [_node_to_payload(node) for node in self.ctx.nodes]
        payload_node_id = str(payload.get("node_id") or "").strip()
        payload_name = str(payload["name"]).strip()
        rename_map: dict[str, str] = {}
        if selected_name is None:
            if not payload_node_id:
                payload_node_id = generate_unique_node_id(nodes)
            if any(node["node_id"] == payload_node_id for node in nodes):
                raise ValueError("같은 식별자의 노드가 이미 있습니다.")
            if any(node["name"] == payload_name for node in nodes):
                raise ValueError("같은 이름의 노드가 이미 있습니다.")
            if any(node["name"] == payload_node_id for node in nodes):
                raise ValueError("식별자가 다른 노드의 이름과 충돌합니다.")
            if any(node["node_id"] == payload_name for node in nodes):
                raise ValueError("이름이 다른 노드의 식별자와 충돌합니다.")
            payload = dict(payload)
            payload["node_id"] = payload_node_id
            payload["name"] = payload_name
            nodes.append(payload)
            return nodes, rename_map

        if not payload_node_id:
            payload_node_id = selected_name
        for node in nodes:
            if node["node_id"] == payload_node_id and node["node_id"] != selected_name:
                raise ValueError("같은 식별자의 노드가 이미 있습니다.")
            if node["name"] == payload_name and node["node_id"] != selected_name:
                raise ValueError("같은 이름의 노드가 이미 있습니다.")
            if node["name"] == payload_node_id and node["node_id"] != selected_name:
                raise ValueError("식별자가 다른 노드의 이름과 충돌합니다.")
            if node["node_id"] == payload_name and node["node_id"] != selected_name:
                raise ValueError("이름이 다른 노드의 식별자와 충돌합니다.")

        updated = False
        for node in nodes:
            if node["node_id"] != selected_name:
                continue
            updated_payload = dict(payload)
            updated_payload["node_id"] = payload_node_id
            updated_payload["name"] = payload_name
            if payload_node_id != selected_name:
                rename_map[selected_name] = payload_node_id
            node.update(updated_payload)
            updated = True
            break
        if not updated:
            raise ValueError(f"{self._node_display_label(selected_name)} 노드를 찾을 수 없습니다.")
        return nodes, rename_map

    def _describe_save_impact(self, selected_name: str | None, payload: dict) -> tuple[bool, str]:
        if selected_name is None:
            return False, "새 노드를 추가합니다."
        current = self.ctx.get_node(selected_name)
        if current is None:
            raise ValueError(f"{self._node_display_label(selected_name)} 노드를 찾을 수 없습니다.")
        changed = []
        next_node_id = str(payload.get("node_id") or payload["name"]).strip()
        if payload["name"] != current.name:
            changed.append("이름")
        if next_node_id != current.node_id:
            changed.append("식별자")
        if payload["ip"] != current.ip:
            changed.append("IP")
        if payload.get("note", "") != getattr(current, "note", ""):
            changed.append("비고")
        if int(payload.get("priority", DEFAULT_COORDINATOR_PRIORITY)) != int(
            getattr(current, "priority", DEFAULT_COORDINATOR_PRIORITY)
        ):
            changed.append("우선순위")
        if not changed:
            return False, "변경된 내용이 없습니다."
        if current.node_id == self.ctx.self_node.node_id and any(field in {"식별자", "IP"} for field in changed):
            return True, f"내 PC의 {', '.join(changed)} 변경은 앱 다시 시작 후 반영됩니다."
        return False, f"{current.name} 노드의 {', '.join(changed)} 변경을 바로 반영합니다."

    def _delete_selected(self) -> None:
        checked = self._checked_node_ids()
        if not checked:
            QMessageBox.information(self, "삭제 안내", "삭제할 노드를 먼저 체크해 주세요.")
            return
        if self.ctx.self_node.node_id in checked:
            QMessageBox.warning(self, "삭제 불가", "내 PC는 삭제할 수 없습니다.")
            return
        label = ", ".join(self._node_display_label(node_id) for node_id in checked[:4])
        if len(checked) > 4:
            label += f" 외 {len(checked) - 4}개"
        confirmed = QMessageBox.question(
            self,
            "노드 삭제",
            f"선택한 노드를 삭제할까요?\n\n{label}",
        )
        if confirmed != QMessageBox.Yes:
            return

        nodes = [_node_to_payload(node) for node in self.ctx.nodes if node.node_id not in checked]
        try:
            self._save_nodes(nodes, rename_map={}, apply_runtime=True)
        except Exception as exc:
            self._set_status(format_config_persist_error(exc, action="노드 삭제"))
            return

        self._set_status("선택한 노드를 삭제했습니다.")
        self.messageRequested.emit("선택한 노드를 삭제했습니다.", "warning")
        self.refresh()

    def _join_group(self) -> None:
        dialog = GroupJoinDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        target_ip = dialog.target_ip()
        if not target_ip:
            return
        self._group_join_in_progress = True
        self._update_action_state()
        detail = f"{target_ip} PC에 연결해 노드 그룹 참여를 요청하는 중입니다."
        self._set_status(detail)
        self.messageRequested.emit(detail, "accent")
        self._start_group_join_worker(target_ip)
        return

    def _start_group_join_worker(self, target_ip: str) -> None:
        threading.Thread(
            target=self._group_join_worker,
            args=(target_ip,),
            daemon=True,
            name=f"group-join-{target_ip}",
        ).start()

    def _group_join_worker(self, target_ip: str) -> None:
        try:
            payload = request_group_join_state(target_ip, self.ctx.self_node.node_id)
            self.groupJoinSucceeded.emit(payload, target_ip)
        except Exception as exc:
            self.groupJoinFailed.emit(target_ip, str(exc))

    def _handle_group_join_payload(self, payload: object, target_ip: str) -> None:
        self._group_join_in_progress = False
        self._update_action_state()
        payload = payload if isinstance(payload, dict) else {}
        if payload.get("accepted") is False:
            detail = str(payload.get("detail") or "노드 그룹 참여가 거부되었습니다.")
            self._handle_group_join_failure(target_ip, detail)
            return
        nodes = payload.get("nodes") or []
        detail = str(payload.get("detail") or "").strip()
        pending_join_node_ids = [
            str(node.get("node_id") or node.get("name") or "").strip()
            for node in nodes
            if isinstance(node, dict)
            and str(node.get("node_id") or node.get("name") or "").strip()
            and str(node.get("node_id") or node.get("name") or "").strip() != self.ctx.self_node.node_id
        ]
        if hasattr(self.ctx, "set_pending_join_nodes"):
            self.ctx.set_pending_join_nodes(pending_join_node_ids)
        try:
            self._save_nodes(nodes, rename_map={}, apply_runtime=True)
        except Exception as exc:
            self._handle_group_join_failure(target_ip, format_config_persist_error(exc, action="그룹 참여 저장"))
            return
        finally:
            if hasattr(self.ctx, "clear_pending_join_nodes"):
                self.ctx.clear_pending_join_nodes(pending_join_node_ids)

        raw_layout = payload.get("layout")
        if isinstance(raw_layout, dict) and callable(self._apply_layout):
            try:
                layout = build_layout_config({"layout": raw_layout}, self.ctx.nodes)
                self._apply_layout(layout, persist=True)
            except Exception as exc:
                self._handle_group_join_failure(target_ip, f"레이아웃 동기화 실패: {exc}")
                return

        self._sync_nodes_via_coordinator(nodes, rename_map={}, allow_runtime_sync=True)
        if detail:
            self._set_status(detail)
            self.messageRequested.emit(detail, "neutral")
        success_message = "노드 그룹에 참여했습니다."
        self._set_status(success_message)
        self.messageRequested.emit(success_message, "success")
        self.refresh()

    def _handle_group_join_failure(self, target_ip: str, detail: str) -> None:
        self._group_join_in_progress = False
        self._update_action_state()
        message = f"노드 그룹 참여에 실패했습니다: {detail}"
        self._set_status(message)
        self.messageRequested.emit(message, "warning")

    def _handle_node_list_change(self, payload: dict | None = None) -> None:
        payload = payload if isinstance(payload, dict) else {}
        reject_reason = str(payload.get("reject_reason") or "").strip()
        if reject_reason == "timeout":
            message = "노드 목록 변경 요청이 시간 안에 확인되지 않았습니다. 변경 내용을 확인한 뒤 다시 시도해 주세요."
            self._set_status(message)
            self.messageRequested.emit(message, "warning")
            return
        if reject_reason != "stale_revision":
            return
        message = "다른 PC에서 먼저 노드 목록을 변경해 최신 상태로 다시 동기화했습니다. 변경 내용을 확인한 뒤 다시 시도해 주세요."
        self._set_status(message)
        self.messageRequested.emit(message, "warning")

    def _restore_latest_backup(self) -> None:
        if not callable(self._restore_nodes):
            self._set_status("복구 기능을 사용할 수 없습니다.")
            return
        latest = self._latest_backup()
        if latest is None:
            self._set_status("복구할 직전 상태가 없습니다.")
            return
        confirmed = QMessageBox.question(
            self,
            "직전 상태 복구",
            f"{latest.name} 백업으로 되돌릴까요?\n현재 노드 목록과 레이아웃 보정 정보가 함께 복구됩니다.",
        )
        if confirmed != QMessageBox.Yes:
            return
        try:
            restored_path, applied_runtime, detail = self._restore_nodes()
        except Exception as exc:
            self._set_status(format_config_persist_error(exc, action="복구"))
            return
        if applied_runtime:
            self._set_status("직전 상태를 복구했고 현재 실행에도 바로 반영했습니다.")
            self.messageRequested.emit(f"직전 상태를 복구했습니다. ({restored_path.name})", "success")
            self.refresh()
            return
        self._set_status("직전 상태를 복구했습니다. 다시 시작 후 반영됩니다.")
        self.messageRequested.emit(
            f"직전 상태를 복구했습니다. 다시 시작 후 반영됩니다. ({restored_path.name})",
            "warning",
        )
        QMessageBox.information(self, "재시작 필요", detail)

    def _show_quiet_notice(self, title: str, text: str) -> int:
        dialog = QMessageBox(self)
        dialog.setWindowTitle(title)
        dialog.setText(text)
        dialog.setIcon(QMessageBox.NoIcon)
        dialog.setStandardButtons(QMessageBox.Ok)
        ok_button = dialog.button(QMessageBox.Ok)
        if ok_button is not None:
            ok_button.setText("확인")
        return dialog.exec()

    def _sync_nodes_via_coordinator(
        self,
        nodes: list[dict],
        *,
        rename_map: dict[str, str],
        allow_runtime_sync: bool,
    ) -> None:
        if not allow_runtime_sync:
            return
        if self._coord_client is None or not hasattr(self._coord_client, "request_node_list_update"):
            return
        sent = self._coord_client.request_node_list_update(nodes, rename_map=rename_map)
        if not sent and self._has_online_peer():
            self.messageRequested.emit("노드 목록 변경을 다른 노드에 전달하지 못했습니다.", "warning")

    def _node_display_label(self, node_id: str) -> str:
        node = self.ctx.get_node(str(node_id or ""))
        if node is None:
            return str(node_id or "노드")
        return node.display_label()

    def _has_online_peer(self) -> bool:
        registry = getattr(self._coord_client, "registry", None)
        if registry is None or not hasattr(registry, "all"):
            return False
        for peer_id, conn in registry.all():
            if peer_id == self.ctx.self_node.node_id:
                continue
            if conn is not None and not getattr(conn, "closed", False):
                return True
        return False
