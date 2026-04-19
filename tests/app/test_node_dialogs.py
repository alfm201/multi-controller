"""Tests for app/ui/node_dialogs.py."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QStyle, QStyleOptionViewItem

from app.ui import node_dialogs as node_dialogs_module
from control.state.context import NodeInfo, build_runtime_context
from app.ui.node_dialogs import NodeEditorDialog, NodeManagerPage


def _ctx():
    config = {
        "nodes": [
            {"name": "A", "ip": "127.0.0.1", "port": 5000},
            {"name": "B", "ip": "127.0.0.1", "port": 5001, "note": "기존"},
            {"name": "C", "ip": "127.0.0.1", "port": 5002},
        ],
    }
    return build_runtime_context(config, override_name="A", config_path="config/config.json")


def _ctx_with_explicit_ids():
    config = {
        "nodes": [
            {"node_id": "node-a", "name": "A", "ip": "127.0.0.1", "port": 5000},
            {"node_id": "node-b", "name": "B", "ip": "127.0.0.1", "port": 5001, "note": "기존"},
        ],
    }
    return build_runtime_context(config, override_name="node-a", config_path="config/config.json")


def test_node_manager_creates_node_from_modal_payload(qtbot, monkeypatch):
    ctx = _ctx()
    saved = []
    page = NodeManagerPage(ctx, save_nodes=lambda nodes, **kwargs: saved.append((nodes, kwargs)))
    qtbot.addWidget(page)
    monkeypatch.setattr(
        "app.ui.node_dialogs.generate_unique_node_id",
        lambda _nodes: "11111111-2222-3333-4444-555555555555",
    )

    monkeypatch.setattr(
        page,
        "_open_node_editor",
        lambda **kwargs: {"name": "D", "ip": "127.0.0.1", "port": 5000},
    )

    page._create_node()

    assert saved
    assert [node["node_id"] for node in saved[0][0]] == [
        "A",
        "B",
        "C",
        "11111111-2222-3333-4444-555555555555",
    ]
    assert [node["name"] for node in saved[0][0]] == ["A", "B", "C", "D"]
    assert saved[0][1]["apply_runtime"] is True


def test_node_manager_rejects_name_that_conflicts_with_existing_node_id(qtbot):
    ctx = _ctx_with_explicit_ids()
    page = NodeManagerPage(ctx, save_nodes=lambda nodes, **kwargs: None)
    qtbot.addWidget(page)

    with pytest.raises(ValueError, match="식별자와 충돌"):
        page._build_nodes_payload(
            None,
            {"node_id": "node-c", "name": "node-b", "ip": "127.0.0.1", "port": 5002, "note": ""},
        )


def test_node_manager_requires_single_checked_node_for_edit(qtbot, monkeypatch):
    ctx = _ctx()
    page = NodeManagerPage(ctx, save_nodes=lambda nodes, **kwargs: None)
    qtbot.addWidget(page)
    messages = []

    monkeypatch.setattr(
        page,
        "_show_quiet_notice",
        lambda title, text: messages.append((title, text)) or QMessageBox.Ok,
    )

    page._table.item(1, 0).setCheckState(Qt.Checked)
    page._table.item(2, 0).setCheckState(Qt.Checked)
    page._edit_selected()

    assert messages
    assert "하나의 노드" in messages[-1][1]


def test_node_manager_table_shows_priority_column_and_hides_port_column(qtbot):
    ctx = _ctx()
    page = NodeManagerPage(ctx, save_nodes=lambda nodes, **kwargs: None)
    qtbot.addWidget(page)

    headers = [
        page._table.horizontalHeaderItem(index).text()
        for index in range(page._table.columnCount())
    ]

    assert headers == ["선택", "이름", "IP", "우선순위", "비고"]
    assert page._table.columnCount() == 5
    assert page._table.item(0, 1).text() == "A"
    assert page._table.item(0, 3).text() == "후순위"


def test_node_manager_table_applies_header_and_cell_tooltips(qtbot):
    ctx = _ctx()
    page = NodeManagerPage(ctx, save_nodes=lambda nodes, **kwargs: None)
    qtbot.addWidget(page)

    header = page._table.horizontalHeader()
    expected_tooltip = "숫자가 낮을수록 코디네이터로 먼저 선발됩니다. 비우거나 0이면 가장 후순위입니다."

    assert isinstance(header, node_dialogs_module.NodeTableHeaderView)
    assert page._table.horizontalHeaderItem(3).toolTip() == ""
    assert header._tooltips[3] == expected_tooltip
    assert page._table.item(0, 3).toolTip() == ""
    assert page._table.item(0, 3).data(page._table.TOOLTIP_ROLE) == expected_tooltip


def test_node_editor_blank_priority_is_saved_as_last_priority(qtbot):
    dialog = NodeEditorDialog(
        title="노드 수정",
        payload={"name": "A", "ip": "127.0.0.1", "note": "", "priority": 0},
    )
    qtbot.addWidget(dialog)
    dialog.show()

    assert dialog._priority.text() == ""
    dialog._priority.setText("")

    payload = dialog._collect_payload()

    assert payload["priority"] == 0


def test_node_editor_rejects_invalid_ipv4_and_marks_segment_invalid(qtbot):
    dialog = NodeEditorDialog(
        title="노드 수정",
        payload={"name": "A", "ip": "127.0.0.1", "note": "", "priority": 0},
    )
    qtbot.addWidget(dialog)
    dialog.show()

    dialog._ip.setText("256.0.0.1")

    with pytest.raises(ValueError, match="IPv4"):
        dialog._collect_payload()

    assert dialog._ip._segments[0].property("invalid") is True


def test_ipv4_input_auto_advances_and_dot_moves_next_segment(qtbot):
    dialog = NodeEditorDialog(
        title="노드 추가",
        payload={"name": "A", "ip": "", "note": "", "priority": 0},
    )
    qtbot.addWidget(dialog)
    dialog.show()

    first, second, third, _fourth = dialog._ip._segments
    first.setFocus()
    qtbot.keyClicks(first, "192")

    qtbot.waitUntil(lambda: second.hasFocus() is True)

    qtbot.keyClick(second, Qt.Key_Period)

    qtbot.waitUntil(lambda: third.hasFocus() is True)


def test_ipv4_input_numpad_decimal_moves_next_segment(qtbot):
    dialog = NodeEditorDialog(
        title="노드 추가",
        payload={"name": "A", "ip": "", "note": "", "priority": 0},
    )
    qtbot.addWidget(dialog)
    dialog.show()

    first, second, _third, _fourth = dialog._ip._segments
    first.setFocus()
    qtbot.keyClicks(first, "10")
    qtbot.keyClick(first, Qt.Key_Period, modifier=Qt.KeypadModifier)

    qtbot.waitUntil(lambda: second.hasFocus() is True)


def test_ipv4_input_backspace_moves_to_previous_segment(qtbot):
    dialog = NodeEditorDialog(
        title="노드 추가",
        payload={"name": "A", "ip": "192.168.0.1", "note": "", "priority": 0},
    )
    qtbot.addWidget(dialog)
    dialog.show()

    second = dialog._ip._segments[1]
    third = dialog._ip._segments[2]
    third.clear()
    third.setFocus()
    qtbot.keyClick(third, Qt.Key_Backspace)

    qtbot.waitUntil(lambda: second.hasFocus() is True)


def test_ipv4_input_delete_removes_digit(qtbot):
    dialog = NodeEditorDialog(
        title="노드 추가",
        payload={"name": "A", "ip": "192.168.0.1", "note": "", "priority": 0},
    )
    qtbot.addWidget(dialog)
    dialog.show()

    second = dialog._ip._segments[1]
    second.setFocus()
    second.setCursorPosition(0)
    qtbot.keyClick(second, Qt.Key_Delete)

    assert second.text() == "68"


def test_ipv4_input_arrow_keys_move_between_segments(qtbot):
    dialog = NodeEditorDialog(
        title="노드 추가",
        payload={"name": "A", "ip": "192.168.0.1", "note": "", "priority": 0},
    )
    qtbot.addWidget(dialog)
    dialog.show()

    first, second, third, _fourth = dialog._ip._segments
    second.setFocus()
    second.setCursorPosition(0)
    qtbot.keyClick(second, Qt.Key_Left)
    qtbot.waitUntil(lambda: first.hasFocus() is True)

    second.setFocus()
    second.setCursorPosition(len(second.text()))
    qtbot.keyClick(second, Qt.Key_Right)
    qtbot.waitUntil(lambda: third.hasFocus() is True)


def test_ipv4_input_supports_select_all_and_full_paste(qtbot):
    dialog = NodeEditorDialog(
        title="노드 추가",
        payload={"name": "A", "ip": "1.2.3.4", "note": "", "priority": 0},
    )
    qtbot.addWidget(dialog)
    dialog.show()

    second = dialog._ip._segments[1]
    second.setFocus()
    qtbot.keyClick(second, "A", modifier=Qt.ControlModifier)
    QApplication.clipboard().setText("10.20.30.40")
    qtbot.keyClick(second, "V", modifier=Qt.ControlModifier)

    assert dialog._ip.normalized_text() == "10.20.30.40"


def test_ipv4_input_select_all_from_later_segment_selects_all_segments(qtbot):
    dialog = NodeEditorDialog(
        title="노드 추가",
        payload={"name": "A", "ip": "1.2.3.4", "note": "", "priority": 0},
    )
    qtbot.addWidget(dialog)
    dialog.show()

    third = dialog._ip._segments[2]
    third.setFocus()
    qtbot.keyClick(third, "A", modifier=Qt.ControlModifier)

    assert all(segment.selectedText() == segment.text() for segment in dialog._ip._segments)


def test_ipv4_input_clears_full_selection_when_focus_moves(qtbot):
    dialog = NodeEditorDialog(
        title="노드 추가",
        payload={"name": "A", "ip": "1.2.3.4", "note": "", "priority": 0},
    )
    qtbot.addWidget(dialog)
    dialog.show()

    second = dialog._ip._segments[1]
    second.setFocus()
    qtbot.keyClick(second, "A", modifier=Qt.ControlModifier)
    assert all(segment.selectedText() for segment in dialog._ip._segments)

    dialog._note.setFocus()
    qtbot.waitUntil(lambda: not any(segment.selectedText() for segment in dialog._ip._segments))


def test_ipv4_input_copy_uses_dotted_ipv4_text(qtbot):
    dialog = NodeEditorDialog(
        title="노드 추가",
        payload={"name": "A", "ip": "010.020.030.040", "note": "", "priority": 0},
    )
    qtbot.addWidget(dialog)
    dialog.show()

    second = dialog._ip._segments[1]
    second.setFocus()
    qtbot.keyClick(second, "A", modifier=Qt.ControlModifier)
    qtbot.keyClick(second, "C", modifier=Qt.ControlModifier)

    assert QApplication.clipboard().text() == "10.20.30.40"


def test_ipv4_input_blocks_non_digit_characters(qtbot):
    dialog = NodeEditorDialog(
        title="노드 추가",
        payload={"name": "A", "ip": "", "note": "", "priority": 0},
    )
    qtbot.addWidget(dialog)
    dialog.show()

    first = dialog._ip._segments[0]
    first.setFocus()
    qtbot.keyClicks(first, "ab!1")

    assert first.text() == "1"


def test_group_join_dialog_uses_ipv4_input_and_normalizes_address(qtbot):
    dialog = node_dialogs_module.GroupJoinDialog()
    qtbot.addWidget(dialog)
    dialog.show()

    dialog._ip.setText("010.020.030.040")

    assert dialog.target_ip() == "10.20.30.40"


def test_node_manager_clicking_selection_column_toggles_checkbox(qtbot):
    ctx = _ctx()
    page = NodeManagerPage(ctx, save_nodes=lambda nodes, **kwargs: None)
    qtbot.addWidget(page)

    assert page._table.item(1, 0).checkState() == Qt.Unchecked

    page._on_cell_clicked(1, 0)
    assert page._table.item(1, 0).checkState() == Qt.Checked

    page._on_cell_clicked(1, 0)
    assert page._table.item(1, 0).checkState() == Qt.Unchecked


def test_node_manager_selection_cells_are_user_checkable(qtbot):
    ctx = _ctx()
    page = NodeManagerPage(ctx, save_nodes=lambda nodes, **kwargs: None)
    qtbot.addWidget(page)

    item = page._table.item(0, 0)

    assert item is not None
    assert bool(item.flags() & Qt.ItemIsUserCheckable)


def test_node_manager_checkbox_delegate_renders_checked_state_differently(qtbot):
    ctx = _ctx()
    page = NodeManagerPage(ctx, save_nodes=lambda nodes, **kwargs: None)
    qtbot.addWidget(page)
    delegate = page._table.itemDelegateForColumn(0)
    checked_item = page._table.item(1, 0)
    assert checked_item is not None

    def render_image(check_state):
        checked_item.setCheckState(check_state)
        image = QImage(28, 28, QImage.Format_ARGB32_Premultiplied)
        image.fill(0)
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 28, 28)
        option.state = QStyle.State_Enabled | QStyle.State_Active
        option.palette = page._table.palette()
        painter = QPainter(image)
        delegate.paint(painter, option, page._table.model().index(1, 0))
        painter.end()
        return image

    unchecked = render_image(Qt.Unchecked)
    checked = render_image(Qt.Checked)

    difference_count = 0
    for y in range(unchecked.height()):
        for x in range(unchecked.width()):
            if unchecked.pixel(x, y) != checked.pixel(x, y):
                difference_count += 1

    assert difference_count > 20


def test_node_manager_deletes_multiple_checked_nodes(qtbot, monkeypatch):
    ctx = _ctx()
    saved = []
    page = NodeManagerPage(ctx, save_nodes=lambda nodes, **kwargs: saved.append((nodes, kwargs)))
    qtbot.addWidget(page)

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)

    page._table.item(1, 0).setCheckState(Qt.Checked)
    page._table.item(2, 0).setCheckState(Qt.Checked)
    page._delete_selected()

    assert saved
    assert [node["name"] for node in saved[0][0]] == ["A"]


def test_node_manager_requests_node_list_sync_after_edit(qtbot, monkeypatch):
    class FakeCoordClient:
        def __init__(self):
            self.calls = []

        def request_node_list_update(self, nodes, rename_map=None):
            self.calls.append((nodes, rename_map))
            return True

    ctx = _ctx()
    saved = []
    coord_client = FakeCoordClient()
    page = NodeManagerPage(
        ctx,
        save_nodes=lambda nodes, **kwargs: saved.append((nodes, kwargs)),
        coord_client=coord_client,
    )
    qtbot.addWidget(page)

    monkeypatch.setattr(
        page,
        "_open_node_editor",
        lambda **kwargs: {
            "node_id": "B",
            "name": "회의실 PC",
            "ip": "127.0.0.1",
            "port": 5000,
            "note": "편의실",
            "priority": 3,
        },
    )

    page._edit_node_by_id("B")

    assert saved
    assert coord_client.calls
    assert coord_client.calls[0][0][1]["node_id"] == "B"
    assert coord_client.calls[0][0][1]["name"] == "회의실 PC"
    assert coord_client.calls[0][0][1]["note"] == "편의실"
    assert coord_client.calls[0][0][1]["priority"] == 3
    assert coord_client.calls[0][1] == {}


def test_node_manager_skips_sync_warning_when_only_local_node_is_online(qtbot, monkeypatch):
    class FakeCoordClient:
        def __init__(self):
            self.calls = []
            self.registry = SimpleNamespace(all=lambda: [])

        def request_node_list_update(self, nodes, rename_map=None):
            self.calls.append((nodes, rename_map))
            return False

    ctx = _ctx()
    saved = []
    coord_client = FakeCoordClient()
    page = NodeManagerPage(
        ctx,
        save_nodes=lambda nodes, **kwargs: saved.append((nodes, kwargs)),
        coord_client=coord_client,
    )
    qtbot.addWidget(page)
    messages = []
    page.messageRequested.connect(lambda text, tone: messages.append((text, tone)))

    monkeypatch.setattr(
        page,
        "_open_node_editor",
        lambda **kwargs: {
            "node_id": "B",
            "name": "회의실 PC",
            "ip": "127.0.0.1",
            "port": 5000,
            "note": "메모",
            "priority": 3,
        },
    )

    page._edit_node_by_id("B")

    assert saved
    assert coord_client.calls
    assert messages == [("노드 목록을 저장했습니다.", "success")]


def test_node_manager_group_join_fetches_nodes_and_syncs(qtbot, monkeypatch):
    class FakeCoordClient:
        def __init__(self):
            self.calls = []

        def request_node_list_update(self, nodes, rename_map=None):
            self.calls.append((nodes, rename_map))
            return True

    ctx = _ctx()
    saved = []
    coord_client = FakeCoordClient()
    page = NodeManagerPage(
        ctx,
        save_nodes=lambda nodes, **kwargs: saved.append((nodes, kwargs)),
        coord_client=coord_client,
    )
    qtbot.addWidget(page)
    messages = []
    page.messageRequested.connect(lambda text, tone: messages.append((text, tone)))

    monkeypatch.setattr(node_dialogs_module.GroupJoinDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(node_dialogs_module.GroupJoinDialog, "target_ip", lambda self: "192.168.0.20")
    monkeypatch.setattr(
        page,
        "_start_group_join_worker",
        lambda target_ip: page._handle_group_join_payload(
            {
                "detail": "노드 그룹에 참여할 수 있도록 현재 목록을 동기화했습니다.",
                "nodes": [
                    {"node_id": "A", "name": "A", "ip": "127.0.0.1", "port": 5000},
                    {"node_id": "B", "name": "B", "ip": "127.0.0.1", "port": 5001, "note": "기존"},
                    {"node_id": "D", "name": "D", "ip": "192.168.0.20", "port": 45873},
                ],
            },
            target_ip,
        ),
    )

    page._join_group()

    assert saved
    assert [node["node_id"] for node in saved[0][0]] == ["A", "B", "D"]
    assert coord_client.calls
    assert coord_client.calls[0][0][-1]["node_id"] == "D"
    assert messages[0][1] == "accent"
    assert messages[-1][0] == "노드 그룹에 참여했습니다."

def test_node_manager_warns_when_node_list_update_is_rejected(qtbot):
    class FakeCoordClient:
        def __init__(self):
            self.listener = None

        def add_node_list_change_listener(self, listener):
            self.listener = listener

    ctx = _ctx()
    coord_client = FakeCoordClient()
    page = NodeManagerPage(
        ctx,
        save_nodes=lambda nodes, **kwargs: None,
        coord_client=coord_client,
    )
    qtbot.addWidget(page)
    messages = []
    page.messageRequested.connect(lambda text, tone: messages.append((text, tone)))

    assert coord_client.listener is not None
    coord_client.listener({"reject_reason": "stale_revision"})

    assert messages == [
        (
            "다른 PC에서 먼저 노드 목록을 변경해 최신 상태로 다시 동기화했습니다. 변경 내용을 확인한 뒤 다시 시도해 주세요.",
            "warning",
        )
    ]


def test_node_manager_warns_when_node_list_update_times_out(qtbot):
    class FakeCoordClient:
        def __init__(self):
            self.listener = None
            self.registry = SimpleNamespace(all=lambda: [("B", SimpleNamespace(closed=False))])

        def add_node_list_change_listener(self, listener):
            self.listener = listener

    ctx = _ctx()
    coord_client = FakeCoordClient()
    page = NodeManagerPage(
        ctx,
        save_nodes=lambda nodes, **kwargs: None,
        coord_client=coord_client,
    )
    qtbot.addWidget(page)
    messages = []
    page.messageRequested.connect(lambda text, tone: messages.append((text, tone)))

    assert coord_client.listener is not None
    coord_client.listener({"reject_reason": "timeout"})

    assert messages == [
        (
            "노드 목록 변경 요청이 시간 안에 확인되지 않았습니다. 변경 내용을 확인한 뒤 다시 시도해 주세요.",
            "warning",
        )
    ]


def test_node_manager_group_join_applies_shared_layout_snapshot(qtbot):
    class FakeCoordClient:
        def request_node_list_update(self, nodes, rename_map=None):
            return True

    ctx = _ctx()
    saved = []
    applied = []

    def save_nodes(nodes, **kwargs):
        saved.append((nodes, kwargs))
        ctx.replace_nodes([NodeInfo.from_dict(node) for node in nodes])

    page = NodeManagerPage(
        ctx,
        save_nodes=save_nodes,
        apply_layout=lambda layout, persist=True: applied.append((layout, persist)),
        coord_client=FakeCoordClient(),
    )
    qtbot.addWidget(page)

    page._handle_group_join_payload(
        {
            "detail": "?꾩옱 ?몃뱶 洹몃９ ?뺣낫瑜??꾨떖?덉뒿?덈떎.",
            "nodes": [
                {"node_id": "A", "name": "A", "ip": "127.0.0.1", "port": 5000},
                {"node_id": "B", "name": "B", "ip": "127.0.0.1", "port": 5001, "note": "湲곗〈"},
                {"node_id": "D", "name": "D", "ip": "192.168.0.20", "port": 45873},
            ],
            "layout": {
                "nodes": {
                    "A": {"x": 0, "y": 0, "width": 1, "height": 1},
                    "B": {"x": 1, "y": 0, "width": 1, "height": 1},
                    "D": {"x": 2, "y": 0, "width": 1, "height": 1},
                }
            },
        },
        "192.168.0.20",
    )

    assert saved
    assert applied
    assert applied[0][0].get_node("D").x == 2
    assert applied[0][1] is True


def test_node_manager_group_join_marks_pending_nodes_during_save(qtbot):
    ctx = _ctx()
    pending_snapshots = []

    def save_nodes(nodes, **kwargs):
        pending_snapshots.append(
            (
                ctx.is_pending_join_node("B"),
                ctx.is_pending_join_node("D"),
            )
        )
        ctx.replace_nodes([NodeInfo.from_dict(node) for node in nodes])

    page = NodeManagerPage(
        ctx,
        save_nodes=save_nodes,
        coord_client=None,
    )
    qtbot.addWidget(page)

    page._handle_group_join_payload(
        {
            "accepted": True,
            "detail": "joined",
            "nodes": [
                {"node_id": "A", "name": "A", "ip": "127.0.0.1", "port": 5000},
                {"node_id": "B", "name": "B", "ip": "127.0.0.1", "port": 5001},
                {"node_id": "D", "name": "D", "ip": "192.168.0.20", "port": 45873},
            ],
        },
        "192.168.0.20",
    )

    assert pending_snapshots == [(True, True)]
    assert ctx.is_pending_join_node("B") is False
    assert ctx.is_pending_join_node("D") is False
