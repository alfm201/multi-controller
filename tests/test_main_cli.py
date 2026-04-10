"""Tests for main.py CLI/UI mode behavior."""

import main as main_module
from main import parse_args, resolve_ui_mode


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


def test_runtime_and_layout_diagnostics_can_be_requested_together():
    args = parse_args(["--diagnostics", "--layout-diagnostics"])

    assert args.diagnostics is True
    assert args.layout_diagnostics is True


def test_main_runtime_diagnostics_does_not_require_config(monkeypatch, capsys):
    monkeypatch.setattr(main_module, "parse_args", lambda: parse_args(["--diagnostics"]))
    monkeypatch.setattr(main_module, "setup_logging", lambda: None)
    monkeypatch.setattr(main_module, "log_windows_interaction_diagnostics", lambda: None)
    monkeypatch.setattr(main_module, "build_runtime_diagnostics", lambda: {"ok": True})
    monkeypatch.setattr(
        main_module,
        "load_config",
        lambda _path: (_ for _ in ()).throw(AssertionError("load_config should not be called")),
    )

    main_module.main()

    assert '"ok": true' in capsys.readouterr().out
