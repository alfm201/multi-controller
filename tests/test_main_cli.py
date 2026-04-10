"""Tests for main.py CLI/UI mode behavior."""

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
