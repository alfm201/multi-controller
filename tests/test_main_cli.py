"""Tests for main.py CLI/UI mode behavior."""

import main as main_module
from main import parse_args, resolve_ui_mode
from runtime.monitor_inventory import MonitorBounds, MonitorInventoryItem, MonitorInventorySnapshot


def test_default_run_uses_gui_mode():
    args = parse_args([])

    assert args.gui is False
    assert args.console is False
    assert args.tray is False
    assert resolve_ui_mode(args) == "gui"


def test_console_flag_disables_gui_mode():
    args = parse_args(["--console"])

    assert args.console is True
    assert resolve_ui_mode(args) == "console"


def test_tray_flag_selects_tray_mode():
    args = parse_args(["--tray"])

    assert args.tray is True
    assert resolve_ui_mode(args) == "tray"


def test_gui_flag_remains_accepted_for_compatibility():
    args = parse_args(["--gui"])

    assert args.gui is True
    assert resolve_ui_mode(args) == "gui"


def test_layout_diagnostics_flag_is_parsed():
    args = parse_args(["--layout-diagnostics"])

    assert args.layout_diagnostics is True
    assert resolve_ui_mode(args) == "gui"


def test_debug_flag_is_parsed():
    args = parse_args(["--debug"])

    assert args.debug is True
    assert resolve_ui_mode(args) == "gui"


def test_runtime_and_layout_diagnostics_can_be_requested_together():
    args = parse_args(["--diagnostics", "--layout-diagnostics"])

    assert args.diagnostics is True
    assert args.layout_diagnostics is True


def test_config_helper_flags_are_parsed():
    args = parse_args(["--init-config", "--force"])

    assert args.init_config is True
    assert args.force is True


def test_main_runtime_diagnostics_does_not_require_config(monkeypatch, capsys):
    monkeypatch.setattr(main_module, "parse_args", lambda: parse_args(["--diagnostics"]))
    monkeypatch.setattr(main_module, "setup_logging", lambda **_kwargs: None)
    monkeypatch.setattr(main_module, "log_windows_interaction_diagnostics", lambda: None)
    monkeypatch.setattr(main_module, "build_runtime_diagnostics", lambda: {"ok": True})
    monkeypatch.setattr(
        main_module,
        "ensure_runtime_config",
        lambda _path, override_name=None: (_ for _ in ()).throw(
            AssertionError("ensure_runtime_config should not be called")
        ),
    )

    main_module.main()

    assert '"ok": true' in capsys.readouterr().out


def test_main_passes_debug_flag_to_setup_logging(monkeypatch):
    captured = {}

    monkeypatch.setattr(main_module, "parse_args", lambda: parse_args(["--debug", "--diagnostics"]))
    monkeypatch.setattr(
        main_module,
        "setup_logging",
        lambda **kwargs: captured.update(kwargs),
    )
    monkeypatch.setattr(main_module, "log_windows_interaction_diagnostics", lambda: None)
    monkeypatch.setattr(main_module, "build_runtime_diagnostics", lambda: {"ok": True})

    main_module.main()

    assert captured == {
        "debug": True,
        "log_dir": main_module._runtime_log_dir(None),
        "retention_days": 14,
        "max_total_size_mb": 100,
    }


def test_runtime_log_dir_uses_user_location_for_frozen_app(monkeypatch, tmp_path):
    exe_dir = tmp_path / "Program Files" / "Multi Screen Pass"
    exe_dir.mkdir(parents=True)
    monkeypatch.setattr(main_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main_module.sys, "executable", str(exe_dir / "MultiScreenPass.exe"), raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))

    assert main_module._runtime_log_dir(None) == tmp_path / "LocalAppData" / "MultiScreenPass" / "logs"


def test_runtime_log_dir_uses_localappdata_for_dev_config(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(main_module.sys, "frozen", False, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))

    assert main_module._runtime_log_dir(config_path) == tmp_path / "LocalAppData" / "MultiScreenPass" / "logs"


