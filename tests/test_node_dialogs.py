"""Tests for runtime/node_dialogs.py."""

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QDialog, QMessageBox, QStyle, QStyleOptionViewItem

from runtime import node_dialogs as node_dialogs_module
from runtime.context import NodeInfo, build_runtime_context
from runtime.node_dialogs import NodeManagerPage


def _ctx():
    config = {
        "nodes": [
            {"name": "A", "ip": "127.0.0.1", "port": 5000},
            {"name": "B", "ip": "127.0.0.1", "port": 5001, "note": "기존"},
            {"name": "C", "ip": "127.0.0.1", "port": 5002},
        ],
    }
    return build_runtime_context(config, override_name="A", config_path="config/config.json")


def test_node_manager_creates_node_from_modal_payload(qtbot, monkeypatch):
    ctx = _ctx()
    saved = []
    page = NodeManagerPage(ctx, save_nodes=lambda nodes, **kwargs: saved.append((nodes, kwargs)))
    qtbot.addWidget(page)

    monkeypatch.setattr(
        page,
        "_open_node_editor",
        lambda **kwargs: {"name": "D", "ip": "127.0.0.1", "port": 5000},
    )

    page._create_node()

    assert saved
    assert [node["name"] for node in saved[0][0]] == ["A", "B", "C", "D"]
    assert saved[0][1]["apply_runtime"] is True


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


def test_node_manager_table_hides_port_column_and_uses_note_column(qtbot):
    ctx = _ctx()
    page = NodeManagerPage(ctx, save_nodes=lambda nodes, **kwargs: None)
    qtbot.addWidget(page)

    headers = [
        page._table.horizontalHeaderItem(index).text()
        for index in range(page._table.columnCount())
    ]

    assert headers == ["선택", "이름", "IP", "비고"]
    assert page._table.columnCount() == 4
    assert page._table.item(0, 1).text() == "A"


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
        lambda **kwargs: {"name": "B", "ip": "127.0.0.1", "port": 5000, "note": "편의실"},
    )

    page._edit_node_by_id("B")

    assert saved
    assert coord_client.calls
    assert coord_client.calls[0][0][1]["note"] == "편의실"
    assert coord_client.calls[0][1] == {}


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
                    {"name": "A", "ip": "127.0.0.1", "port": 5000},
                    {"name": "B", "ip": "127.0.0.1", "port": 5001, "note": "기존"},
                    {"name": "D", "ip": "192.168.0.20", "port": 45873},
                ],
            },
            target_ip,
        ),
    )

    page._join_group()

    assert saved
    assert [node["name"] for node in saved[0][0]] == ["A", "B", "D"]
    assert coord_client.calls
    assert coord_client.calls[0][0][-1]["name"] == "D"
    assert messages[0][1] == "accent"
    assert messages[-1][0] == "노드 그룹에 참여했습니다."

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
                {"name": "A", "ip": "127.0.0.1", "port": 5000},
                {"name": "B", "ip": "127.0.0.1", "port": 5001, "note": "湲곗〈"},
                {"name": "D", "ip": "192.168.0.20", "port": 45873},
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
