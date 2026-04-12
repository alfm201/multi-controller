"""Qt main window shell for runtime monitoring and editing."""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from runtime.app_identity import APP_DISPLAY_NAME
from runtime.hover_tooltip import HoverTooltip
from runtime.layout_editor import LayoutEditor
from runtime.node_dialogs import NodeManagerPage
from runtime.settings_page import SettingsPage
from runtime.status_controller import StatusController
from runtime.status_tray import StatusTray
from runtime.status_view import (
    build_connection_summary_text,
    build_primary_status_text,
    build_selection_hint_text,
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
        self._selection_sync = False
        self._allow_close = False
        self._status_tray = None
        self._current_page = self.PAGE_OVERVIEW
        self.controller = StatusController(
            ctx,
            registry,
            coordinator_resolver,
            router=router,
            sink=sink,
            refresh_ms=refresh_ms,
            parent=self,
        )
        if self.config_reloader is not None and hasattr(self.config_reloader, "set_save_error_notifier"):
            self.config_reloader.set_save_error_notifier(self.controller.set_message)
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.resize(680, 740)
        self._build()
        self._connect_controller()
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
        self.controller.stop()
        self._layout_editor.close()
        super().closeEvent(event)

    def _build(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        self._banner = QFrame()
        self._banner.setObjectName("banner")
        banner_layout = QHBoxLayout(self._banner)
        self._banner_label = QLabel("")
        self._banner_label.setWordWrap(True)
        banner_layout.addWidget(self._banner_label)
        outer.addWidget(self._banner)
        self._banner.hide()

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
        self._hint = QLabel("")
        self._hint.setObjectName("subtle")
        self._hint.setWordWrap(True)
        center_layout.addWidget(self._headline)
        center_layout.addWidget(self._summary)
        center_layout.addWidget(self._hint)

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
        self._peer_table = QTableWidget(0, 5)
        self._peer_table.setHorizontalHeaderLabels(
            ("노드명", "온라인", "최근 확인", "감지 상태", "레이아웃")
        )
        header = self._peer_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self._peer_table.verticalHeader().hide()
        self._peer_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._peer_table.setSelectionMode(QTableWidget.SingleSelection)
        self._peer_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._peer_table.itemSelectionChanged.connect(self._on_peer_table_selection_changed)
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
        )
        page.messageRequested.connect(self.controller.set_message)
        self._node_manager_page = page
        self._pages.addWidget(page)

    def _build_settings_page(self) -> None:
        page = SettingsPage(self.ctx, config_reloader=self.config_reloader)
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
            "authorized_controller": "편집권",
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
        layout.addWidget(QLabel("최근 이벤트"))
        self._event_list = QListWidget()
        layout.addWidget(self._event_list, 1)
        self._pages.addWidget(page)

    def _connect_controller(self) -> None:
        self.controller.summaryChanged.connect(self._render_summary)
        self.controller.peersChanged.connect(self._render_peers)
        self.controller.selectedNodeChanged.connect(self._render_selected_detail)
        self.controller.layoutChanged.connect(self._layout_editor.refresh)
        self.controller.advancedChanged.connect(self._render_advanced)
        self.controller.messageChanged.connect(self._render_banner)

    def _show_page(self, index: int) -> None:
        if self._current_page == self.PAGE_LAYOUT and index != self.PAGE_LAYOUT:
            self._layout_editor.deactivate_edit_mode(notify=True)
        self._pages.setCurrentIndex(index)
        for button_index, button in enumerate(self._nav_buttons):
            button.setChecked(button_index == index)
        if index == self.PAGE_LAYOUT:
            self._layout_editor.fit_view()
        if index == self.PAGE_SETTINGS:
            self._settings_page.refresh()
        self._current_page = index

    def _render_summary(self, view) -> None:
        self._current_view = view
        self._headline.setText(build_primary_status_text(view))
        self._summary.setText(build_connection_summary_text(view))
        self._hint.setText(build_selection_hint_text(view))
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
                    (
                        view.self_id,
                        "연결",
                        "내 PC",
                        next((badge.text for badge in self_detail.badges if badge.text.startswith("감지 ")), "최신"),
                        next((field.value for field in self_detail.fields if field.label == "레이아웃"), "-"),
                    )
                )
        for peer in peers:
            rows_payload.append(
                (
                    peer.node_id,
                    "연결" if peer.online else "오프라인",
                    peer.last_seen,
                    peer.freshness_label,
                    peer.layout_summary,
                )
            )
        self._peer_table.blockSignals(True)
        self._peer_table.setRowCount(len(rows_payload))
        for row, values in enumerate(rows_payload):
            for col, value in enumerate(values):
                item = self._peer_table.item(row, col)
                if item is None:
                    item = QTableWidgetItem()
                    self._peer_table.setItem(row, col, item)
                item.setText(value)
                if col == 0:
                    item.setData(Qt.UserRole, values[0])
        self._peer_table.blockSignals(False)
        self._select_peer_row(self.controller.selected_node_id)

    def _render_selected_detail(self, detail) -> None:
        self._selection_sync = True
        try:
            self._layout_editor.select_node(detail.node_id)
            self._select_peer_row(detail.node_id)
            self._inspector_title.setText(detail.title)
            self._inspector_subtitle.setText(detail.subtitle)
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
            self._inspector_action.setText(detail.action_label)
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
        self._event_list.clear()
        for event in payload["events"]:
            self._event_list.addItem(event)
        self._node_manager_page.refresh()

    def _render_banner(self, message: str, tone: str) -> None:
        if not message:
            self._banner.hide()
            return
        from runtime.gui_style import palette_for_tone

        background, foreground = palette_for_tone(tone)
        self._banner.setStyleSheet(
            f"QFrame#banner{{background:{background}; border:1px solid {foreground}; border-radius:6px;}} QLabel{{background:transparent; color:{foreground};}}"
        )
        self._banner_label.setText(message)
        self._banner.show()

    def _on_peer_table_selection_changed(self) -> None:
        if self._selection_sync:
            return
        rows = self._peer_table.selectionModel().selectedRows()
        if not rows:
            return
        node_id = self._peer_table.item(rows[0].row(), 0).data(Qt.UserRole)
        self.controller.set_selected_node(node_id)
        self._layout_editor.select_node(node_id)

    def _select_peer_row(self, node_id: str | None) -> None:
        if node_id is None or not hasattr(self, "_peer_table"):
            return
        self._peer_table.blockSignals(True)
        try:
            self._peer_table.clearSelection()
            for row in range(self._peer_table.rowCount()):
                item = self._peer_table.item(row, 0)
                if item is not None and item.data(Qt.UserRole) == node_id:
                    self._peer_table.selectRow(row)
                    break
        finally:
            self._peer_table.blockSignals(False)

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
            self.coord_client.request_target(node_id)

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
