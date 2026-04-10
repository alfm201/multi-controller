"""Tests for runtime/config_reloader.py."""

import shutil
import uuid
from pathlib import Path

import pytest

from runtime.config_reloader import RuntimeConfigReloader, validate_reloadable_self
from runtime.context import NodeInfo, RuntimeContext


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
