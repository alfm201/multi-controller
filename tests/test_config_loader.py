"""Tests for runtime/config_loader.py."""

from pathlib import Path
import shutil
import uuid

import pytest

import runtime.config_loader as config_loader
from runtime.config_loader import (
    _candidate_paths,
    default_config_path,
    ensure_runtime_config,
    init_config,
    load_config,
    migrate_config,
    validate_config,
)


def _minimal():
    return {"nodes": [{"name": "A", "ip": "127.0.0.1", "port": 45873}]}


def _two_nodes():
    return {
        "nodes": [
            {"name": "A", "ip": "127.0.0.1", "port": 45873},
            {"name": "B", "ip": "127.0.0.2", "port": 45873},
        ],
        "coordinator": {},
    }


def test_minimal_valid():
    validate_config(_minimal())


def test_with_coordinator_valid():
    validate_config(_two_nodes())


def test_legacy_roles_list_is_tolerated():
    cfg = _minimal()
    cfg["nodes"][0]["roles"] = ["controller", "target"]
    validate_config(cfg)


def test_missing_nodes_key():
    with pytest.raises(ValueError, match="nodes"):
        validate_config({})


def test_duplicate_names():
    cfg = {
        "nodes": [
            {"name": "A", "ip": "127.0.0.1", "port": 5000},
            {"name": "A", "ip": "127.0.0.1", "port": 5001},
        ]
    }
    with pytest.raises(ValueError, match="duplicated"):
        validate_config(cfg)


def test_duplicate_ips_are_rejected():
    cfg = {
        "nodes": [
            {"name": "A", "ip": "192.168.0.10", "port": 45873},
            {"name": "B", "ip": "192.168.0.10", "port": 45874},
        ]
    }
    with pytest.raises(ValueError, match="ip is duplicated"):
        validate_config(cfg)


def test_roles_not_list_is_ignored():
    cfg = _minimal()
    cfg["nodes"][0]["roles"] = "controller"
    validate_config(cfg)


def test_roles_unknown_is_ignored():
    cfg = _minimal()
    cfg["nodes"][0]["roles"] = ["invalid-role"]
    validate_config(cfg)


def test_coordinator_section_may_be_empty_object():
    cfg = _minimal()
    cfg["coordinator"] = {}
    validate_config(cfg)


def test_coordinator_not_dict():
    cfg = _minimal()
    cfg["coordinator"] = "bad"
    with pytest.raises(ValueError, match="coordinator"):
        validate_config(cfg)


def test_default_roles_is_ignored():
    cfg = _minimal()
    cfg["default_roles"] = "controller"
    validate_config(cfg)


def test_port_must_be_positive_int():
    cfg = _minimal()
    cfg["nodes"][0]["port"] = 0
    with pytest.raises(ValueError, match="positive"):
        validate_config(cfg)


def test_layout_section_may_define_positions_and_auto_switch():
    cfg = _two_nodes()
    cfg["layout"] = {
        "nodes": {
            "A": {"x": 0, "y": 0},
            "B": {"x": 1, "y": 0, "width": 1, "height": 1},
        },
        "auto_switch": {
            "enabled": True,
            "cooldown_ms": 300,
            "return_guard_ms": 450,
        },
    }

    validate_config(cfg)


def test_layout_rejects_unknown_node_id():
    cfg = _minimal()
    cfg["layout"] = {"nodes": {"B": {"x": 1, "y": 0}}}

    with pytest.raises(ValueError, match="unknown node"):
        validate_config(cfg)


def test_layout_auto_switch_accepts_legacy_tuning_fields():
    cfg = _minimal()
    cfg["layout"] = {
        "auto_switch": {
            "edge_threshold": 0.5,
            "warp_margin": 0.2,
            "anchor_dead_zone": 0.25,
        }
    }

    validate_config(cfg)


