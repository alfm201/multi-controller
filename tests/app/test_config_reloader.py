"""Tests for app/config/config_reloader.py."""

import os
import shutil
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.config.app_settings import AppSettings, BackupRetentionSettings
from app.config.config_reloader import RuntimeConfigReloader, validate_reloadable_self
from control.state.context import NodeInfo, RuntimeContext
from model.display.layouts import LayoutConfig, LayoutNode


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
        NodeInfo.from_dict({"node_id": "A", "name": "A", "ip": "127.0.0.1", "port": 5000}),
        NodeInfo.from_dict({"node_id": "B", "name": "B", "ip": "127.0.0.2", "port": 5001}),
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


def test_validate_reloadable_self_allows_display_name_change():
    current = NodeInfo.from_dict({"node_id": "node-a", "name": "A", "ip": "127.0.0.1", "port": 5000})
    changed = NodeInfo.from_dict({"node_id": "node-a", "name": "회의실", "ip": "127.0.0.1", "port": 5000})

    validate_reloadable_self(current, changed)


def test_validate_reloadable_self_allows_ip_change():
    current = NodeInfo.from_dict({"node_id": "node-a", "name": "A", "ip": "127.0.0.1", "port": 5000})
    changed = NodeInfo.from_dict({"node_id": "node-a", "name": "A", "ip": "192.168.0.10", "port": 5000})

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
            '    {"node_id": "A", "name": "A", "ip": "127.0.0.1", "port": 5000},\n'
            '    {"node_id": "C", "name": "C", "ip": "127.0.0.3", "port": 5002, "roles": ["target"]}\n'
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
            '    {"node_id": "A", "name": "A", "ip": "127.0.0.1", "port": 5000},\n'
            '    {"node_id": "C", "name": "C", "ip": "127.0.0.3", "port": 5002}\n'
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
            '    {"node_id": "A", "name": "A", "ip": "127.0.0.1", "port": 5000},\n'
            '    {"node_id": "B", "name": "B", "ip": "127.0.0.2", "port": 5001}\n'
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
        base_text = config_path.read_text(encoding="utf-8")
        layout_text = (tmp_dir / "layout.json").read_text(encoding="utf-8")
        assert '"layout"' not in base_text
        assert '"B"' in layout_text
        assert '"x": 2' in layout_text
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_apply_layout_with_debounce_flushes_only_latest_layout():
    tmp_dir = _make_test_dir()
    config_path = tmp_dir / "config.json"
    config_path.write_text(
        (
            '{\n'
            '  "nodes": [\n'
            '    {"node_id": "A", "name": "A", "ip": "127.0.0.1", "port": 5000},\n'
            '    {"node_id": "B", "name": "B", "ip": "127.0.0.2", "port": 5001}\n'
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
        base_text = config_path.read_text(encoding="utf-8")
        layout_text = (tmp_dir / "layout.json").read_text(encoding="utf-8")
        assert '"layout"' not in base_text
        assert '"x": 4' in layout_text
        assert '"y": 3' in layout_text
    finally:
        reloader.flush_pending_layout()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_apply_layout_without_persist_updates_runtime_only():
    tmp_dir = _make_test_dir()
    config_path = tmp_dir / "config.json"
    config_path.write_text(
        (
            '{\n'
            '  "nodes": [\n'
            '    {"node_id": "A", "name": "A", "ip": "127.0.0.1", "port": 5000},\n'
            '    {"node_id": "B", "name": "B", "ip": "127.0.0.2", "port": 5001}\n'
            "  ]\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    ctx = _ctx()
    ctx.config_path = config_path
    reloader = RuntimeConfigReloader(ctx)
    layout = LayoutConfig(
        nodes=(LayoutNode("A", 0, 0), LayoutNode("B", 3, 2)),
    )

    try:
        reloader.apply_layout(layout, persist=False)

        assert ctx.layout is not None
        assert ctx.layout.get_node("B").x == 3
        text = config_path.read_text(encoding="utf-8")
        assert '"layout"' not in text
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_save_nodes_updates_config_and_layout_files():
    tmp_dir = _make_test_dir()
    config_path = tmp_dir / "config.json"
    config_path.write_text(
        (
            '{\n'
            '  "nodes": [\n'
            '    {"node_id": "A", "name": "A", "ip": "127.0.0.1", "port": 5000},\n'
            '    {"node_id": "B", "name": "B", "ip": "127.0.0.2", "port": 5001}\n'
            "  ]\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    ctx = _ctx()
    ctx.config_path = config_path
    reloader = RuntimeConfigReloader(ctx)

    try:
        reloader.save_nodes(
            [
                {"name": "A", "ip": "127.0.0.1", "port": 5000},
                {"name": "C", "ip": "127.0.0.3", "port": 5002},
            ]
        )

        assert [node.node_id for node in ctx.nodes] == ["A", "C"]
        layout_text = (tmp_dir / "layout.json").read_text(encoding="utf-8")
        assert '"C"' in layout_text
        assert '"B"' not in layout_text
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_apply_nodes_state_updates_self_ip_without_restart():
    tmp_dir = _make_test_dir()
    config_path = tmp_dir / "config.json"
    config_path.write_text(
        (
            '{\n'
            '  "nodes": [\n'
            '    {"node_id": "A", "name": "A", "ip": "127.0.0.1", "port": 5000},\n'
            '    {"node_id": "B", "name": "B", "ip": "127.0.0.2", "port": 5001}\n'
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
        reloader.apply_nodes_state(
            [
                {"node_id": "A", "name": "A", "ip": "192.168.0.10", "port": 5000},
                {"node_id": "B", "name": "B", "ip": "127.0.0.2", "port": 5001},
            ],
            rename_map={},
            persist=True,
            apply_runtime=True,
        )

        assert ctx.self_node.ip == "192.168.0.10"
        assert dialer.refresh_calls == 1
        assert '"ip": "192.168.0.10"' in config_path.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_apply_node_note_updates_runtime_and_config():
    tmp_dir = _make_test_dir()
    config_path = tmp_dir / "config.json"
    config_path.write_text(
        (
            '{\n'
            '  "nodes": [\n'
            '    {"node_id": "A", "name": "A", "ip": "127.0.0.1", "port": 5000},\n'
            '    {"node_id": "B", "name": "B", "ip": "127.0.0.2", "port": 5001, "note": "기존"}\n'
            "  ]\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    ctx = _ctx()
    ctx.config_path = config_path
    reloader = RuntimeConfigReloader(ctx)

    try:
        reloader.apply_node_note("B", "회의실", persist=True)

        assert ctx.get_node("B").note == "회의실"
        assert '"note": "회의실"' in config_path.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_apply_nodes_state_updates_runtime_and_config():
    tmp_dir = _make_test_dir()
    config_path = tmp_dir / "config.json"
    config_path.write_text(
        (
            '{\n'
            '  "nodes": [\n'
            '    {"node_id": "A", "name": "A", "ip": "127.0.0.1", "port": 5000},\n'
            '    {"node_id": "B", "name": "B", "ip": "127.0.0.2", "port": 5001}\n'
            "  ]\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    ctx = _ctx()
    ctx.config_path = config_path
    reloader = RuntimeConfigReloader(ctx)

    try:
        reloader.apply_nodes_state(
            [
                {"name": "A", "ip": "127.0.0.1", "port": 5000},
                {"name": "B", "ip": "127.0.0.2", "port": 5001, "note": "회의실"},
                {"name": "D", "ip": "127.0.0.4", "port": 5003},
            ],
            persist=True,
            apply_runtime=True,
        )

        assert ctx.get_node("B").note == "회의실"
        assert ctx.get_node("D") is not None
        assert '"note": "회의실"' in config_path.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_save_nodes_can_persist_restart_only_changes_without_reloading_runtime():
    tmp_dir = _make_test_dir()
    config_path = tmp_dir / "config.json"
    config_path.write_text(
        (
            '{\n'
            '  "nodes": [\n'
            '    {"node_id": "A", "name": "A", "ip": "127.0.0.1", "port": 5000},\n'
            '    {"node_id": "B", "name": "B", "ip": "127.0.0.2", "port": 5001}\n'
            "  ]\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    ctx = _ctx()
    ctx.config_path = config_path
    reloader = RuntimeConfigReloader(ctx)

    try:
        reloader.save_nodes(
            [
                {"node_id": "A", "name": "A2", "ip": "127.0.0.1", "port": 5000, "roles": ["controller", "target"]},
                {"node_id": "B", "name": "B", "ip": "127.0.0.2", "port": 5001, "roles": ["controller", "target"]},
            ],
            rename_map={},
            apply_runtime=True,
        )

        assert [node.node_id for node in ctx.nodes] == ["A", "B"]
        assert ctx.get_node("A").name == "A2"
        base_text = config_path.read_text(encoding="utf-8")
        assert '"node_id": "A"' in base_text
        assert '"name": "A2"' in base_text
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_save_nodes_creates_backup_snapshot():
    tmp_dir = _make_test_dir()
    config_path = tmp_dir / "config.json"
    config_path.write_text(
        (
            '{\n'
            '  "nodes": [\n'
            '    {"node_id": "A", "name": "A", "ip": "127.0.0.1", "port": 5000},\n'
            '    {"node_id": "B", "name": "B", "ip": "127.0.0.2", "port": 5001}\n'
            "  ]\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    ctx = _ctx()
    ctx.config_path = config_path
    reloader = RuntimeConfigReloader(ctx)

    try:
        reloader.save_nodes(
            [
                {"name": "A", "ip": "127.0.0.1", "port": 5000},
                {"name": "C", "ip": "127.0.0.3", "port": 5002},
            ]
        )

        latest = reloader.get_latest_backup_path()
        assert latest is not None
        assert (latest / "config.json").exists()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_restore_latest_backup_restores_previous_runtime_state():
    tmp_dir = _make_test_dir()
    config_path = tmp_dir / "config.json"
    config_path.write_text(
        (
            '{\n'
            '  "nodes": [\n'
            '    {"node_id": "A", "name": "A", "ip": "127.0.0.1", "port": 5000},\n'
            '    {"node_id": "B", "name": "B", "ip": "127.0.0.2", "port": 5001}\n'
            "  ]\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    ctx = _ctx()
    ctx.config_path = config_path
    reloader = RuntimeConfigReloader(ctx)

    try:
        reloader.save_nodes(
            [
                {"name": "A", "ip": "127.0.0.1", "port": 5000},
                {"name": "C", "ip": "127.0.0.3", "port": 5002},
            ]
        )

        restored_path, applied_runtime, detail = reloader.restore_latest_backup()

        assert restored_path.exists()
        assert applied_runtime is True
        assert "반영" in detail
        assert [node.node_id for node in ctx.nodes] == ["A", "B"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_prune_backups_keeps_latest_min_count_and_removes_old_remainder():
    tmp_dir = _make_test_dir()
    config_path = tmp_dir / "config.json"
    config_path.write_text(
        '{\n  "nodes": [{"node_id": "A", "name": "A", "ip": "127.0.0.1", "port": 5000}]\n}\n',
        encoding="utf-8",
    )
    ctx = _ctx()
    ctx.config_path = config_path
    ctx.replace_settings(
        AppSettings(backups=BackupRetentionSettings(min_count=2, max_age_days=30))
    )
    reloader = RuntimeConfigReloader(ctx)
    backup_root = tmp_dir / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    (backup_root / ".multiscreenpass-backups").write_text("managed\n", encoding="utf-8")

    try:
        created = []
        for index in range(4):
            path = backup_root / f"2024010{index + 1}-000000-test"
            path.mkdir(parents=True, exist_ok=True)
            (path / ".multiscreenpass-backup").write_text("managed\n", encoding="utf-8")
            (path / "config.json").write_text("{}", encoding="utf-8")
            created.append(path)

        base_old_timestamp = (datetime.now() - timedelta(days=40)).timestamp()
        recent_timestamp = (datetime.now() - timedelta(days=5)).timestamp()
        for offset, path in enumerate(created[:3]):
            stamp = base_old_timestamp + offset
            os.utime(path, (stamp, stamp))
        os.utime(created[3], (recent_timestamp, recent_timestamp))

        removed = {path.resolve() for path in reloader.prune_backups()}
        created = [path.resolve() for path in created]

        assert created[0] in removed
        assert created[1] in removed
        assert created[2] not in removed
        assert created[3] not in removed
        assert created[0].exists() is False
        assert created[1].exists() is False
        assert created[2].exists() is True
        assert created[3].exists() is True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_prune_backups_skips_unmanaged_root():
    tmp_dir = _make_test_dir()
    config_path = tmp_dir / "config.json"
    config_path.write_text(
        '{\n  "nodes": [{"node_id": "A", "name": "A", "ip": "127.0.0.1", "port": 5000}]\n}\n',
        encoding="utf-8",
    )
    ctx = _ctx()
    ctx.config_path = config_path
    ctx.replace_settings(
        AppSettings(backups=BackupRetentionSettings(min_count=1, max_age_days=1))
    )
    reloader = RuntimeConfigReloader(ctx)
    backup_root = tmp_dir / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    external = backup_root / "external-tool"
    external.mkdir(parents=True, exist_ok=True)
    (external / "config.json").write_text("{}", encoding="utf-8")
    old_timestamp = (datetime.now() - timedelta(days=60)).timestamp()
    os.utime(external, (old_timestamp, old_timestamp))

    try:
        removed = reloader.prune_backups()

        assert removed == []
        assert external.exists() is True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_periodic_backup_pruning_start_is_non_blocking(monkeypatch):
    ctx = _ctx()
    reloader = RuntimeConfigReloader(ctx)
    calls: list[str] = []

    monkeypatch.setattr(reloader, "_run_periodic_backup_prune", lambda *, reason: calls.append(reason))
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    monkeypatch.setattr(threading.Thread, "join", lambda self, timeout=None: None)

    try:
        started = reloader.start_periodic_backup_pruning(interval_sec=1)

        assert started is True
        assert calls == []
    finally:
        reloader.stop_periodic_backup_pruning()


def test_backup_prune_worker_runs_until_stop_requested(monkeypatch):
    class FakeStopEvent:
        def __init__(self):
            self.stopped = False

        def wait(self, _timeout):
            return self.stopped

        def set(self):
            self.stopped = True

    ctx = _ctx()
    reloader = RuntimeConfigReloader(ctx)
    calls: list[str] = []
    stop_event = FakeStopEvent()
    reloader._backup_prune_stop = stop_event
    reloader._backup_prune_thread = object()

    def fake_prune(settings=None):
        calls.append("prune")
        if len(calls) >= 2:
            stop_event.set()
        return []

    monkeypatch.setattr(reloader, "prune_backups", fake_prune)

    reloader._backup_prune_worker(0.01, stop_event)

    assert calls == ["prune", "prune"]
    assert reloader._backup_prune_thread is None


def test_periodic_backup_pruning_stop_is_idempotent():
    ctx = _ctx()
    reloader = RuntimeConfigReloader(ctx)

    assert reloader.stop_periodic_backup_pruning() is False


def test_save_layout_and_settings_persists_both_together():
    tmp_dir = _make_test_dir()
    config_path = tmp_dir / "config.json"
    config_path.write_text(
        (
            '{\n'
            '  "nodes": [\n'
            '    {"node_id": "A", "name": "A", "ip": "127.0.0.1", "port": 5000},\n'
            '    {"node_id": "B", "name": "B", "ip": "127.0.0.2", "port": 5001}\n'
            "  ]\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    ctx = _ctx()
    ctx.config_path = config_path
    reloader = RuntimeConfigReloader(ctx)
    layout = LayoutConfig(nodes=(LayoutNode("A", 0, 0), LayoutNode("B", 4, 2)))
    settings = AppSettings(backups=BackupRetentionSettings(min_count=7, max_age_days=20))

    try:
        reloader.save_layout_and_settings(layout, settings)

        assert ctx.layout is not None
        assert ctx.layout.get_node("B").x == 4
        assert ctx.settings.backups.min_count == 7
        base_text = config_path.read_text(encoding="utf-8")
        layout_text = (tmp_dir / "layout.json").read_text(encoding="utf-8")
        assert '"settings"' in base_text
        assert '"min_count": 7' in base_text
        assert '"x": 4' in layout_text
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_background_layout_save_notifies_when_persist_fails():
    ctx = _ctx()
    reloader = RuntimeConfigReloader(ctx)
    messages = []
    reloader.set_save_error_notifier(lambda message, tone="warning": messages.append((message, tone)))
    reloader._pending_layout = LayoutConfig(nodes=(LayoutNode("A", 0, 0),))
    reloader._pending_layout_version = 1

    def fail(_layout):
        raise RuntimeError("boom")

    reloader._persist_layout = fail

    reloader._flush_pending_layout_version(1)

    assert messages == [("레이아웃 저장에 실패했습니다: boom", "warning")]


def test_background_layout_save_notifies_with_actionable_file_in_use_message():
    ctx = _ctx()
    reloader = RuntimeConfigReloader(ctx)
    messages = []
    reloader.set_save_error_notifier(lambda message, tone="warning": messages.append((message, tone)))
    reloader._pending_layout = LayoutConfig(nodes=(LayoutNode("A", 0, 0),))
    reloader._pending_layout_version = 1

    def fail(_layout):
        err = PermissionError("file in use")
        err.winerror = 32
        err.filename = "layout.json"
        raise err

    reloader._persist_layout = fail

    reloader._flush_pending_layout_version(1)

    assert len(messages) == 1
    assert messages[0][1] == "warning"
    assert "레이아웃 저장에 실패했습니다" in messages[0][0]
    assert "다른 프로그램이 설정 파일을 사용 중입니다" in messages[0][0]


