"""Tests for runtime/status_window.py."""

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView

from runtime.context import build_runtime_context
from runtime.gui_style import PALETTE
from runtime.app_version import get_current_version_label
from runtime.settings_page import HelpDot
from runtime.status_window import StatusWindow, SummaryCard


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

    def get_layout_edit_denial(self):
        return None


def _layout_ctx():
    config = {
        "nodes": [
            {"name": "A", "ip": "127.0.0.1", "port": 5000},
            {"name": "B", "ip": "127.0.0.1", "port": 5001},
        ],
    }
    return build_runtime_context(config, override_name="A", config_path="config/config.json")


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
    assert "연결된 PC 2 / 2" == window._summary.text()
    assert window._peer_table.rowCount() == 2
    assert window._peer_table.item(0, 0).text() == "A"
    assert window._peer_table.item(0, 2).text() == get_current_version_label()
    assert window._peer_table.item(1, 0).text() == "B"
    assert window._peer_table.horizontalHeaderItem(1).text() == "최근 연결"
    assert window._peer_table.horizontalHeaderItem(2).text() == "현재 버전"


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


def test_settings_page_is_wrapped_in_scroll_area(qtbot):
    ctx = _layout_ctx()
    window = StatusWindow(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(window)
    window.controller.stop()

    assert window._settings_scroll.widget() is window._settings_page


def test_incompatible_peer_version_is_highlighted_with_tooltip(qtbot):
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
    assert "호환되지 않는 버전" in version_item.toolTip()
    assert version_item.foreground().color() == QColor(PALETTE["danger"])


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


def test_peer_selection_syncs_inspector(qtbot):
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

    assert "B" in window._inspector_title.text()


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
