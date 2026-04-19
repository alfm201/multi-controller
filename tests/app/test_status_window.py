"""Tests for app/ui/status_window.py."""

from types import SimpleNamespace

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QWheelEvent
from PySide6.QtWidgets import QApplication, QAbstractItemView, QMessageBox

from app.ui import status_window as status_window_module
from control.state.context import build_runtime_context
from app.ui.gui_style import PALETTE
from app.update.app_version import get_current_version, get_current_version_label
from app.ui.settings_page import HelpDot
from app.ui.status_window import HoverTooltipTableWidget, StatusWindow, SummaryCard


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

    def close(self):
        self.closed = True


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
        self.remote_update_statuses = []
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

    def report_remote_update_status(
        self,
        *,
        target_id,
        requester_id,
        status,
        detail="",
        reason="",
        request_id="",
        event_id="",
        session_id="",
        current_version="",
        latest_version="",
    ):
        self.remote_update_statuses.append(
            (
                target_id,
                requester_id,
                status,
                detail,
                reason,
                request_id,
                event_id,
                session_id,
                current_version,
                latest_version,
            )
        )
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


def _next_version(version: str) -> str:
    parts = [int(part) for part in version.split(".")]
    parts[-1] += 1
    return ".".join(str(part) for part in parts)


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

    assert window._peer_table.rowCount() == 2
    assert window._peer_table.item(0, 0).text() == "A(127.0.0.1)"
    assert window._peer_table.item(0, 1).text() == "내 PC"
    assert window._peer_table.item(0, 2).text() == get_current_version_label()
    assert window._peer_table.item(1, 0).text() == "B(127.0.0.1)"
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

    assert window.windowTitle() == f"127.0.0.1 | {get_current_version_label()}"


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
    window._ensure_page_built(window.PAGE_SETTINGS)

    assert window._settings_page._content_scroll.widget() is not None
    assert window._settings_page._reset_button.parent() is window._settings_page._footer_bar
    assert window._settings_page._save_button.parent() is window._settings_page._footer_bar


def test_nodes_page_is_lazy_built_when_opened(qtbot, monkeypatch):
    ctx = _layout_ctx()
    refresh_calls = []
    original_refresh = status_window_module.NodeManagerPage.refresh

    def wrapped_refresh(self):
        refresh_calls.append("refresh")
        return original_refresh(self)

    monkeypatch.setattr(status_window_module.NodeManagerPage, "refresh", wrapped_refresh)
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()

    assert window._node_manager_page is None

    window._show_page(window.PAGE_NODES)

    assert window._node_manager_page is not None
    assert len(refresh_calls) >= 1