def test_layout_monitor_topology_allows_logical_and_physical_grids():
    cfg = _minimal()
    cfg["layout"] = {
        "nodes": {
            "A": {
                "monitors": {
                    "logical": [["1", "2", "3", "4", "5", "6"]],
                    "physical": [["1", "2", "3"], ["4", "5", "6"]],
                }
            }
        }
    }

    validate_config(cfg)


def test_layout_monitor_topology_requires_matching_ids():
    cfg = _minimal()
    cfg["layout"] = {
        "nodes": {
            "A": {
                "monitors": {
                    "logical": [["1", "2"]],
                    "physical": [["1", "3"]],
                }
            }
        }
    }

    with pytest.raises(ValueError, match="ids must match"):
        validate_config(cfg)


def test_load_config_merges_split_files():
    tmp_dir = Path("tests") / "_tmp" / str(uuid.uuid4())
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        (tmp_dir / "config.json").write_text(
            (
                '{\n'
                '  "nodes": [\n'
                '    {"name": "A", "ip": "127.0.0.1", "port": 45873},\n'
                '    {"name": "B", "ip": "127.0.0.2", "port": 45873}\n'
                "  ]\n"
                "}\n"
            ),
            encoding="utf-8",
        )
        (tmp_dir / "layout.json").write_text(
            (
                '{\n'
                '  "nodes": {\n'
                '    "A": {"x": 0, "y": 0, "width": 1, "height": 1},\n'
                '    "B": {"x": 1, "y": 0, "width": 1, "height": 1}\n'
                "  }\n"
                "}\n"
            ),
            encoding="utf-8",
        )
        (tmp_dir / "monitor_inventory.json").write_text(
            (
                '{\n'
                '  "nodes": {\n'
                '    "B": {\n'
                '      "node_id": "B",\n'
                '      "captured_at": "10:00:00",\n'
                '      "monitors": [\n'
                '        {"monitor_id": "\\\\\\\\.\\\\DISPLAY1", "display_name": "Display 1", "bounds": {"left": 0, "top": 0, "width": 1920, "height": 1080}, "logical_order": 0},\n'
                '        {"monitor_id": "\\\\\\\\.\\\\DISPLAY2", "display_name": "Display 2", "bounds": {"left": 1920, "top": 0, "width": 1920, "height": 1080}, "logical_order": 1}\n'
                "      ]\n"
                "    }\n"
                "  }\n"
                "}\n"
            ),
            encoding="utf-8",
        )

        config, resolved = load_config(tmp_dir / "config.json")

        assert resolved == tmp_dir / "config.json"
        assert config["layout"]["nodes"]["B"]["x"] == 1
        assert config["monitor_inventory"]["nodes"]["B"]["node_id"] == "B"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_load_config_preserves_legacy_role_fields_without_migration():
    tmp_dir = Path("tests") / "_tmp" / str(uuid.uuid4())
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        (tmp_dir / "config.json").write_text(
            (
                '{\n'
                '  "default_roles": ["target"],\n'
                '  "nodes": [\n'
                '    {"name": "A", "ip": "127.0.0.1", "port": 45873, "roles": ["controller"]}\n'
                "  ]\n"
                "}\n"
            ),
            encoding="utf-8",
        )

        config, _resolved = load_config(tmp_dir / "config.json")

        assert config["default_roles"] == ["target"]
        assert config["nodes"][0]["roles"] == ["controller"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_load_config_accepts_config_subdirectory_path():
    tmp_dir = Path("tests") / "_tmp" / str(uuid.uuid4())
    config_dir = tmp_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    try:
        (config_dir / "config.json").write_text(
            (
                '{\n'
                '  "nodes": [\n'
                '    {"name": "A", "ip": "127.0.0.1", "port": 45873}\n'
                "  ]\n"
                "}\n"
            ),
            encoding="utf-8",
        )

        config, resolved = load_config(config_dir / "config.json")

        assert resolved == config_dir / "config.json"
        assert config["nodes"][0]["name"] == "A"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_candidate_paths_prefer_config_subdirectory_before_legacy_root():
    candidates = list(_candidate_paths())

    assert candidates[0].name == "config.json"
    assert candidates[0].parent.name == "config"
    assert candidates[1].name == "config.json"


def test_default_config_path_uses_localappdata_when_frozen(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\Test\AppData\Local")
    monkeypatch.setattr("runtime.config_loader.sys.frozen", True, raising=False)
    monkeypatch.setattr("runtime.config_loader.sys.executable", r"C:\Program Files\MultiScreenPass\MultiScreenPass.exe")

    path = default_config_path()

    assert path == Path(r"C:\Users\Test\AppData\Local\MultiScreenPass\config\config.json")


def test_candidate_paths_use_only_localappdata_when_frozen(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\Test\AppData\Local")
    monkeypatch.setattr("runtime.config_loader.sys.frozen", True, raising=False)
    monkeypatch.setattr("runtime.config_loader.sys.executable", r"C:\Program Files\MultiScreenPass\MultiScreenPass.exe")

    candidates = list(_candidate_paths())

    assert candidates == [Path(r"C:\Users\Test\AppData\Local\MultiScreenPass\config\config.json")]


def test_init_config_creates_split_config_directory():
    tmp_dir = Path("tests") / "_tmp" / str(uuid.uuid4())
    try:
        path = init_config(tmp_dir / "config" / "config.json")

        assert path == tmp_dir / "config" / "config.json"
        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        assert '"name": "A"' in text
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_migrate_config_writes_split_destination():
    tmp_dir = Path("tests") / "_tmp" / str(uuid.uuid4())
    legacy = tmp_dir / "legacy.json"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        legacy.write_text(
            (
                '{\n'
                '  "nodes": [\n'
                '    {"name": "A", "ip": "127.0.0.1", "port": 45873},\n'
                '    {"name": "B", "ip": "127.0.0.2", "port": 45873}\n'
                "  ],\n"
                '  "layout": {\n'
                '    "nodes": {"B": {"x": 1, "y": 0, "width": 1, "height": 1}}\n'
                "  }\n"
                "}\n"
            ),
            encoding="utf-8",
        )

        source, destination = migrate_config(legacy)

        assert source == legacy
        assert destination == tmp_dir / "config" / "config.json"
        assert destination.is_file()
        assert (tmp_dir / "config" / "layout.json").is_file()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_ensure_runtime_config_creates_local_config_when_missing(monkeypatch):
    tmp_dir = Path("tests") / "_tmp" / str(uuid.uuid4())
    config_path = tmp_dir / "config" / "config.json"
    try:
        monkeypatch.setattr("runtime.config_loader.get_local_ips", lambda: {"192.168.0.10", "127.0.0.1"})
        monkeypatch.setattr("runtime.config_loader.socket.gethostname", lambda: "MY-PC")

        config, resolved = ensure_runtime_config(config_path)

        assert resolved == config_path
        assert config_path.is_file()
        assert config["nodes"] == [{"name": "MY-PC", "ip": "192.168.0.10", "port": 45873}]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_ensure_runtime_config_appends_local_node_when_missing(monkeypatch):
    tmp_dir = Path("tests") / "_tmp" / str(uuid.uuid4())
    config_path = tmp_dir / "config" / "config.json"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            (
                '{\n'
                '  "nodes": [\n'
                '    {"name": "A", "ip": "10.0.0.10", "port": 5000},\n'
                '    {"name": "B", "ip": "10.0.0.11", "port": 5000}\n'
                "  ]\n"
                "}\n"
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("runtime.config_loader.get_local_ips", lambda: {"192.168.0.10", "127.0.0.1"})
        monkeypatch.setattr("runtime.config_loader.socket.gethostname", lambda: "MY-PC")

        config, _resolved = ensure_runtime_config(config_path, override_name="CUSTOM")

        assert [node["name"] for node in config["nodes"]] == ["A", "B", "MY-PC"]
        assert config["nodes"][-1]["ip"] == "192.168.0.10"
        assert config["nodes"][-1]["port"] == 45873
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_ensure_runtime_config_updates_hostname_match_with_local_ip(monkeypatch):
    tmp_dir = Path("tests") / "_tmp" / str(uuid.uuid4())
    config_path = tmp_dir / "config" / "config.json"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            (
                '{\n'
                '  "nodes": [\n'
                '    {"name": "DESKTOP", "ip": "10.0.0.10", "port": 5000},\n'
                '    {"name": "OTHER", "ip": "10.0.0.11", "port": 5000}\n'
                "  ]\n"
                "}\n"
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("runtime.config_loader.get_local_ips", lambda: {"192.168.0.10", "127.0.0.1"})
        monkeypatch.setattr("runtime.config_loader.socket.gethostname", lambda: "DESKTOP")

        config, _resolved = ensure_runtime_config(config_path)

        assert len(config["nodes"]) == 2
        assert config["nodes"][0]["name"] == "DESKTOP"
        assert config["nodes"][0]["ip"] == "192.168.0.10"
        assert config["nodes"][0]["port"] == 5000
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_ensure_runtime_config_creates_localappdata_config_when_frozen(monkeypatch):
    tmp_dir = Path("tests") / "_tmp" / str(uuid.uuid4())
    user_root = tmp_dir / "LocalAppData"
    try:
        monkeypatch.setenv("LOCALAPPDATA", str(user_root))
        monkeypatch.setattr("runtime.config_loader.sys.frozen", True, raising=False)
        monkeypatch.setattr(
            "runtime.config_loader.sys.executable",
            str(tmp_dir / "Program Files" / "MultiScreenPass" / "MultiScreenPass.exe"),
        )
        monkeypatch.setattr("runtime.config_loader.get_local_ips", lambda: {"10.0.0.10", "127.0.0.1"})
        monkeypatch.setattr("runtime.config_loader.socket.gethostname", lambda: "A")

        config, resolved = ensure_runtime_config()

        expected = user_root / "MultiScreenPass" / "config" / "config.json"
        assert resolved == expected
        assert expected.is_file()
        assert config["nodes"][0]["name"] == "A"
        assert config["nodes"][0]["ip"] == "10.0.0.10"
        assert config["nodes"][0]["port"] == 45873
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_ensure_runtime_config_does_not_rewrite_legacy_role_fields_without_node_changes(monkeypatch):
    tmp_dir = Path("tests") / "_tmp" / str(uuid.uuid4())
    config_path = tmp_dir / "config" / "config.json"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            (
                '{\n'
                '  "default_roles": ["target"],\n'
                '  "nodes": [\n'
                '    {"name": "MY-PC", "ip": "192.168.0.10", "port": 45873, "roles": ["controller"]}\n'
                "  ]\n"
                "}\n"
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("runtime.config_loader.get_local_ips", lambda: {"192.168.0.10", "127.0.0.1"})
        monkeypatch.setattr("runtime.config_loader.socket.gethostname", lambda: "MY-PC")

        config, _resolved = ensure_runtime_config(config_path)

        assert config["default_roles"] == ["target"]
        assert config["nodes"][0]["roles"] == ["controller"]
        saved = config_path.read_text(encoding="utf-8")
        assert '"default_roles"' in saved
        assert '"roles"' in saved
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_save_config_retries_replace_on_permission_error(monkeypatch):
    tmp_dir = Path("tests") / "_tmp" / str(uuid.uuid4())
    config_path = tmp_dir / "config" / "config.json"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        attempts = {"count": 0}
        real_replace = config_loader.os.replace

        def flaky_replace(src, dst):
            attempts["count"] += 1
            if attempts["count"] == 1:
                err = PermissionError("file in use")
                err.winerror = 32
                raise err
            return real_replace(src, dst)

        monkeypatch.setattr(config_loader.os, "replace", flaky_replace)
        monkeypatch.setattr(config_loader.time, "sleep", lambda _seconds: None)

        config_loader.save_config(_minimal(), config_path)

        assert attempts["count"] == 2
        assert config_path.is_file()
        saved = config_path.read_text(encoding="utf-8")
        assert '"name": "A"' in saved
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
