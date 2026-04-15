"""Qt main window shell for runtime monitoring and editing."""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from runtime.app_log_buffer import available_ui_log_levels
from runtime.gui_style import PALETTE
from runtime.app_version import get_current_version_label
from runtime.hover_tooltip import HoverTooltip
from runtime.layout_editor import LayoutEditor
from runtime.node_dialogs import NodeManagerPage
from runtime.scroll_utils import attach_horizontal_scroll_interaction
from runtime.settings_page import SettingsPage
from runtime.status_controller import StatusController
from runtime.status_tray import StatusTray
from runtime.status_view import (
    build_connection_summary_text,
    build_primary_status_text,
)


class SummaryCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setMouseTracking(True)
        self._hover_tooltip = HoverTooltip(self)
        self._tooltip_text = ""
        layout = QVBoxLayout(self)
        self.title = QLabel()
        self.title.setObjectName("cardTitle")
        self.title.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.value = QLabel()
        self.value.setObjectName("cardValue")
        self.value.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.detail.setObjectName("subtle")
        self.detail.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        self.detail.hide()

    def apply(self, card) -> None:
        self.title.setText(card.title)
        self.value.setText(card.value)
        self.detail.setText(card.detail)
        self._tooltip_text = card.detail or ""
        self.setToolTip("")
        self.title.setToolTip("")
        self.value.setToolTip("")

    def enterEvent(self, event):  # noqa: N802
        self._show_tooltip(event.position().toPoint() if hasattr(event, "position") else self.rect().center())
        super().enterEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        self._show_tooltip(event.position().toPoint())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._hover_tooltip.hide()
        super().leaveEvent(event)

    def _show_tooltip(self, local_pos) -> None:
        if not self._tooltip_text:
            return
        self._hover_tooltip.show_text(self._tooltip_text, self.mapToGlobal(local_pos))


class BadgeLabel(QLabel):
    def apply_badge(self, badge) -> None:
        self.setText(badge.text)
        self.setStyleSheet(
            "padding: 4px 8px; border-radius: 6px; background: %s; color: %s; font-weight: 600;"
            % (__import__("runtime.gui_style", fromlist=["palette_for_tone"]).palette_for_tone(badge.tone))
        )


class HoverTooltipTableWidget(QTableWidget):
    TOOLTIP_ROLE = Qt.UserRole + 10

    def __init__(self, rows: int, columns: int, parent=None):
        super().__init__(rows, columns, parent)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.ElideRight)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        attach_horizontal_scroll_interaction(self)
        self._hover_tooltip = HoverTooltip(self)
        self.viewport().installEventFilter(self)

    def set_hover_tooltip(self, item: QTableWidgetItem, text: str) -> None:
        item.setToolTip("")
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

class ScrollableListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.ElideNone)
        self.setSpacing(0)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        attach_horizontal_scroll_interaction(self)


