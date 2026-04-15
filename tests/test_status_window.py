"""Tests for runtime/status_window.py."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QMessageBox

from runtime.context import build_runtime_context
from runtime.gui_style import PALETTE
from runtime.app_version import get_current_version_label
from runtime.settings_page import HelpDot
from runtime.status_window import HoverTooltipTableWidget, StatusWindow, SummaryCard


class FakeConn:
    def __init__(
        self,
        closed=False,
        *,
        peer_app_version=None,
        peer_compatibility_version=None,
    ):
        self.closed = closed
        self.peer_app_version = peer_app_version
        self.peer_compatibility_version = peer_compatibility_version


class FakeRegistry:
    def __init__(self, pairs):
        self._pairs = pairs

    def all(self):
        return list(self._pairs)


class FakeRouter:
    def __init__(self, state, target):
        self._state = state
        self._target = target

    def get_target_state(self):
        return self._state

    def get_requested_target(self):
        return self._target

    def get_active_target(self):
        if self._state == "active":
            return self._target
        return None

    def get_selected_target(self):
        return self._target


class FakeSink:
    def __init__(self, controller_id):
        self._controller_id = controller_id

    def get_authorized_controller(self):
        return self._controller_id


class FakeCoordClient:
    def __init__(self):
        self.requested = []
        self.cleared = 0
        self.remote_updates = []
        self._is_editor = False
        self._pending = False

    def request_target(self, node_id, source=None):
        self.requested.append((node_id, source))

    def clear_target(self):
        self.cleared += 1

    def is_layout_editor(self):
        return self._is_editor

    def get_layout_editor(self):
        return "A" if self._is_editor else None

    def is_layout_edit_pending(self):
        return self._pending

    def publish_layout(self, layout, persist=True):
        return True

    def request_layout_edit(self):
        self._pending = True

    def end_layout_edit(self):
        self._is_editor = False
        self._pending = False

    def request_monitor_inventory_refresh(self, _node_id):
        return True

    def request_remote_update(self, node_id):
        self.remote_updates.append(node_id)
        return True

    def get_layout_edit_denial(self):
        return None


def _layout_ctx():
    config = {
        "nodes": [
            {"name": "A", "ip": "127.0.0.1", "port": 5000},
            {"name": "B", "ip": "127.0.0.1", "port": 5001, "note": "회의실"},
        ],
    }
    return build_runtime_context(config, override_name="A", config_path="config/config.json")


def _seed_message_history(window):
    window.controller.set_message("첫 번째 메시지", "neutral")
    window.controller.set_message("두 번째 메시지", "warning")


def test_refresh_updates_summary_and_renders_targets(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([("B", FakeConn())]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        router=FakeRouter("active", "B"),
        sink=FakeSink("B"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window.controller.refresh_now()

    assert "B" in window._headline.text()
    assert window._peer_table.rowCount() == 2
    assert window._peer_table.item(0, 0).text() == "A"
    assert window._peer_table.item(0, 1).text() == "내 PC"
    assert window._peer_table.item(0, 2).text() == get_current_version_label()
    assert window._peer_table.item(1, 0).text() == "B"
    assert window._peer_table.horizontalHeaderItem(1).text() == "최근 연결"
    assert window._peer_table.horizontalHeaderItem(2).text() == "현재 버전"
    assert window._peer_table.horizontalHeaderItem(3).text() == "모니터 배치"


def test_window_title_includes_current_version(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()

    assert window.windowTitle() == f"A | {get_current_version_label()}"


def test_settings_page_uses_internal_scroll_with_fixed_footer(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()

    assert window._settings_page._content_scroll.widget() is not None
    assert window._settings_page._reset_button.parent() is window._settings_page._footer_bar
    assert window._settings_page._save_button.parent() is window._settings_page._footer_bar


def test_outdated_peer_version_is_highlighted_with_tooltip(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry(
            [
                (
                    "B",
                    FakeConn(
                        peer_app_version="0.3.17",
                        peer_compatibility_version="0.3.17",
                    ),
                )
            ]
        ),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window.controller.refresh_now()

    version_item = window._peer_table.item(1, 2)

    assert version_item.text() == "v0.3.17"
    assert version_item.toolTip() == ""
    assert "오래된 버전" in version_item.data(HoverTooltipTableWidget.TOOLTIP_ROLE)
    assert version_item.foreground().color() == QColor("#a55252")
    assert version_item.background().style() == Qt.NoBrush
    assert window._peer_table.item(1, 0).data(HoverTooltipTableWidget.TOOLTIP_ROLE) == ""
    return
    assert "호환되지 않는 버전" in version_item.data(HoverTooltipTableWidget.TOOLTIP_ROLE)
    assert version_item.foreground().color() == QColor("#a55252")
    assert version_item.background().style() == Qt.NoBrush
    assert window._peer_table.item(1, 0).data(HoverTooltipTableWidget.TOOLTIP_ROLE) == ""


def test_unknown_peer_version_is_highlighted_only_on_version_column(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([("B", FakeConn())]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window.controller.refresh_now()

    version_item = window._peer_table.item(1, 2)
    name_item = window._peer_table.item(1, 0)

    assert version_item.text() == "알 수 없음"
    assert version_item.data(HoverTooltipTableWidget.TOOLTIP_ROLE)
    assert version_item.foreground().color() == QColor("#9a6b3d")
    assert version_item.background().style() == Qt.NoBrush
    assert version_item.font().italic() is True
    assert name_item.data(HoverTooltipTableWidget.TOOLTIP_ROLE) == ""
    assert name_item.foreground().color() == QColor(PALETTE["text"])


def test_newer_peer_version_uses_softer_tone_without_remote_update_prompt(qtbot):
    ctx = _layout_ctx()
    coord_client = FakeCoordClient()
    window = StatusWindow(
        ctx,
        FakeRegistry(
            [
                (
                    "B",
                    FakeConn(
                        peer_app_version="0.3.27",
                        peer_compatibility_version="0.3.27",
                    ),
                )
            ]
        ),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=coord_client,
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window.controller.refresh_now()

    version_item = window._peer_table.item(1, 2)

    assert version_item.data(HoverTooltipTableWidget.TOOLTIP_ROLE)
    assert "더 최신 버전" in version_item.data(HoverTooltipTableWidget.TOOLTIP_ROLE)
    assert version_item.foreground().color() == QColor("#60748a")

    window._on_peer_table_cell_clicked(1, 2)

    assert coord_client.remote_updates == []
    assert "더 최신 버전" in window.controller._current_message[0]


def test_connection_tab_removed_from_navigation(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()

    labels = [button.text() for button in window._nav_buttons]

    assert "연결 상태" not in labels
    assert len(labels) == 5


def test_peer_table_does_not_change_shared_selection(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window.controller.refresh_now()
    window._peer_table.selectRow(1)

    assert window._inspector_title.text() == "A PC"
    assert window.controller.selected_node_id == "A"


def test_clicking_remote_version_cell_requests_remote_update(qtbot, monkeypatch):
    ctx = _layout_ctx()
    coord_client = FakeCoordClient()
    window = StatusWindow(
        ctx,
        FakeRegistry([("B", FakeConn(peer_app_version="0.3.17", peer_compatibility_version="0.3.17"))]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=coord_client,
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window.controller.refresh_now()
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)

    window._on_peer_table_cell_clicked(1, 2)

    assert coord_client.remote_updates == ["B"]
    assert "B(회의실)" in window.controller._current_message[0]


def test_peer_table_is_read_only(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()

    assert window._peer_table.editTriggers() == QAbstractItemView.NoEditTriggers
    assert window._peer_table.selectionMode() == QAbstractItemView.NoSelection


def test_offline_peer_keeps_last_known_version_label(qtbot):
    ctx = _layout_ctx()
    registry = FakeRegistry(
        [
            (
                "B",
                FakeConn(
                    peer_app_version="0.3.17",
                    peer_compatibility_version="0.3.17",
                ),
            )
        ]
    )
    window = StatusWindow(
        ctx,
        registry,
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window.controller.refresh_now()

    registry._pairs = []
    window.controller.refresh_now()

    assert window._peer_table.item(1, 2).text() == "v0.3.17"
    assert window._peer_table.item(1, 1).text() == "오프라인"


def test_advanced_runtime_panel_uses_control_authority_label(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()

    label = window._advanced_runtime_layout.itemAtPosition(4, 0).widget()

    assert label.text() == "제어권"


def test_leaving_layout_page_ends_edit_mode(qtbot):
    ctx = _layout_ctx()
    coord_client = FakeCoordClient()
    coord_client._is_editor = True
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=coord_client,
    )
    qtbot.addWidget(window)
    window.controller.stop()

    window._show_page(window.PAGE_LAYOUT)
    window._show_page(window.PAGE_OVERVIEW)

    assert coord_client._is_editor is False


def test_entering_layout_page_triggers_fit_view(qtbot, monkeypatch):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()

    called = {"count": 0}

    def fake_fit():
        called["count"] += 1

    monkeypatch.setattr(window._layout_editor, "fit_view", fake_fit)

    window._show_page(window.PAGE_LAYOUT)

    assert called["count"] == 1


def test_banner_updates_from_message_signal(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window.controller.set_message("테스트 배너", "warning")

    assert window._banner.isHidden() is False
    assert "테스트 배너" in window._banner_label.text()


def test_message_history_toggle_expands_recent_messages(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window.show()
    _seed_message_history(window)

    qtbot.mouseClick(window._message_history_toggle, Qt.LeftButton)
    qtbot.waitUntil(lambda: window._message_history_expanded)

    assert window._message_history_frame.isHidden() is False
    assert window._message_history_toggle.text() == "▴"
    assert window._message_history_list.count() == 2
    assert window._message_history_list.item(0).text().endswith("두 번째 메시지")
    assert window._message_history_frame.maximumHeight() > 0


def test_message_history_toggle_shows_empty_placeholder_when_no_history(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window.show()

    window._render_banner("테스트 배너", "warning")
    window._render_message_history(())

    qtbot.mouseClick(window._message_history_toggle, Qt.LeftButton)
    qtbot.waitUntil(lambda: window._message_history_expanded)

    assert window._message_history_list.count() == 1
    assert window._message_history_list.item(0).text() == "메시지 기록이 없습니다."


def test_message_history_toggle_closes_panel_when_clicked_again(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window.show()
    _seed_message_history(window)
    window._set_message_history_expanded(True, animate=False)

    qtbot.mouseClick(window._message_history_toggle, Qt.LeftButton)
    qtbot.waitUntil(lambda: not window._message_history_expanded)

    assert window._message_history_frame.isHidden() is True
    assert window._message_history_toggle.text() == "▾"
    assert hasattr(window, "_message_history_collapse") is False


def test_message_history_only_closes_after_clicking_outside_banners(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window.show()
    _seed_message_history(window)
    window._render_update_banner(
        {
            "visible": True,
            "title": "새로운 업데이트가 있습니다!",
            "detail": "v0.3.18 버전이 준비되었습니다.",
            "tag_name": "v0.3.18",
        }
    )
    window._set_message_history_expanded(True, animate=False)

    qtbot.mouseClick(window._update_banner_title, Qt.LeftButton)
    qtbot.wait(50)
    assert window._message_history_expanded is True

    qtbot.mouseClick(window._headline, Qt.LeftButton)
    qtbot.waitUntil(lambda: not window._message_history_expanded)

    assert window._message_history_frame.isHidden() is True


def test_update_banner_is_separate_from_message_banner(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()

    window._render_update_banner(
        {
            "visible": True,
            "title": "새로운 업데이트가 있습니다!",
            "detail": "v0.3.18 버전이 준비되었습니다.",
            "tag_name": "v0.3.18",
        }
    )
    window.controller.set_message("테스트 배너", "warning")

    assert window._update_banner.isHidden() is False
    assert "새로운 업데이트가 있습니다!" == window._update_banner_title.text()
    assert "v0.3.18" in window._update_banner_detail.text()
    assert window._banner.isHidden() is False
    assert "테스트 배너" in window._banner_label.text()


def test_remote_update_command_uses_background_flow_when_window_hidden(qtbot, monkeypatch):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window.hide()
    started = []
    notifications = []

    monkeypatch.setattr(
        window._settings_page,
        "start_remote_update",
        lambda *, background: started.append(background),
    )

    class Tray:
        def available(self):
            return True

        def refresh(self):
            return None

        def show_notification(self, message, timeout_ms=2500):
            notifications.append((message, timeout_ms))

    window.attach_tray(Tray())
    window.handle_remote_update_command({})

    assert started == [True]
    assert notifications
    assert "원격 업데이트" in notifications[0][0]


def test_remote_update_command_uses_visible_flow_when_window_is_open(qtbot, monkeypatch):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
        ui_mode="tray",
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window.show()
    started = []

    monkeypatch.setattr(
        window._settings_page,
        "start_remote_update",
        lambda *, background: started.append(background),
    )

    window.handle_remote_update_command({})

    assert started == [False]


def test_advanced_log_filters_support_multi_select(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()

    window._render_advanced(
        {
            "runtime": {
                "self_id": "A",
                "coordinator_id": "A",
                "selected_target": "-",
                "router_state": "-",
                "authorized_controller": "-",
                "connected_peers": "1/1",
                "config_path": "-",
            },
            "logs": (
                type("LogEntry", (), {"timestamp": "2026-04-15 12:00:00", "level": "INFO", "message": "info-log"})(),
                type("LogEntry", (), {"timestamp": "2026-04-15 12:00:01", "level": "WARNING", "message": "warning-log"})(),
            ),
            "busy": False,
        }
    )
    window._show_page(window.PAGE_ADVANCED)
    qtbot.waitUntil(lambda: not window._log_render_in_progress)

    assert window._log_list.count() == 2

    qtbot.mouseClick(window._log_level_buttons["INFO"], Qt.LeftButton)
    qtbot.waitUntil(lambda: not window._log_render_in_progress)

    assert window._log_list.count() == 1
    assert "warning-log" in window._log_list.item(0).text()


def test_advanced_log_area_shows_loading_state_during_async_render(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window.LOG_RENDER_BATCH_SIZE = 1
    window._latest_logs = tuple(
        type(
            "LogEntry",
            (),
            {
                "timestamp": f"2026-04-15 12:00:0{index}",
                "level": "INFO",
                "message": f"log-{index}",
            },
        )()
        for index in range(3)
    )

    window._start_async_log_render()

    assert window._log_loading_label.isHidden() is False
    assert "로그를 불러오는 중입니다" in window._log_loading_label.text()
    qtbot.waitUntil(lambda: not window._log_render_in_progress)
    assert window._log_loading_label.isHidden() is True
    assert window._log_list.count() == 3


def test_update_banner_install_button_uses_settings_page_action(qtbot, monkeypatch):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    called = {"count": 0}

    monkeypatch.setattr(
        window._settings_page,
        "_install_update",
        lambda: called.__setitem__("count", called["count"] + 1),
    )

    window._render_update_banner(
        {
            "visible": True,
            "title": "새로운 업데이트가 있습니다!",
            "detail": "v0.3.18 버전이 준비되었습니다.",
            "tag_name": "v0.3.18",
        }
    )

    qtbot.mouseClick(window._update_banner_button, Qt.LeftButton)

    assert called["count"] == 1


def test_layout_grant_message_reaches_banner_after_refresh(qtbot):
    ctx = _layout_ctx()
    coord_client = FakeCoordClient()
    coord_client._is_editor = False
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=coord_client,
    )
    qtbot.addWidget(window)
    window.controller.stop()

    coord_client._pending = True
    window.controller.refresh_now()
    qtbot.waitUntil(lambda: window._layout_editor._layout_feedback_state.pending is True)
    coord_client._pending = False
    coord_client._is_editor = True
    window.controller.refresh_now()
    qtbot.waitUntil(
        lambda: "편집 권한을 얻었습니다. 레이아웃 편집을 시작합니다." in window._banner_label.text()
    )

    assert "편집 권한을 얻었습니다. 레이아웃 편집을 시작합니다." in window._banner_label.text()


def test_summary_card_tooltip_follows_pointer(monkeypatch, qtbot):
    card = SummaryCard()
    qtbot.addWidget(card)
    calls = []

    monkeypatch.setattr(
        card._hover_tooltip,
        "show_text",
        lambda text, pos: calls.append((text, pos)),
    )

    card.apply(type("Card", (), {"title": "현재 대상", "value": "-", "detail": "설명"})())
    assert card.toolTip() == ""
    card._show_tooltip(card.rect().center())
    card._show_tooltip(card.rect().topLeft())

    assert len(calls) == 2
    assert calls[0][0] == "설명"
    assert calls[0][1] != calls[1][1]


def test_help_dot_tooltip_follows_pointer(monkeypatch, qtbot):
    dot = HelpDot("도움말")
    qtbot.addWidget(dot)
    calls = []

    monkeypatch.setattr(
        dot._hover_tooltip,
        "show_text",
        lambda text, pos: calls.append((text, pos)),
    )
    assert dot.toolTip() == ""

    dot._show_tooltip(dot.rect().center())
    dot._show_tooltip(dot.rect().topLeft())

    assert len(calls) == 2
    assert calls[0][0] == "도움말"
    assert calls[0][1] != calls[1][1]
