"""Tests for runtime/clip_recovery.py."""

from runtime import clip_recovery


class FakeUser32:
    def __init__(self):
        self.calls = []

    def ClipCursor(self, rect):
        self.calls.append(rect)
        return 1


class FakeKernel32:
    def __init__(self, *, open_handle=1, exit_code=clip_recovery.STILL_ACTIVE, exit_code_ok=True):
        self.open_handle = open_handle
        self.exit_code = exit_code
        self.exit_code_ok = exit_code_ok
        self.closed = []

    def OpenProcess(self, _access, _inherit, _pid):
        return self.open_handle

    def GetExitCodeProcess(self, _handle, exit_code_ref):
        if not self.exit_code_ok:
            return 0
        exit_code_ref._obj.value = self.exit_code
        return 1

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1


def test_release_cursor_clip_calls_clipcursor_none():
    user32 = FakeUser32()

    assert clip_recovery.release_cursor_clip(user32=user32) is True
    assert user32.calls == [None]


def test_restore_cursor_scheme_calls_restore_and_show(monkeypatch):
    called = []

    monkeypatch.setattr(
        clip_recovery,
        "restore_system_cursors",
        lambda *, user32=None: called.append(("restore", user32)) or True,
    )
    monkeypatch.setattr(
        clip_recovery,
        "best_effort_show_cursor",
        lambda *, user32=None: called.append(("show", user32)) or True,
    )

    assert clip_recovery.restore_cursor_scheme(user32="u32") is True
    assert called == [("restore", "u32"), ("show", "u32")]


def test_release_input_guards_runs_clip_and_cursor_restore(monkeypatch):
    called = []

    monkeypatch.setattr(
        clip_recovery,
        "release_cursor_clip",
        lambda *, user32=None: called.append(("clip", user32)) or True,
    )
    monkeypatch.setattr(
        clip_recovery,
        "restore_cursor_scheme",
        lambda *, user32=None: called.append(("cursor", user32)) or True,
    )

    assert clip_recovery.release_input_guards(user32="u32") is True
    assert called == [("clip", "u32"), ("cursor", "u32")]


def test_is_process_alive_uses_exit_code():
    kernel32 = FakeKernel32(exit_code=clip_recovery.STILL_ACTIVE)
    assert clip_recovery.is_process_alive(1234, kernel32=kernel32) is True

    kernel32 = FakeKernel32(exit_code=0)
    assert clip_recovery.is_process_alive(1234, kernel32=kernel32) is False


def test_wait_for_parent_exit_calls_release_once():
    checks = iter((True, True, False))
    released = []

    clip_recovery.wait_for_parent_exit(
        999,
        poll_interval=0.01,
        is_alive_fn=lambda _pid: next(checks),
        sleep_fn=lambda _seconds: None,
        on_parent_exit=lambda: released.append("done"),
    )

    assert released == ["done"]


def test_resolve_mouse_unlock_tool_command_prefers_script_during_development(tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "mouse_unlock_tool.py").write_text("print('ok')\n", encoding="utf-8")
    exe_path = tmp_path / clip_recovery.RECOVERY_EXECUTABLE_FILENAMES[0]
    exe_path.write_bytes(b"stub")

    assert clip_recovery.resolve_mouse_unlock_tool_command(tmp_path) == [
        clip_recovery.sys.executable,
        str(scripts_dir / "mouse_unlock_tool.py"),
    ]


def test_resolve_mouse_unlock_tool_command_falls_back_to_python_script(tmp_path, monkeypatch):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script_path = scripts_dir / "mouse_unlock_tool.py"
    script_path.write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(clip_recovery.sys, "executable", "python-test")

    assert clip_recovery.resolve_mouse_unlock_tool_command(tmp_path) == ["python-test", str(script_path)]


def test_resolve_mouse_unlock_tool_command_uses_built_exe_when_frozen(tmp_path, monkeypatch):
    exe_path = tmp_path / clip_recovery.RECOVERY_EXECUTABLE_FILENAMES[0]
    exe_path.write_bytes(b"stub")
    monkeypatch.setattr(clip_recovery.sys, "frozen", True, raising=False)

    assert clip_recovery.resolve_mouse_unlock_tool_command(tmp_path) == [str(exe_path)]


def test_resolve_mouse_unlock_tool_command_uses_legacy_built_exe_when_frozen(tmp_path, monkeypatch):
    exe_path = tmp_path / clip_recovery.RECOVERY_EXECUTABLE_FILENAMES[1]
    exe_path.write_bytes(b"stub")
    monkeypatch.setattr(clip_recovery.sys, "frozen", True, raising=False)

    assert clip_recovery.resolve_mouse_unlock_tool_command(tmp_path) == [str(exe_path)]


def test_resolve_mouse_unlock_tool_command_uses_old_korean_built_exe_when_frozen(tmp_path, monkeypatch):
    exe_path = tmp_path / clip_recovery.RECOVERY_EXECUTABLE_FILENAMES[2]
    exe_path.write_bytes(b"stub")
    monkeypatch.setattr(clip_recovery.sys, "frozen", True, raising=False)

    assert clip_recovery.resolve_mouse_unlock_tool_command(tmp_path) == [str(exe_path)]


def test_resolve_watchdog_tool_command_prefers_script_during_development(tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "recovery_watchdog.py").write_text("print('ok')\n", encoding="utf-8")
    exe_path = tmp_path / clip_recovery.WATCHDOG_EXECUTABLE_FILENAMES[0]
    exe_path.write_bytes(b"stub")

    assert clip_recovery.resolve_watchdog_tool_command(tmp_path) == [
        clip_recovery.sys.executable,
        str(scripts_dir / "recovery_watchdog.py"),
    ]


def test_resolve_watchdog_tool_command_uses_built_exe_when_frozen(tmp_path, monkeypatch):
    exe_path = tmp_path / clip_recovery.WATCHDOG_EXECUTABLE_FILENAMES[0]
    exe_path.write_bytes(b"stub")
    monkeypatch.setattr(clip_recovery.sys, "frozen", True, raising=False)

    assert clip_recovery.resolve_watchdog_tool_command(tmp_path) == [str(exe_path)]


def test_spawn_clip_watchdog_uses_watch_mode(monkeypatch, tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "recovery_watchdog.py").write_text("print('ok')\n", encoding="utf-8")

    captured = {}

    class DummyProc:
        pid = 4321

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return DummyProc()

    monkeypatch.setattr(clip_recovery.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(clip_recovery.sys, "executable", "python-test")

    proc = clip_recovery.spawn_clip_watchdog(777, root_dir=tmp_path)

    assert proc.pid == 4321
    assert captured["command"][:2] == ["python-test", str(scripts_dir / "recovery_watchdog.py")]
    assert captured["command"][2:] == ["--watch-parent", "777", "--poll-interval", "0.25", "--quiet"]