class StatusWindow(QMainWindow):
    PAGE_OVERVIEW = 0
    PAGE_LAYOUT = 1
    PAGE_NODES = 2
    PAGE_SETTINGS = 3
    PAGE_ADVANCED = 4

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
        request_quit=None,
        ui_mode: str = "gui",
        refresh_ms: int = 250,
    ):
        super().__init__()
        self.ctx = ctx
        self.registry = registry
        self.coordinator_resolver = coordinator_resolver
        self.router = router
        self.sink = sink
        self.coord_client = coord_client
        self.config_reloader = config_reloader
        self.monitor_inventory_manager = monitor_inventory_manager
        self.request_quit = request_quit
        self.ui_mode = ui_mode
        self._selection_sync = False
        self._allow_close = False
        self._status_tray = None
        self._current_page = self.PAGE_OVERVIEW
        self._last_update_banner_tag = None
        self._message_history_expanded = False
        self._message_history_target_expanded = False
        self._message_history_entries = ()
        self._message_history_dirty = False
        self._latest_logs = ()
        self._log_list_dirty = False
        self._available_log_levels = available_ui_log_levels(
            debug_enabled=logging.getLogger().isEnabledFor(logging.DEBUG)
        )
        self._active_log_levels = set(self._available_log_levels)
        self.controller = StatusController(
            ctx,
            registry,
            coordinator_resolver,
            router=router,
            sink=sink,
            coord_client=coord_client,
            refresh_ms=refresh_ms,
            parent=self,
        )
        if self.config_reloader is not None and hasattr(self.config_reloader, "set_save_error_notifier"):
            self.config_reloader.set_save_error_notifier(self.controller.set_message)
        self.setWindowTitle(f"{self.ctx.self_node.node_id} | {get_current_version_label()}")
        self.resize(680, 740)
        self._build()
        self._connect_controller()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self.controller.start()

    def attach_tray(self, tray: StatusTray | None) -> None:
        self._status_tray = tray

    def force_close(self) -> None:
        self._allow_close = True
        self.close()

    def closeEvent(self, event):  # noqa: N802
        if not self._allow_close and self._status_tray is not None and self._status_tray.available():
            self.hide()
            self._status_tray.show_notification("트레이에서 계속 실행 중입니다.")
            self._status_tray.refresh()
            event.ignore()
            return
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self.controller.stop()
        self._layout_editor.close()
        super().closeEvent(event)

    def _build(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        self._update_banner = QFrame()
        self._update_banner.setObjectName("banner")
        update_banner_layout = QHBoxLayout(self._update_banner)
        update_banner_text = QVBoxLayout()
        update_banner_text.setSpacing(2)
        self._update_banner_title = QLabel("")
        self._update_banner_title.setStyleSheet("font-weight: 700;")
        self._update_banner_detail = QLabel("")
        self._update_banner_detail.setWordWrap(True)
        update_banner_text.addWidget(self._update_banner_title)
        update_banner_text.addWidget(self._update_banner_detail)
        update_banner_layout.addLayout(update_banner_text, 1)
        self._update_banner_button = QPushButton("업데이트 설치")
        self._update_banner_button.setObjectName("primary")
        self._update_banner_button.clicked.connect(self._install_available_update)
        update_banner_layout.addWidget(self._update_banner_button, alignment=Qt.AlignVCenter)
        outer.addWidget(self._update_banner)
        self._update_banner.hide()

        self._banner = QFrame()
        self._banner.setObjectName("banner")
        banner_layout = QHBoxLayout(self._banner)
        banner_layout.setContentsMargins(12, 10, 12, 10)
        banner_layout.setSpacing(10)
        self._banner_label = QLabel("")
        self._banner_label.setWordWrap(True)
        banner_layout.addWidget(self._banner_label, 1)
        self._message_history_toggle = QToolButton()
        self._message_history_toggle.setObjectName("bannerDisclosureButton")
        self._message_history_toggle.setAutoRaise(True)
        self._message_history_toggle.setText("▾")
        self._message_history_toggle.setToolTip("메시지 히스토리 열기")
        self._message_history_toggle.setCursor(Qt.PointingHandCursor)
        self._message_history_toggle.clicked.connect(self._toggle_message_history)
        banner_layout.addWidget(self._message_history_toggle, alignment=Qt.AlignTop)
        outer.addWidget(self._banner)
        self._banner.hide()

        self._message_history_frame = QFrame()
        self._message_history_frame.setObjectName("panelAlt")
        self._message_history_frame.setMaximumHeight(0)
        self._message_history_frame.hide()
        history_layout = QVBoxLayout(self._message_history_frame)
        history_layout.setContentsMargins(12, 10, 12, 12)
        history_layout.setSpacing(8)
        history_header = QHBoxLayout()
        history_title = QLabel("최근 메시지")
        history_title.setStyleSheet("font-weight: 700; background: transparent;")
        history_header.addWidget(history_title)
        history_header.addStretch(1)
        history_layout.addLayout(history_header)
        self._message_history_list = ScrollableListWidget()
        self._message_history_list.setSelectionMode(QAbstractItemView.NoSelection)
        self._message_history_list.setFocusPolicy(Qt.NoFocus)
        history_layout.addWidget(self._message_history_list, 1)
        outer.addWidget(self._message_history_frame)

        self._message_history_animation = QPropertyAnimation(
            self._message_history_frame,
            b"maximumHeight",
            self,
        )
        self._message_history_animation.setDuration(160)
        self._message_history_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._message_history_animation.finished.connect(self._on_message_history_animation_finished)

        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)
        outer.addWidget(splitter, 1)

        nav_panel = QFrame()
        nav_panel.setObjectName("panel")
        nav_panel.setMaximumWidth(132)
        nav_layout = QVBoxLayout(nav_panel)
        self._nav_buttons = []
        for index, label in enumerate(("개요", "레이아웃", "노드 관리", "설정", "고급 정보")):
            button = QPushButton(label)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, current=index: self._show_page(current))
            nav_layout.addWidget(button)
            self._nav_buttons.append(button)
        nav_layout.addStretch(1)
        splitter.addWidget(nav_panel)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(10)
        self._headline = QLabel("")
        self._headline.setObjectName("heading")
        self._summary = QLabel("")
        self._summary.setObjectName("subtle")
        center_layout.addWidget(self._headline)
        center_layout.addWidget(self._summary)

        self._pages = QStackedWidget()
        self._pages.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        center_layout.addWidget(self._pages, 1)
        splitter.addWidget(center)

        self._inspector = QFrame(root)
        self._inspector.setObjectName("panel")
        self._inspector.setMinimumWidth(220)
        self._inspector.setMaximumWidth(220)
        inspector_layout = QVBoxLayout(self._inspector)
        self._inspector_title = QLabel("선택된 PC")
        self._inspector_title.setObjectName("heading")
        self._inspector_title.setStyleSheet("font-size: 16px;")
        self._inspector_subtitle = QLabel("")
        self._inspector_subtitle.setWordWrap(True)
        self._inspector_subtitle.setObjectName("subtle")
        inspector_layout.addWidget(self._inspector_title)
        inspector_layout.addWidget(self._inspector_subtitle)
        self._badge_row = QHBoxLayout()
        inspector_layout.addLayout(self._badge_row)
        self._field_frame = QFrame()
        fields_layout = QGridLayout(self._field_frame)
        fields_layout.setColumnStretch(1, 1)
        self._field_labels = []
        inspector_layout.addWidget(self._field_frame)
        self._inspector_action = QLabel("")
        self._inspector_action.setWordWrap(True)
        self._inspector_action.setObjectName("subtle")
        inspector_layout.addWidget(self._inspector_action)
        inspector_actions = QHBoxLayout()
        self._request_target_button = QPushButton("전환 요청")
        self._request_target_button.clicked.connect(self._request_selected_target)
        self._monitor_editor_button = QPushButton("모니터 맵 편집")
        self._monitor_editor_button.clicked.connect(lambda: self._layout_editor.open_monitor_editor())
        inspector_actions.addWidget(self._request_target_button)
        inspector_actions.addWidget(self._monitor_editor_button)
        inspector_actions.addStretch(1)
        inspector_layout.addLayout(inspector_actions)
        inspector_layout.addStretch(1)
        self._inspector.hide()

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([118, 840])

        self._build_overview_page()
        self._build_layout_page()
        self._build_nodes_page()
        self._build_settings_page()
        self._build_advanced_page()
        self._show_page(self.PAGE_OVERVIEW)
        self.menuBar().hide()

    def _build_overview_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self._summary_cards_layout = QHBoxLayout()
        layout.addLayout(self._summary_cards_layout)
        layout.addWidget(QLabel("노드 목록"))
        self._peer_table = HoverTooltipTableWidget(0, 4)
        self._peer_table.setHorizontalHeaderLabels(
            ("노드명", "최근 연결", "현재 버전", "모니터 배치")
        )
        header = self._peer_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setStretchLastSection(False)
        self._peer_table.verticalHeader().hide()
        self._peer_table.setSelectionMode(QAbstractItemView.NoSelection)
        self._peer_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._peer_table.setFocusPolicy(Qt.NoFocus)
        self._peer_table.cellClicked.connect(self._on_peer_table_cell_clicked)
        layout.addWidget(self._peer_table, 1)
        self._pages.addWidget(page)

    def _build_layout_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self._layout_editor = LayoutEditor(
            self.ctx,
            self.registry,
            self.coordinator_resolver,
            router=self.router,
            sink=self.sink,
            coord_client=self.coord_client,
            config_reloader=self.config_reloader,
            monitor_inventory_manager=self.monitor_inventory_manager,
        )
        self._layout_editor.nodeSelected.connect(self.controller.set_selected_node)
        self._layout_editor.messageRequested.connect(self.controller.set_message)
        layout.addWidget(self._layout_editor, 1)
        self._pages.addWidget(page)

    def _build_nodes_page(self) -> None:
        page = NodeManagerPage(
            self.ctx,
            save_nodes=self.config_reloader.save_nodes if self.config_reloader is not None else lambda *args, **kwargs: None,
            restore_nodes=None if self.config_reloader is None else self.config_reloader.restore_latest_backup,
            latest_backup=None if self.config_reloader is None else self.config_reloader.get_latest_backup_path,
            coord_client=self.coord_client,
        )
        page.messageRequested.connect(self.controller.set_message)
        self._node_manager_page = page
        self._pages.addWidget(page)

    def _build_settings_page(self) -> None:
        page = SettingsPage(
            self.ctx,
            config_reloader=self.config_reloader,
            request_quit=self.request_quit,
            ui_mode=self.ui_mode,
        )
        page.messageRequested.connect(self.controller.set_message)
        self._settings_page = page
        self._pages.addWidget(page)

    def _build_advanced_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        runtime_panel = QFrame()
        runtime_panel.setObjectName("panel")
        self._advanced_runtime_layout = QGridLayout(runtime_panel)
        self._advanced_runtime_labels = {}
        label_map = {
            "self_id": "내 PC",
            "coordinator_id": "코디네이터",
            "selected_target": "현재 대상",
            "router_state": "상태",
            "authorized_controller": "제어권",
            "connected_peers": "연결 수",
            "config_path": "설정 경로",
        }
        for row, key in enumerate(
            ("self_id", "coordinator_id", "selected_target", "router_state", "authorized_controller", "connected_peers", "config_path")
        ):
            left = QLabel(label_map[key])
            left.setObjectName("subtle")
            right = QLabel("-")
            right.setWordWrap(True)
            self._advanced_runtime_layout.addWidget(left, row, 0)
            self._advanced_runtime_layout.addWidget(right, row, 1)
            self._advanced_runtime_labels[key] = right
        layout.addWidget(runtime_panel)
        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("로그"))
        log_header.addStretch(1)
        self._log_level_buttons = {}
        for level in self._available_log_levels:
            button = QPushButton(level)
            button.setCheckable(True)
            button.setChecked(True)
            button.setProperty("compactFilter", True)
            button.clicked.connect(
                lambda checked=False, current=level: self._toggle_log_level_filter(current)
            )
            log_header.addWidget(button)
            self._log_level_buttons[level] = button
        layout.addLayout(log_header)
        self._log_list = ScrollableListWidget()
        self._log_list.setSelectionMode(QAbstractItemView.NoSelection)
        self._log_list.setFocusPolicy(Qt.NoFocus)
        layout.addWidget(self._log_list, 1)
        self._pages.addWidget(page)

    def _connect_controller(self) -> None:
        self.controller.summaryChanged.connect(self._render_summary)
        self.controller.peersChanged.connect(self._render_peers)
        self.controller.selectedNodeChanged.connect(self._render_selected_detail)
        self.controller.layoutChanged.connect(self._layout_editor.refresh)
        self.controller.advancedChanged.connect(self._render_advanced)
        self.controller.messageChanged.connect(self._render_banner)
        self.controller.messageHistoryChanged.connect(self._render_message_history)
        self._settings_page.updateNoticeChanged.connect(self._render_update_banner)
        self._render_message_history(self.controller.message_history)
        self._render_update_banner(getattr(self._settings_page, "_update_notice_payload", None))

    def _show_page(self, index: int) -> None:
        if self._current_page == self.PAGE_LAYOUT and index != self.PAGE_LAYOUT:
            self._layout_editor.deactivate_edit_mode(notify=True)
        self._pages.setCurrentIndex(index)
        for button_index, button in enumerate(self._nav_buttons):
            button.setChecked(button_index == index)
        if index == self.PAGE_LAYOUT:
            self._layout_editor.fit_view()
        if index == self.PAGE_NODES:
            self._node_manager_page.refresh()
        if index == self.PAGE_SETTINGS:
            self._settings_page.refresh()
        if index == self.PAGE_ADVANCED and self._log_list_dirty:
            self._render_log_list()
        self._current_page = index

    def _render_summary(self, view) -> None:
        self._current_view = view
        self._headline.setText(build_primary_status_text(view))
        self._summary.setText(build_connection_summary_text(view))
        while self._summary_cards_layout.count():
            item = self._summary_cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for card in view.summary_cards:
            widget = SummaryCard()
            widget.apply(card)
            self._summary_cards_layout.addWidget(widget, 1)
        self._summary_cards_layout.addStretch(1)
        if self.controller._current_message[0]:
            self._render_banner(*self.controller._current_message)
        elif view.monitor_alert:
            self._render_banner(view.monitor_alert, view.monitor_alert_tone)
        else:
            self._banner.hide()

    def _render_peers(self, peers) -> None:
        view = self.controller.current_view
        rows_payload = []
        if view is not None:
            self_detail = next((detail for detail in view.node_details if detail.node_id == view.self_id), None)
            if self_detail is not None:
                rows_payload.append(
                    {
                        "node_id": view.self_id,
                        "recent_connection": "내 PC",
                        "current_version": view.self_current_version_label,
                        "layout": next(
                            (field.value for field in self_detail.fields if field.label == "모니터 배치"),
                            "-",
                        ),
                        "version_status": "compatible",
                        "tooltip": view.self_version_tooltip,
                    }
                )
        for peer in peers:
            rows_payload.append(
                {
                    "node_id": peer.node_id,
                    "recent_connection": peer.last_seen,
                    "current_version": peer.current_version_label,
                    "layout": peer.layout_summary,
                    "version_status": peer.version_status,
                    "tooltip": peer.version_tooltip,
                }
            )
        self._peer_table.blockSignals(True)
        self._peer_table.setRowCount(len(rows_payload))
        for row, payload in enumerate(rows_payload):
            values = (
                payload["node_id"],
                payload["recent_connection"],
                payload["current_version"],
                payload["layout"],
            )
            for col, value in enumerate(values):
                item = self._peer_table.item(row, col)
                if item is None:
                    item = QTableWidgetItem()
                    self._peer_table.setItem(row, col, item)
                item.setText(value)
                self._peer_table.set_hover_tooltip(item, payload["tooltip"] if col == 2 else "")
                self._apply_peer_table_item_style(
                    item,
                    payload["version_status"] if col == 2 else None,
                )
                if col == 0:
                    item.setData(Qt.UserRole, payload["node_id"])
                if col == 2:
                    item.setData(Qt.UserRole + 1, payload["version_status"])
        self._peer_table.blockSignals(False)
        self._peer_table.resizeColumnsToContents()

    def _apply_peer_table_item_style(self, item: QTableWidgetItem, version_status: str | None) -> None:
        color = PALETTE["text"]
        bold = False
        italic = False
        if version_status == "outdated":
            color = "#a55252"
            bold = True
        elif version_status == "ahead":
            color = "#60748a"
            bold = True
        elif version_status == "unknown":
            color = "#9a6b3d"
            bold = True
            italic = True
        item.setForeground(QBrush(QColor(color)))
        font = item.font()
        font.setBold(bold)
        font.setItalic(italic)
        item.setFont(font)

    def _render_selected_detail(self, detail) -> None:
        self._selection_sync = True
        try:
            self._layout_editor.select_node(detail.node_id)
            is_empty_detail = detail.node_id == "-"
            self._inspector_title.setText("노드 정보 없음" if is_empty_detail else detail.title)
            self._inspector_subtitle.setText("" if is_empty_detail else detail.subtitle)
            while self._badge_row.count():
                item = self._badge_row.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            for badge in detail.badges:
                label = BadgeLabel()
                label.apply_badge(badge)
                self._badge_row.addWidget(label)
            self._badge_row.addStretch(1)
            field_layout = self._field_frame.layout()
            while field_layout.count():
                item = field_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            for row, field in enumerate(detail.fields):
                left = QLabel(field.label)
                left.setObjectName("subtle")
                right = QLabel(field.value)
                right.setWordWrap(True)
                field_layout.addWidget(left, row, 0)
                field_layout.addWidget(right, row, 1)
            self._inspector_action.setText("" if is_empty_detail else detail.action_label)
            if detail.node_id == self.ctx.self_node.node_id:
                self._request_target_button.setText("내 PC")
                self._request_target_button.setEnabled(False)
            elif self._is_node_online(detail.node_id):
                self._request_target_button.setText("제어 전환")
                self._request_target_button.setEnabled(self.coord_client is not None)
            else:
                self._request_target_button.setText("오프라인")
                self._request_target_button.setEnabled(False)
            self._monitor_editor_button.setEnabled(self._layout_editor.can_open_monitor_editor())
        finally:
            self._selection_sync = False

    def _render_advanced(self, payload) -> None:
        runtime = payload["runtime"]
        for key, value in runtime.items():
            if key in self._advanced_runtime_labels:
                self._advanced_runtime_labels[key].setText(str(value))
        self._latest_logs = tuple(payload.get("logs", ()))
        if self._current_page == self.PAGE_ADVANCED:
            self._render_log_list()
        else:
            self._log_list_dirty = True

    def _toggle_log_level_filter(self, level: str) -> None:
        if level in self._active_log_levels:
            self._active_log_levels.remove(level)
        else:
            self._active_log_levels.add(level)
        for current_level, button in self._log_level_buttons.items():
            button.blockSignals(True)
            button.setChecked(current_level in self._active_log_levels)
            button.blockSignals(False)
        self._render_log_list()

    def _render_log_list(self) -> None:
        self._log_list_dirty = False
        self._log_list.setUpdatesEnabled(False)
        self._log_list.clear()
        visible_logs = [
            entry
            for entry in self._latest_logs
            if entry.level in self._active_log_levels
        ]
        if not visible_logs:
            self._append_selectable_list_item(
                self._log_list,
                "표시할 로그가 없습니다.",
                QColor(PALETTE["muted"]),
                selectable=False,
            )
            self._log_list.setUpdatesEnabled(True)
            return
        for entry in visible_logs:
            tone_color = {
                "INFO": PALETTE["text"],
                "DETAIL": PALETTE["muted"],
                "DEBUG": PALETTE["neutral"],
                "WARNING": PALETTE["warning"],
                "ERROR": PALETTE["danger"],
            }.get(entry.level, PALETTE["text"])
            self._append_selectable_list_item(
                self._log_list,
                f"[{entry.timestamp}] [{entry.level}] {entry.message}",
                QColor(tone_color),
                selectable=False,
            )
        self._log_list.setUpdatesEnabled(True)

    def _render_banner(self, message: str, tone: str) -> None:
        if not message:
            self._banner.hide()
            self._set_message_history_expanded(False, animate=False)
            return
        from runtime.gui_style import palette_for_tone

        background, foreground = palette_for_tone(tone)
        self._banner.setStyleSheet(
            f"QFrame#banner{{background:{background}; border:1px solid {foreground}; border-radius:6px;}} QLabel{{background:transparent; color:{foreground};}}"
        )
        self._message_history_toggle.setStyleSheet(
            "QToolButton#bannerDisclosureButton{"
            f"color:{foreground};"
            "background:transparent;"
            "border:none;"
            "padding:0;"
            "}"
            "QToolButton#bannerDisclosureButton:hover{"
            f"background:{self._rgba_color(foreground, 24)};"
            "}"
            "QToolButton#bannerDisclosureButton:pressed{"
            f"background:{self._rgba_color(foreground, 38)};"
            "}"
        )
        self._banner_label.setText(message)
        self._message_history_toggle.setVisible(True)
        self._banner.show()

    def _render_message_history(self, entries) -> None:
        self._message_history_entries = tuple(entries or ())
        self._message_history_dirty = True
        if self._message_history_expanded or self._message_history_target_expanded:
            self._refresh_message_history_list()
        self._message_history_toggle.setVisible(self._banner.isVisible())

    def _toggle_message_history(self) -> None:
        self._set_message_history_expanded(not self._message_history_expanded)

    def _set_message_history_expanded(self, expanded: bool, *, animate: bool = True) -> None:
        if (
            self._message_history_expanded == expanded
            and self._message_history_target_expanded == expanded
            and self._message_history_animation.state() == QPropertyAnimation.Stopped
        ):
            return
        self._message_history_target_expanded = expanded
        self._message_history_toggle.setText("▴" if expanded else "▾")
        self._message_history_toggle.setToolTip(
            "메시지 히스토리 닫기" if expanded else "메시지 히스토리 열기"
        )

        start_height = self._message_history_frame.maximumHeight()
        end_height = self._message_history_target_height() if expanded else 0
        if expanded:
            self._refresh_message_history_list()
            self._message_history_frame.show()
        if not animate:
            self._message_history_animation.stop()
            self._message_history_frame.setMaximumHeight(end_height)
            self._message_history_expanded = expanded
            self._on_message_history_animation_finished()
            return

        self._message_history_animation.stop()
        self._message_history_animation.setStartValue(start_height)
        self._message_history_animation.setEndValue(end_height)
        self._message_history_animation.start()

    def _on_message_history_animation_finished(self) -> None:
        expanded = self._message_history_target_expanded
        self._message_history_expanded = expanded
        if not expanded:
            self._message_history_frame.hide()

    def _message_history_target_height(self) -> int:
        lower = max(180, int(self.height() * 0.33))
        upper = max(lower, int(self.height() * 0.5))
        preferred = max(220, int(self.height() * 0.4))
        return min(max(preferred, lower), upper)

    def _refresh_message_history_list(self) -> None:
        if not self._message_history_dirty and self._message_history_list.count():
            return
        self._message_history_dirty = False
        self._message_history_list.setUpdatesEnabled(False)
        self._message_history_list.clear()
        if self._message_history_entries:
            for entry in self._message_history_entries:
                tone = entry.get("tone", "neutral")
                self._append_selectable_list_item(
                    self._message_history_list,
                    f"[{entry['timestamp']}] {entry['message']}",
                    QColor(PALETTE.get(tone, PALETTE["text"])),
                    selectable=True,
                )
        else:
            self._append_selectable_list_item(
                self._message_history_list,
                "메시지 기록이 없습니다.",
                QColor(PALETTE["muted"]),
                selectable=False,
            )
        self._message_history_list.setUpdatesEnabled(True)

    def eventFilter(self, watched, event):  # noqa: N802
        if (
            self._message_history_expanded
            and event.type() == QEvent.Type.MouseButtonPress
            and self.isVisible()
        ):
            global_pos = (
                event.globalPosition().toPoint()
                if hasattr(event, "globalPosition")
                else event.globalPos()
            )
            if self.frameGeometry().contains(global_pos) and not any(
                self._widget_contains_global_pos(widget, global_pos)
                for widget in (self._update_banner, self._banner, self._message_history_frame)
                if widget is not None and widget.isVisible()
            ):
                self._set_message_history_expanded(False)
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if self._message_history_expanded:
            self._message_history_frame.setMaximumHeight(self._message_history_target_height())

    def _widget_contains_global_pos(self, widget: QWidget, global_pos: QPoint) -> bool:
        top_left = widget.mapToGlobal(QPoint(0, 0))
        rect = widget.rect()
        return rect.translated(top_left).contains(global_pos)

    def _rgba_color(self, color: str, alpha: int) -> str:
        qcolor = QColor(color)
        return f"rgba({qcolor.red()}, {qcolor.green()}, {qcolor.blue()}, {alpha})"

    def _render_update_banner(self, payload) -> None:
        payload = {"visible": False} if payload is None else dict(payload)
        if not payload.get("visible"):
            self._update_banner.hide()
            return
        self._update_banner_title.setText(payload.get("title", "새로운 업데이트가 있습니다!"))
        self._update_banner_detail.setText(payload.get("detail", ""))
        self._update_banner_button.setVisible(bool(payload.get("button_visible", True)))
        self._update_banner_button.setEnabled(bool(payload.get("button_enabled", True)))
        self._update_banner_button.setText(payload.get("button_text", "업데이트 설치"))
        self._update_banner.show()
        tag_name = payload.get("tag_name")
        if (
            tag_name
            and payload.get("title", "") == "새로운 업데이트가 있습니다!"
            and getattr(self, "_last_update_banner_tag", None) != tag_name
        ):
            self._last_update_banner_tag = tag_name
            if self._status_tray is not None:
                self._status_tray.show_notification(
                    f"{tag_name} 업데이트가 준비되었습니다.",
                    timeout_ms=3500,
                )

    def handle_remote_update_command(self, payload: dict | None = None) -> None:
        background = not self.isVisible()
        if background and self._status_tray is not None:
            self._status_tray.show_notification(
                "원격 업데이트 명령으로 업데이트를 시작합니다...",
                timeout_ms=3500,
            )
        self._settings_page.start_remote_update(background=background)

    def _install_available_update(self) -> None:
        self._settings_page._install_update()

    def handle_global_layout_wheel(self, global_x: int, global_y: int, dx: int, dy: int) -> None:
        self._layout_editor.handle_global_wheel(global_x, global_y, dx, dy)

    def should_handle_global_layout_wheel(self, global_x: int, global_y: int, dx: int, dy: int) -> bool:
        return self._layout_editor.should_handle_global_wheel(global_x, global_y, dx, dy)

    def _on_peer_table_cell_clicked(self, row: int, column: int) -> None:
        if column != 2:
            return
        node_item = self._peer_table.item(row, 0)
        version_item = self._peer_table.item(row, column)
        if node_item is None or version_item is None:
            return
        node_id = node_item.data(Qt.UserRole)
        version_status = version_item.data(Qt.UserRole + 1)
        if not node_id or node_id == self.ctx.self_node.node_id or self.coord_client is None:
            return
        label = self._node_display_label(str(node_id))
        if version_status == "ahead":
            self.controller.set_message(
                f"{label} 쪽이 더 최신 버전입니다. 현재 PC를 업데이트해 주세요.",
                "neutral",
            )
            return
        if version_status != "outdated":
            return
        confirmed = QMessageBox.question(
            self,
            "원격 업데이트",
            f"{label} 에 업데이트 명령을 전달하시겠습니까?",
        )
        if confirmed != QMessageBox.Yes:
            return
        if self.coord_client.request_remote_update(str(node_id)):
            self.controller.set_message(f"{label}에 업데이트 명령을 전달했습니다.", "accent")
        else:
            self.controller.set_message(f"{label}에 업데이트 명령을 전달하지 못했습니다.", "warning")

    def _on_peer_table_selection_changed(self) -> None:
        return

    def _select_peer_row(self, node_id: str | None) -> None:
        return

    def _request_target(self, node_id: str) -> None:
        if self.coord_client is None:
            return
        if node_id == self.ctx.self_node.node_id:
            self.controller.set_message("내 PC는 제어 전환 대상이 아닙니다.", "neutral")
            return
        if not self._is_node_online(node_id):
            self.controller.set_message("오프라인 PC는 제어 대상으로 선택할 수 없습니다.", "warning")
            return
        self.controller.set_selected_node(node_id)
        self.controller.set_message(f"{node_id} PC로 전환을 요청했습니다.", "accent")

        def worker():
            self.coord_client.request_target(node_id, source="ui")

        thread = threading.Thread(target=worker, daemon=True, name=f"request-target-{node_id}")
        thread.start()

    def _request_selected_target(self) -> None:
        self._layout_editor.select_node(self.controller.selected_node_id)
        self._layout_editor.request_selected_target()

    def _is_node_online(self, node_id: str) -> bool:
        if node_id == self.ctx.self_node.node_id:
            return True
        view = self.controller.current_view
        if view is None:
            return False
        peer = next((item for item in view.peers if item.node_id == node_id), None)
        return False if peer is None else peer.online

    def _node_display_label(self, node_id: str) -> str:
        node = self.ctx.get_node(node_id)
        note = "" if node is None else (getattr(node, "note", "") or "").strip()
        return f"{node_id}({note})" if note else node_id

    def _append_selectable_list_item(self, widget: QListWidget, text: str, color: QColor, *, selectable: bool) -> None:
        item = QListWidgetItem(text)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"background: transparent; color: {color.name()}; padding: 2px 6px;")
        label.setTextInteractionFlags(Qt.TextSelectableByMouse if selectable else Qt.NoTextInteraction)
        item.setFlags((item.flags() | Qt.ItemIsEnabled) & ~Qt.ItemIsSelectable)
        item.setForeground(QBrush(QColor(0, 0, 0, 0)))
        item.setSizeHint(label.sizeHint())
        widget.addItem(item)
        widget.setItemWidget(item, label)
