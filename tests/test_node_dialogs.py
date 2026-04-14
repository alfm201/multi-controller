"""Tests for runtime/node_dialogs.py."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from runtime.context import build_runtime_context
from runtime.node_dialogs import NodeManagerPage


def _ctx():
    config = {
        "nodes": [
            {"name": "A", "ip": "127.0.0.1", "port": 5000},
            {"name": "B", "ip": "127.0.0.1", "port": 5001},
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
        QMessageBox,
        "information",
        lambda *args, **kwargs: messages.append(args[2]) or QMessageBox.Ok,
    )

    page._table.item(1, 0).setCheckState(Qt.Checked)
    page._table.item(2, 0).setCheckState(Qt.Checked)
    page._edit_selected()

    assert "하나의 노드만" in messages[-1]


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
