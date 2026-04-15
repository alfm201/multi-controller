"""Tests for runtime/node_dialogs.py."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox

from runtime import node_dialogs as node_dialogs_module
from runtime.context import build_runtime_context
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

    assert "하나의 노드만" in messages[-1][1]


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
        lambda **kwargs: {"name": "B", "ip": "127.0.0.1", "port": 5000, "note": "회의실"},
    )

    page._edit_node_by_id("B")

    assert saved
    assert coord_client.calls
    assert coord_client.calls[0][0][1]["note"] == "회의실"
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

    monkeypatch.setattr(node_dialogs_module.GroupJoinDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(node_dialogs_module.GroupJoinDialog, "target_ip", lambda self: "192.168.0.20")
    monkeypatch.setattr(
        node_dialogs_module,
        "request_group_join_state",
        lambda target_ip, requester_id: {
            "detail": "노드 그룹에 참여했습니다.",
            "nodes": [
                {"name": "A", "ip": "127.0.0.1", "port": 5000},
                {"name": "B", "ip": "127.0.0.1", "port": 5001, "note": "기존"},
                {"name": "D", "ip": "192.168.0.20", "port": 45873},
            ],
        },
    )

    page._join_group()

    assert saved
    assert [node["name"] for node in saved[0][0]] == ["A", "B", "D"]
    assert coord_client.calls
    assert coord_client.calls[0][0][-1]["name"] == "D"
