"""Tests for runtime/config_reloader.py."""

import shutil
import uuid
from pathlib import Path

import pytest

from runtime.config_reloader import RuntimeConfigReloader, validate_reloadable_self
from runtime.context import NodeInfo, RuntimeContext
from runtime.layouts import LayoutConfig, LayoutNode


class FakeDialer:
    def __init__(self):
        self.refresh_calls = 0

    def refresh_peers(self):
        self.refresh_calls += 1


class FakeRouter:
    def __init__(self, target_id=None):
        self._target_id = target_id
        self.clear_calls = []

    def get_selected_target(self):
        return self._target_id

    def clear_target(self, reason=None):
        self.clear_calls.append(reason)
        self._target_id = None


class FakeCoordinatorClient:
    def __init__(self):
        self.clear_calls = 0

    def clear_target(self):
        self.clear_calls += 1


def _ctx():
    nodes = [
        NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000}),
        NodeInfo.from_dict({"name": "B", "ip": "127.0.0.1", "port": 5001}),
    ]
    return RuntimeContext(
        self_node=nodes[0],
        nodes=nodes,
        config_path=Path("config.json"),
        layout=LayoutConfig(nodes=(LayoutNode("A", 0, 0), LayoutNode("B", 1, 0))),
    )


def test_validate_reloadable_self_rejects_port_change():
    current = NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000})
    changed = NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5001})

    with pytest.raises(ValueError, match="port"):
        validate_reloadable_self(current, changed)


def _make_test_dir():
    path = Path("tests") / "_tmp" / str(uuid.uuid4())
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_reload_updates_nodes_and_refreshes_dialer():
    tmp_dir = _make_test_dir()
    config_path = tmp_dir / "config.json"
    config_path.write_text(
        (
            '{\n'
            '  "nodes": [\n'
            '    {"name": "A", "ip": "127.0.0.1", "port": 5000},\n'
            '    {"name": "C", "ip": "127.0.0.1", "port": 5002, "roles": ["target"]}\n'
            "  ]\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    ctx = _ctx()
    ctx.config_path = config_path
    dialer = FakeDialer()
    reloader = RuntimeConfigReloader(ctx, dialer=dialer)

    try:
        reloader.reload()

        assert [node.node_id for node in ctx.nodes] == ["A", "C"]
        assert [node.node_id for node in ctx.peers] == ["C"]
        assert dialer.refresh_calls == 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_reload_clears_removed_selected_target():
    tmp_dir = _make_test_dir()
    config_path = tmp_dir / "config.json"
    config_path.write_text(
        (
            '{\n'
            '  "nodes": [\n'
            '    {"name": "A", "ip": "127.0.0.1", "port": 5000},\n'
            '    {"name": "C", "ip": "127.0.0.1", "port": 5002}\n'
            "  ]\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    ctx = _ctx()
    ctx.config_path = config_path
    router = FakeRouter(target_id="B")
    coord_client = FakeCoordinatorClient()
    reloader = RuntimeConfigReloader(ctx, router=router, coord_client=coord_client)

    try:
        reloader.reload()

        assert coord_client.clear_calls == 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_save_layout_updates_config_and_runtime_context():
    tmp_dir = _make_test_dir()
    config_path = tmp_dir / "config.json"
    config_path.write_text(
        (
            '{\n'
            '  "nodes": [\n'
            '    {"name": "A", "ip": "127.0.0.1", "port": 5000},\n'
            '    {"name": "B", "ip": "127.0.0.1", "port": 5001}\n'
            "  ]\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    ctx = _ctx()
    ctx.config_path = config_path
    reloader = RuntimeConfigReloader(ctx)
    layout = LayoutConfig(
        nodes=(LayoutNode("A", 0, 0), LayoutNode("B", 2, 1)),
    )

    try:
        reloader.save_layout(layout)

        assert ctx.layout is not None
        assert ctx.layout.get_node("B").x == 2
        text = config_path.read_text(encoding="utf-8")
        assert '"layout"' in text
        assert '"B"' in text
        assert '"x": 2' in text
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_apply_layout_with_debounce_flushes_only_latest_layout():
    tmp_dir = _make_test_dir()
    config_path = tmp_dir / "config.json"
    config_path.write_text(
        (
            '{\n'
            '  "nodes": [\n'
            '    {"name": "A", "ip": "127.0.0.1", "port": 5000},\n'
            '    {"name": "B", "ip": "127.0.0.1", "port": 5001}\n'
            "  ]\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    ctx = _ctx()
    ctx.config_path = config_path
    reloader = RuntimeConfigReloader(ctx)
    reloader.LAYOUT_SAVE_DEBOUNCE_SEC = 60
    first_layout = LayoutConfig(
        nodes=(LayoutNode("A", 0, 0), LayoutNode("B", 2, 1)),
    )
    second_layout = LayoutConfig(
        nodes=(LayoutNode("A", 0, 0), LayoutNode("B", 4, 3)),
    )

    try:
        reloader.apply_layout(first_layout, persist=True, debounce_persist=True)
        reloader.apply_layout(second_layout, persist=True, debounce_persist=True)

        text_before_flush = config_path.read_text(encoding="utf-8")
        assert '"layout"' not in text_before_flush

        assert reloader.flush_pending_layout() is True
        assert reloader.flush_pending_layout() is False

        assert ctx.layout is not None
        assert ctx.layout.get_node("B").x == 4
        text = config_path.read_text(encoding="utf-8")
        assert '"layout"' in text
        assert '"x": 4' in text
        assert '"y": 3' in text
    finally:
        reloader.flush_pending_layout()
        shutil.rmtree(tmp_dir, ignore_errors=True)