def test_run_main_shows_friendly_dialog_for_frozen_startup_exception(monkeypatch):
    shown = []
    released = []

    monkeypatch.setattr(main_module, "main", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(main_module, "release_input_guards", lambda: released.append(True))
    monkeypatch.setattr(
        main_module,
        "show_user_friendly_error_dialog",
        lambda **kwargs: shown.append(kwargs),
    )
    monkeypatch.setattr(main_module.sys, "frozen", True, raising=False)

    try:
        main_module.run_main()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("SystemExit should be raised for frozen startup exception")

    assert released == [True]
    assert shown[0]["exc_type"] is RuntimeError
    assert str(shown[0]["exc_value"]) == "boom"


def test_install_cursor_cleanup_hooks_registers_release_for_exceptions(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        main_module,
        "install_unhandled_exception_handler",
        lambda **kwargs: captured.update(kwargs),
    )
    monkeypatch.setattr(main_module.sys, "frozen", True, raising=False)

    def cleanup_action():
        return None

    main_module._install_cursor_cleanup_hooks(cleanup_action, log_path="logs/debug.log")

    assert captured == {
        "app_name": "Multi Screen Pass",
        "cleanup_actions": (cleanup_action,),
        "log_path": "logs/debug.log",
        "delegate_previous": False,
    }


def test_host_cursor_parking_point_prefers_primary_inventory_monitor():
    class DummyCtx:
        def __init__(self):
            self.self_node = type("Node", (), {"node_id": "A"})()
            self._snapshots = {
                "A": MonitorInventorySnapshot(
                    node_id="A",
                    monitors=(
                        MonitorInventoryItem("1", "Display 1", MonitorBounds(0, 0, 1920, 1080), is_primary=False),
                        MonitorInventoryItem("2", "Display 2", MonitorBounds(1920, 0, 2560, 1440), is_primary=True),
                    ),
                )
            }

        def get_monitor_inventory(self, node_id):
            return self._snapshots.get(node_id)

    assert main_module._host_cursor_parking_point(DummyCtx()) == (3199, 719)


def test_target_primary_display_id_prefers_inventory_primary_monitor():
    class DummyNode:
        def __init__(self):
            self.node_id = "B"

        def monitors(self):
            logical = (
                type("Display", (), {"display_id": "1"})(),
                type("Display", (), {"display_id": "2"})(),
            )
            return type("Topology", (), {"logical": logical, "physical": logical})()

    class DummyLayout:
        def get_node(self, node_id):
            if node_id == "B":
                return DummyNode()
            return None

    class DummyCtx:
        def __init__(self):
            self.self_node = type("Node", (), {"node_id": "A"})()
            self.layout = DummyLayout()
            self._snapshots = {
                "B": MonitorInventorySnapshot(
                    node_id="B",
                    monitors=(
                        MonitorInventoryItem("1", "Display 1", MonitorBounds(0, 0, 1920, 1080), is_primary=False),
                        MonitorInventoryItem("2", "Display 2", MonitorBounds(1920, 0, 2560, 1440), is_primary=True),
                    ),
                )
            }

        def get_monitor_inventory(self, node_id):
            return self._snapshots.get(node_id)

    assert main_module._target_primary_display_id(DummyCtx(), "B") == "2"


def test_build_target_primary_center_anchor_uses_target_primary_monitor_center():
    class DummyNode:
        def __init__(self):
            self.node_id = "B"

        def monitors(self):
            logical = (
                type("Display", (), {"display_id": "1"})(),
                type("Display", (), {"display_id": "2"})(),
            )
            return type(
                "Topology",
                (),
                {
                    "logical": logical,
                    "physical": logical,
                    "get_logical_display": lambda self, display_id: next(
                        (display for display in logical if display.display_id == display_id),
                        None,
                    ),
                    "get_physical_display": lambda self, display_id: next(
                        (display for display in logical if display.display_id == display_id),
                        None,
                    ),
                },
            )()

    class DummyLayout:
        def get_node(self, node_id):
            if node_id == "B":
                return DummyNode()
            return None

    class DummyCtx:
        def __init__(self):
            self.self_node = type("Node", (), {"node_id": "A"})()
            self.layout = DummyLayout()
            self._snapshots = {
                "B": MonitorInventorySnapshot(
                    node_id="B",
                    monitors=(
                        MonitorInventoryItem("1", "Display 1", MonitorBounds(0, 0, 1920, 1080), is_primary=False),
                        MonitorInventoryItem("2", "Display 2", MonitorBounds(1920, 0, 2560, 1440), is_primary=True),
                    ),
                )
            }

        def get_monitor_inventory(self, node_id):
            return self._snapshots.get(node_id)

    event = main_module._build_target_primary_center_anchor(DummyCtx(), "B")

    assert event["kind"] == "mouse_move"
    assert event["x"] == 3200
    assert event["y"] == 720


def test_host_cursor_parking_point_falls_back_to_primary_screen_bounds(monkeypatch):
    class DummyCtx:
        def __init__(self):
            self.self_node = type("Node", (), {"node_id": "A"})()

        def get_monitor_inventory(self, node_id):
            return None

    monkeypatch.setattr(
        main_module,
        "get_primary_screen_bounds",
        lambda: main_module.get_virtual_screen_bounds().__class__(100, 50, 801, 601),
    )

    assert main_module._host_cursor_parking_point(DummyCtx()) == (500, 350)


def test_restore_local_cursor_after_target_exit_uses_edge_return_anchor_when_available():
    class DummyRouter:
        def __init__(self):
            self.calls = 0

        def consume_local_return_anchor_event(self):
            self.calls += 1
            return {"kind": "mouse_move", "x": 111, "y": 222}

    class DummyCursor:
        def __init__(self):
            self.clear_calls = 0
            self.moves = []

        def clear_clip(self):
            self.clear_calls += 1
            return True

        def move(self, x, y):
            self.moves.append((x, y))
            return True

    class DummyCtx:
        def __init__(self):
            self.self_node = type("Node", (), {"node_id": "A"})()

        def get_monitor_inventory(self, node_id):
            return None

    cursor = DummyCursor()
    router = DummyRouter()

    assert main_module._restore_local_cursor_after_target_exit(router, cursor, DummyCtx()) is True
    assert cursor.clear_calls == 1
    assert cursor.moves == [(111, 222)]


def test_restore_local_cursor_after_target_exit_falls_back_to_parking_point(monkeypatch):
    class DummyRouter:
        def consume_local_return_anchor_event(self):
            return None

    class DummyCursor:
        def __init__(self):
            self.clear_calls = 0
            self.moves = []

        def clear_clip(self):
            self.clear_calls += 1
            return True

        def move(self, x, y):
            self.moves.append((x, y))
            return True

    monkeypatch.setattr(main_module, "_host_cursor_parking_point", lambda ctx: (333, 444))

    class DummyCtx:
        pass

    cursor = DummyCursor()

    assert main_module._restore_local_cursor_after_target_exit(DummyRouter(), cursor, DummyCtx()) is True
    assert cursor.clear_calls == 1
    assert cursor.moves == [(333, 444)]


def test_park_local_cursor_for_active_target_uses_parking_point_without_clip(monkeypatch):
    class DummyCursor:
        def __init__(self):
            self.moves = []
            self.clear_calls = 0

        def move(self, x, y):
            self.moves.append((x, y))
            return True

        def clear_clip(self):
            self.clear_calls += 1
            return True

    monkeypatch.setattr(main_module, "_host_cursor_parking_point", lambda ctx: (555, 666))

    class DummyCtx:
        pass

    cursor = DummyCursor()

    assert main_module._park_local_cursor_for_active_target(cursor, DummyCtx()) is True
    assert cursor.moves == [(555, 666)]
    assert cursor.clear_calls == 1
