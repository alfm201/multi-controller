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

    assert captured == {"debug": True}


def test_install_cursor_cleanup_hooks_registers_release_for_exceptions(monkeypatch):
    released = []
    registered = []
    previous_sys_calls = []
    previous_thread_calls = []

    monkeypatch.setattr(main_module.atexit, "register", lambda fn: registered.append(fn))
    monkeypatch.setattr(main_module.sys, "excepthook", lambda *args: previous_sys_calls.append(args))
    monkeypatch.setattr(main_module.threading, "excepthook", lambda args: previous_thread_calls.append(args))

    main_module._install_cursor_cleanup_hooks(lambda: released.append("clip"))

    assert len(registered) == 1

    registered[0]()
    assert released == ["clip"]

    main_module.sys.excepthook(RuntimeError, RuntimeError("boom"), None)
    assert released == ["clip", "clip"]
    assert len(previous_sys_calls) == 1

    thread_args = object()
    main_module.threading.excepthook(thread_args)
    assert released == ["clip", "clip", "clip"]
    assert previous_thread_calls == [thread_args]