def test_summary_cards_are_reused_when_card_count_changes(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()

    def make_view(count: int):
        cards = tuple(
            SimpleNamespace(
                title=f"title-{index}",
                value=f"value-{index}",
                detail=f"detail-{index}",
                tone="neutral",
            )
            for index in range(count)
        )
        return SimpleNamespace(
            self_ip="127.0.0.1",
            summary_cards=cards,
            monitor_alert=None,
            monitor_alert_tone="neutral",
        )

    window._render_summary(make_view(3))
    first_ids = [id(widget) for widget in window._summary_card_widgets[:3]]

    window._render_summary(make_view(1))
    assert [id(widget) for widget in window._summary_card_widgets[:1]] == first_ids[:1]
    assert window._summary_card_widgets[1].isHidden() is True
    assert window._summary_card_widgets[2].isHidden() is True

    window._render_summary(make_view(3))
    assert [id(widget) for widget in window._summary_card_widgets[:3]] == first_ids
    assert all(not widget.isHidden() for widget in window._summary_card_widgets[:3])


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
    newer_version = _next_version(get_current_version())
    window = StatusWindow(
        ctx,
        FakeRegistry(
            [
                    (
                            "B",
                            FakeConn(
                                peer_app_version=newer_version,
                                peer_compatibility_version=newer_version,
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

    assert window._inspector_title.text() == "A(127.0.0.1) PC"
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
    assert "B(127.0.0.1)" in window.controller._current_message[0]


def test_request_target_uses_name_and_ip_in_message(qtbot):
    ctx = _layout_ctx()
    coord_client = FakeCoordClient()
    window = StatusWindow(
        ctx,
        FakeRegistry([("B", FakeConn())]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=coord_client,
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window.controller.refresh_now()

    window._request_target("B")
    qtbot.waitUntil(lambda: coord_client.requested == [("B", "ui")])

    assert window.controller._current_message == ("B(127.0.0.1) PC로 전환을 요청했습니다.", "accent")


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
    assert window._peer_table.item(1, 1).text() == "0초 전"
    for column in range(window._peer_table.columnCount()):
        assert window._peer_table.item(1, column).foreground().color() == QColor("#7a8496")


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
    qtbot.waitUntil(lambda: "테스트 배너" in window._banner_label.text())

    assert window._banner.isHidden() is False
    assert "테스트 배너" in window._banner_label.text()


def test_banner_stays_visible_with_default_message_when_empty(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()

    window._render_banner("", "neutral")

    assert window._banner.isHidden() is False
    assert window._banner_label.text() == "새로운 알림이 없습니다."


def test_monitor_state_no_longer_uses_passive_banner(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()

    window.controller._current_view = type(
        "View",
        (),
        {"monitor_alert": "諛곗튂 李⑥씠: B", "monitor_alert_tone": "warning"},
    )()

    initial_count = len(window.controller.message_history)
    window._refresh_banner_from_state()
    window._refresh_banner_from_state()

    assert len(window.controller.message_history) == initial_count
    assert window._last_passive_banner_payload is None
    assert window._banner_label.text() == "새로운 알림이 없습니다."


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
    qtbot.waitUntil(lambda: not window._message_history_render_in_progress)

    assert window._message_history_frame.isHidden() is False
    assert window._message_history_toggle.text() == "▴"
    assert window._message_history_list.count() >= 2
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
    qtbot.waitUntil(lambda: not window._message_history_render_in_progress)

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

    qtbot.mouseClick(window._peer_table.viewport(), Qt.LeftButton)
    qtbot.waitUntil(lambda: not window._message_history_expanded)

    assert window._message_history_frame.isHidden() is True


def test_message_history_stays_open_while_clicking_history_text(qtbot):
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
    qtbot.waitUntil(lambda: not window._message_history_render_in_progress)

    item = window._message_history_list.item(0)
    label = window._message_history_list.itemWidget(item)
    assert label is not None

    qtbot.mouseClick(label, Qt.LeftButton)
    qtbot.wait(50)

    assert window._message_history_expanded is True


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
    qtbot.waitUntil(lambda: "테스트 배너" in window._banner_label.text())

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
    window._ensure_page_built(window.PAGE_SETTINGS)

    monkeypatch.setattr(
        window._settings_page,
        "start_remote_update",
        lambda *, background, requester_id=None, request_id=None: started.append(
            (background, requester_id, request_id)
        ),
    )

    class Tray:
        def available(self):
            return True

        def refresh(self):
            return None

        def show_notification(self, message, timeout_ms=2500):
            notifications.append((message, timeout_ms))

    window.attach_tray(Tray())
    window.handle_remote_update_command({"requester_id": "A"})

    assert started == [(True, "A", None)]
    assert notifications == [("원격 업데이트 명령을 받아 업데이트를 시작합니다.", 3500)]
    assert window.controller.message_history[0]["message"] == "원격 업데이트 명령을 받아 업데이트를 시작합니다."
    assert window.controller.message_history[0]["tone"] == "accent"


def test_close_to_tray_records_recent_message_history(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    notifications = []
    refresh_calls = []

    class Tray:
        def available(self):
            return True

        def refresh(self):
            refresh_calls.append("refresh")

        def show_notification(self, message, timeout_ms=2500):
            notifications.append((message, timeout_ms))

    window.attach_tray(Tray())
    before = len(window.controller.message_history)

    window.close()

    assert notifications
    assert refresh_calls == ["refresh"]
    assert len(window.controller.message_history) == before + 1
    assert window.controller.message_history[0]["message"] == "트레이에서 계속 실행 중입니다."


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
    window._ensure_page_built(window.PAGE_SETTINGS)

    monkeypatch.setattr(
        window._settings_page,
        "start_remote_update",
        lambda *, background, requester_id=None, request_id=None: started.append(
            (background, requester_id, request_id)
        ),
    )

    window.handle_remote_update_command({"requester_id": "A"})

    assert started == [(False, "A", None)]


def test_remote_update_installing_status_is_not_shown_to_requester(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()

    window.handle_remote_update_status({"target_id": "B", "status": "installing"})

    assert window._banner_label.text() == "새로운 알림이 없습니다."


def test_remote_update_requested_status_sets_banner_message(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()

    window.handle_remote_update_status({"target_id": "B", "status": "requested"})

    assert window._banner_label.text() == "새로운 알림이 없습니다."


def test_remote_update_downloading_status_is_not_shown_to_requester(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()

    window.handle_remote_update_status({"target_id": "B", "status": "downloading"})

    assert window._banner_label.text() == "새로운 알림이 없습니다."


def test_remote_update_checking_status_is_not_shown_to_requester(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()

    window.handle_remote_update_status(
        {
            "target_id": "B",
            "status": "checking",
            "current_version": "0.3.17",
            "latest_version": "0.3.18",
        }
    )
    assert window._banner_label.text() == "새로운 알림이 없습니다."


def test_remote_update_completed_and_no_update_statuses_use_versioned_messages(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()

    window.handle_remote_update_status(
        {
            "target_id": "B",
            "status": "completed",
            "current_version": "0.3.17",
            "latest_version": "0.3.18",
        }
    )
    qtbot.waitUntil(lambda: "업데이트가 완료되었습니다" in window._banner_label.text())
    assert "v0.3.17 -> v0.3.18" in window._banner_label.text()

    window.handle_remote_update_status(
        {
            "target_id": "B",
            "status": "no_update",
            "latest_version": "0.3.18",
        }
    )
    qtbot.waitUntil(lambda: "이미 최신 버전" in window._banner_label.text())
    assert "v0.3.18" in window._banner_label.text()


def test_remote_update_starting_status_is_not_shown_to_requester(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()

    window.handle_remote_update_status({"target_id": "B", "status": "starting"})

    assert window._banner_label.text() == "새로운 알림이 없습니다."


def test_hidden_requester_shows_tray_notification_for_remote_update_terminal_status(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    notifications = []

    class Tray:
        def available(self):
            return True

        def refresh(self):
            return None

        def show_notification(self, message, timeout_ms=2500):
            notifications.append((message, timeout_ms))

    window.attach_tray(Tray())
    window.hide()

    window.handle_remote_update_status(
        {
            "target_id": "B",
            "status": "completed",
            "current_version": "0.3.17",
            "latest_version": "0.3.18",
        }
    )

    qtbot.waitUntil(lambda: "업데이트가 완료되었습니다" in window._banner_label.text())
    assert notifications == [(window._banner_label.text(), 3500)]


def test_remote_update_busy_status_sets_specific_banner_message(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()

    window.handle_remote_update_status(
        {
            "target_id": "B",
            "status": "failed",
            "reason": "busy",
        }
    )
    qtbot.waitUntil(lambda: "이미 업데이트 진행 중입니다." in window._banner_label.text())

    assert window._banner_label.text() == "B(127.0.0.1) 노드는 이미 업데이트 진행 중입니다."


def test_settings_page_remote_update_status_is_forwarded_by_window(qtbot):
    ctx = _layout_ctx()
    coord_client = FakeCoordClient()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=coord_client,
    )
    qtbot.addWidget(window)
    window.controller.stop()

    window._report_remote_update_status(
        {"target_id": "B", "requester_id": "A", "status": "installing", "detail": ""}
    )

    sent = coord_client.remote_update_statuses[0]
    assert sent[:4] == ("B", "A", "installing", "")
    assert sent[6]


def test_remote_update_status_retries_when_initial_send_fails(qtbot):
    ctx = _layout_ctx()
    coord_client = FakeCoordClient()
    attempts = []

    def flaky_report(
        *,
        target_id,
        requester_id,
        status,
        detail="",
        reason="",
        request_id="",
        event_id="",
        session_id="",
        current_version="",
        latest_version="",
    ):
        attempts.append(
            (
                target_id,
                requester_id,
                status,
                detail,
                reason,
                request_id,
                event_id,
                session_id,
                current_version,
                latest_version,
            )
        )
        return len(attempts) > 1

    coord_client.report_remote_update_status = flaky_report
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=coord_client,
    )
    qtbot.addWidget(window)
    window.controller.stop()

    window._report_remote_update_status(
        {"target_id": "B", "requester_id": "A", "status": "installing", "detail": ""}
    )

    first_attempt = attempts[0]
    assert first_attempt[:4] == ("B", "A", "installing", "")
    assert first_attempt[6]
    qtbot.waitUntil(lambda: len(attempts) == 2)
    assert window._pending_remote_status_payloads == []


def test_remote_update_status_persists_failed_send_payload(qtbot, monkeypatch, tmp_path):
    ctx = _layout_ctx()
    coord_client = FakeCoordClient()
    coord_client.report_remote_update_status = lambda **_: False
    persisted = []

    def fake_write(
        update_root,
        *,
        requester_id,
        target_id,
        status,
        detail="",
        event_id="",
        session_id="",
        current_version="",
        latest_version="",
    ):
        path = tmp_path / "updates" / f"{status}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        persisted.append(
            (
                str(update_root),
                requester_id,
                target_id,
                status,
                detail,
                event_id,
                session_id,
                current_version,
                latest_version,
            )
        )
        return path

    monkeypatch.setattr(status_window_module, "write_remote_update_outcome", fake_write)
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=coord_client,
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window._ensure_page_built(window.PAGE_SETTINGS)
    window._settings_page._update_installer.update_root = tmp_path / "updates"

    window._report_remote_update_status(
        {"target_id": "B", "requester_id": "A", "status": "downloading", "detail": ""}
    )

    assert persisted[0][:5] == (str(tmp_path / "updates"), "A", "B", "downloading", "")
    assert persisted[0][5]
    pending = window._pending_remote_status_payloads[0]
    assert pending["target_id"] == "B"
    assert pending["requester_id"] == "A"
    assert pending["status"] == "downloading"
    assert pending["event_id"]


def test_remote_update_status_persists_before_successful_send_and_clears_file(qtbot, tmp_path):
    ctx = _layout_ctx()
    coord_client = FakeCoordClient()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=coord_client,
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window._ensure_page_built(window.PAGE_SETTINGS)
    window._settings_page._update_installer.update_root = tmp_path / "updates"

    window._report_remote_update_status(
        {"target_id": "B", "requester_id": "A", "status": "installing", "detail": ""}
    )

    outcome_dir = tmp_path / "updates" / "state"
    assert outcome_dir.exists() is True
    assert list(outcome_dir.glob("remote-update-*.json")) == []


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
    assert all(button.isChecked() is False for button in window._log_level_buttons.values())

    qtbot.mouseClick(window._log_level_buttons["INFO"], Qt.LeftButton)
    qtbot.waitUntil(lambda: not window._log_render_in_progress)

    assert window._log_list.count() == 1
    assert "info-log" in window._log_list.item(0).text()
    assert window._open_logs_button.toolTip() == "로그 폴더 열기"


def test_advanced_log_area_shows_loading_state_in_overlay_during_async_render(qtbot):
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

    assert window._log_loading_overlay.isHidden() is False
    assert "로그를 불러오는 중입니다" in window._log_loading_label.text()
    qtbot.waitUntil(lambda: not window._log_render_in_progress)
    assert window._log_loading_overlay.isHidden() is True
    assert window._log_list.count() == 3


def test_advanced_logs_refresh_in_real_time(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()

    first_log = type("LogEntry", (), {"timestamp": "2026-04-15 12:00:00", "level": "INFO", "message": "first-log"})()
    second_log = type("LogEntry", (), {"timestamp": "2026-04-15 12:00:01", "level": "INFO", "message": "second-log"})()

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
            "logs": (first_log,),
            "busy": False,
        }
    )
    window._show_page(window.PAGE_ADVANCED)
    qtbot.waitUntil(lambda: not window._log_render_in_progress)

    assert window._log_list.count() == 1
    assert "first-log" in window._log_list.item(0).text()

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
            "logs": (second_log, first_log),
            "busy": False,
        }
    )

    qtbot.waitUntil(lambda: not window._log_render_in_progress)

    assert window._log_list.count() == 2
    assert "second-log" in window._log_list.item(1).text()


def test_advanced_log_loading_overlay_does_not_record_message_history(qtbot):
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
    before = len(window.controller.message_history)
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
        for index in range(2)
    )

    window._start_async_log_render()
    qtbot.waitUntil(lambda: not window._log_render_in_progress)

    assert len(window.controller.message_history) == before


def test_selectable_list_items_reserve_space_for_vertical_scrollbar(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()

    text = "scrollbar-width-check"
    window._append_selectable_list_item(
        window._log_list,
        text,
        QColor(PALETTE["text"]),
        selectable=True,
    )
    item = window._log_list.item(0)
    expected_min_width = (
        window._log_list.fontMetrics().horizontalAdvance(text)
        + window._log_list.verticalScrollBar().sizeHint().width()
        + 20
    )

    assert item.sizeHint().width() >= expected_min_width


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
    window._ensure_page_built(window.PAGE_SETTINGS)

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


def test_update_banner_announcement_uses_common_stage_metadata(qtbot, monkeypatch):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    tray_messages = []
    recorded = []
    window._status_tray = type("Tray", (), {"available": lambda self: False})()

    monkeypatch.setattr(
        window._status_tray,
        "show_notification",
        lambda message, timeout_ms=0: tray_messages.append((message, timeout_ms)),
        raising=False,
    )
    monkeypatch.setattr(
        window.controller,
        "publish_message",
        lambda message, tone, *, show_banner=True, record_history=True: recorded.append(
            (message, tone, show_banner, record_history)
        ),
    )

    window._render_update_banner(
        {
            "visible": True,
            "stage": "update_available",
            "target_kind": "self",
            "title": "새 업데이트 v0.3.18이 준비되었습니다!",
            "detail": "설치 버튼을 눌러 새 버전 준비를 시작할 수 있습니다.",
            "tag_name": "v0.3.18",
        }
    )

    assert tray_messages == [("v0.3.18 업데이트가 준비되었습니다.", 3500)]
    assert recorded == [("v0.3.18 업데이트가 준비되었습니다.", "accent", False, True)]


def test_auto_update_banner_does_not_trigger_tray_announcement(qtbot, monkeypatch):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    tray_messages = []
    recorded = []
    window._status_tray = type("Tray", (), {"available": lambda self: False})()

    monkeypatch.setattr(
        window._status_tray,
        "show_notification",
        lambda message, timeout_ms=0: tray_messages.append((message, timeout_ms)),
        raising=False,
    )
    monkeypatch.setattr(
        window.controller,
        "publish_message",
        lambda message, tone, *, show_banner=True, record_history=True: recorded.append(
            (message, tone, show_banner, record_history)
        ),
    )

    window._render_update_banner(
        {
            "visible": True,
            "stage": "update_available",
            "target_kind": "self",
            "origin": "auto",
            "title": "새 업데이트 v0.3.18이 준비되었습니다!",
            "detail": "설치 버튼을 눌러 새 버전 준비를 시작할 수 있습니다.",
            "tag_name": "v0.3.18",
        }
    )

    assert tray_messages == []
    assert recorded == []


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


def test_advanced_log_list_keeps_latest_entry_at_bottom(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window.LOG_RENDER_BATCH_SIZE = 8
    window._latest_logs = (
        type("LogEntry", (), {"timestamp": "2026-04-15 12:00:03", "level": "INFO", "message": "latest"})(),
        type("LogEntry", (), {"timestamp": "2026-04-15 12:00:02", "level": "INFO", "message": "middle"})(),
        type("LogEntry", (), {"timestamp": "2026-04-15 12:00:01", "level": "INFO", "message": "oldest"})(),
    )

    window._start_async_log_render()

    qtbot.waitUntil(lambda: not window._log_render_in_progress)

    assert "oldest" in window._log_list.item(0).text()
    assert "latest" in window._log_list.item(window._log_list.count() - 1).text()


def test_overview_reconnect_button_reloads_config_and_closes_connections(qtbot):
    class FakeConfigReloader:
        def __init__(self):
            self.reload_calls = 0

        def reload(self):
            self.reload_calls += 1

        def save_nodes(self, *args, **kwargs):
            return None

        def restore_latest_backup(self):
            return None

        def get_latest_backup_path(self):
            return None

    ctx = _layout_ctx()
    conn = FakeConn()
    reloader = FakeConfigReloader()
    window = StatusWindow(
        ctx,
        FakeRegistry([("B", conn)]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
        config_reloader=reloader,
    )
    qtbot.addWidget(window)
    window.controller.stop()

    qtbot.mouseClick(window._reconnect_peers_button, Qt.LeftButton)

    assert conn.closed is True
    assert reloader.reload_calls == 1


def test_advanced_log_updates_in_real_time_after_new_entries_arrive(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window._current_page = window.PAGE_ADVANCED
    old = type("LogEntry", (), {"timestamp": "2026-04-15 12:00:01", "level": "INFO", "message": "old"})()
    new = type("LogEntry", (), {"timestamp": "2026-04-15 12:00:02", "level": "INFO", "message": "new"})()
    window._latest_logs = (old,)
    window._start_async_log_render()
    qtbot.waitUntil(lambda: not window._log_render_in_progress)

    window._render_advanced(
        {
            "runtime": {},
            "logs": (new, old),
            "busy": False,
        }
    )

    qtbot.waitUntil(lambda: not window._log_render_in_progress)

    assert window._log_list.count() == 2
    assert "new" in window._log_list.item(1).text()
    assert window._log_list.verticalScrollBar().value() == window._log_list.verticalScrollBar().maximum()


def test_advanced_log_filters_show_all_when_none_selected(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    first = type("LogEntry", (), {"timestamp": "2026-04-15 12:00:00", "level": "INFO", "message": "first"})()
    second = type("LogEntry", (), {"timestamp": "2026-04-15 12:00:01", "level": "WARNING", "message": "second"})()
    window._render_advanced({"runtime": {}, "logs": (second, first), "busy": False})
    window._show_page(window.PAGE_ADVANCED)
    qtbot.waitUntil(lambda: not window._log_render_in_progress)

    assert window._log_list.count() == 2
    assert "first" in window._log_list.item(0).text()
    assert "second" in window._log_list.item(1).text()


def test_advanced_log_updates_pause_while_text_is_selected(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    old = type("LogEntry", (), {"timestamp": "2026-04-15 12:00:01", "level": "INFO", "message": "old"})()
    new = type("LogEntry", (), {"timestamp": "2026-04-15 12:00:02", "level": "INFO", "message": "new"})()
    window._render_advanced({"runtime": {}, "logs": (old,), "busy": False})
    window._show_page(window.PAGE_ADVANCED)
    qtbot.waitUntil(lambda: not window._log_render_in_progress)
    window._log_list.selectAll()

    assert window._log_list.has_active_selection() is True

    window._render_advanced({"runtime": {}, "logs": (new, old), "busy": False})
    qtbot.wait(50)

    assert window._log_list.count() == 1
    assert "new" not in window._log_list.toPlainText()
    assert window._log_list_dirty is True

    cursor = window._log_list.textCursor()
    cursor.clearSelection()
    window._log_list.setTextCursor(cursor)
    qtbot.waitUntil(lambda: not window._log_render_in_progress)

    assert window._log_list.count() == 2
    assert "new" in window._log_list.item(1).text()


def test_open_logs_button_opens_runtime_log_directory(qtbot, monkeypatch, tmp_path):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    opened = []

    monkeypatch.setattr(window, "_runtime_log_dir", lambda _path: tmp_path)
    monkeypatch.setattr(status_window_module.os, "startfile", lambda path: opened.append(path))

    qtbot.mouseClick(window._open_logs_button, Qt.LeftButton)

    assert opened == [str(tmp_path)]


def test_advanced_log_incremental_update_preserves_scroll_when_not_at_bottom(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window._current_page = window.PAGE_ADVANCED
    entries = tuple(
        type("LogEntry", (), {"timestamp": f"2026-04-15 12:00:{index:02d}", "level": "INFO", "message": f"log-{index}"})()
        for index in range(12)
    )
    window._latest_logs = tuple(reversed(entries))
    window._start_async_log_render()
    qtbot.waitUntil(lambda: not window._log_render_in_progress)
    window._log_list.verticalScrollBar().setValue(0)

    latest = type("LogEntry", (), {"timestamp": "2026-04-15 12:01:00", "level": "INFO", "message": "latest"})()
    window._latest_logs = (latest, *tuple(reversed(entries)))
    window._render_advanced(
        {
            "runtime": {},
            "logs": (latest, *tuple(reversed(entries))),
            "busy": False,
        }
    )

    assert window._log_list.verticalScrollBar().value() == 0
    last_widget = window._log_list.itemWidget(window._log_list.item(window._log_list.count() - 1))
    assert last_widget is not None
    assert bool(last_widget.textInteractionFlags() & Qt.TextSelectableByMouse)


def test_advanced_log_incremental_update_preserves_horizontal_scroll(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window.resize(320, 740)
    window._current_page = window.PAGE_ADVANCED
    entries = tuple(
        type(
            "LogEntry",
            (),
            {
                "timestamp": f"2026-04-15 12:00:{index:02d}",
                "level": "INFO",
                "message": f"log-{index}-" + ("x" * 160),
            },
        )()
        for index in range(6)
    )
    window._latest_logs = tuple(reversed(entries))
    window._start_async_log_render()
    qtbot.waitUntil(lambda: not window._log_render_in_progress)
    horizontal = window._log_list.horizontalScrollBar()
    horizontal.setValue(horizontal.maximum())
    original_value = horizontal.value()
    window._log_list.verticalScrollBar().setValue(0)

    latest = type(
        "LogEntry",
        (),
        {"timestamp": "2026-04-15 12:01:00", "level": "INFO", "message": "latest-" + ("y" * 160)},
    )()
    window._render_advanced({"runtime": {}, "logs": (latest, *tuple(reversed(entries))), "busy": False})

    assert window._log_list.verticalScrollBar().value() == 0
    assert horizontal.value() == original_value


def test_shift_wheel_scrolls_horizontal_in_log_and_message_views(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()
    window.resize(320, 740)

    long_entry = type(
        "LogEntry",
        (),
        {"timestamp": "2026-04-15 12:00:00", "level": "INFO", "message": "wide-" + ("x" * 200)},
    )()
    window._render_advanced({"runtime": {}, "logs": (long_entry,), "busy": False})
    window._show_page(window.PAGE_ADVANCED)
    qtbot.waitUntil(lambda: not window._log_render_in_progress)

    _seed_message_history(window)
    window._set_message_history_expanded(True, animate=False)
    qtbot.waitUntil(lambda: not window._message_history_render_in_progress)
    window._message_history_list.set_entries(
        [("[2026-04-15 12:00:00] " + ("wide-message-" * 24), QColor(PALETTE["text"]))]
    )

    for text_view in (window._log_list, window._message_history_list):
        scrollbar = text_view.horizontalScrollBar()
        assert scrollbar.maximum() > 0
        start_value = scrollbar.value()
        wheel = QWheelEvent(
            QPointF(8, 8),
            QPointF(8, 8),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.NoButton,
            Qt.ShiftModifier,
            Qt.ScrollUpdate,
            False,
        )
        QApplication.sendEvent(text_view.viewport(), wheel)
        assert scrollbar.value() != start_value


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
