"""Tests for runtime/app_update.py."""

from __future__ import annotations

import json
import calendar
import sys
from types import SimpleNamespace
from datetime import UTC, datetime, timedelta

from runtime.app_update import (
    AUTO_UPDATE_CHECK_INTERVAL_SEC,
    AppUpdateManager,
    build_relaunch_command,
    build_silent_install_command,
    cleanup_update_workspace,
    consume_remote_update_outcomes,
    run_update_handoff,
    seconds_until_next_update_check,
    write_remote_update_outcome,
)
from runtime.clip_recovery import CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW, DETACHED_PROCESS


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload
        self._offset = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self._payload) - self._offset
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


def test_build_relaunch_command_forces_tray_mode():
    command = build_relaunch_command(
        ["C:/Program Files/Multi Screen Pass/MultiScreenPass.exe"],
        ["--debug", "--console"],
        mode="tray",
    )

    assert command == [
        "C:/Program Files/Multi Screen Pass/MultiScreenPass.exe",
        "--debug",
        "--tray",
    ]


def test_build_silent_install_command_disables_restart_and_icons(tmp_path):
    installer = tmp_path / "MultiScreenPass-Setup-0.3.20.exe"
    install_dir = tmp_path / "Program Files" / "Multi Screen Pass"
    log_path = tmp_path / "install.log"

    command = build_silent_install_command(
        installer,
        install_dir=install_dir,
        log_path=log_path,
    )

    assert "/VERYSILENT" in command
    assert "/NORESTART" in command
    assert "/NOICONS" in command
    assert "/MERGETASKS=!desktopicon" in command
    assert f"/DIR={install_dir}" in command
    assert f"/LOG={log_path}" in command


def test_seconds_until_next_update_check_returns_zero_when_midnight_has_passed():
    now_epoch = calendar.timegm((2025, 4, 15, 0, 0, 0, 0, 0, 0))
    recent = "2025-04-14T12:00:00Z"

    remaining = seconds_until_next_update_check(recent, now_epoch_sec=now_epoch)

    assert remaining == 0


def test_seconds_until_next_update_check_targets_next_local_midnight():
    now = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
    now_epoch = now.timestamp()
    recent = datetime.fromtimestamp(now_epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    remaining = seconds_until_next_update_check(recent, now_epoch_sec=now_epoch)

    assert 0 < remaining < AUTO_UPDATE_CHECK_INTERVAL_SEC
    assert remaining == int((datetime.combine(now.date() + timedelta(days=1), datetime.min.time()) - now).total_seconds())


def test_app_update_manager_prepares_download_and_handoff(tmp_path):
    opened = []
    launched = {}

    def fake_urlopen(request, timeout):
        opened.append((request.full_url, timeout))
        return _FakeResponse(b"installer-bytes")

    def fake_popen(command, **kwargs):
        launched["command"] = list(command)
        launched["kwargs"] = kwargs
        return SimpleNamespace(pid=4321)

    root_dir = tmp_path / "repo"
    (root_dir / "scripts").mkdir(parents=True)
    manager = AppUpdateManager(
        root_dir=root_dir,
        install_dir=tmp_path / "installed-app",
        update_root=tmp_path / "updates",
        base_launch_command=[str(tmp_path / "installed-app" / "MultiScreenPass.exe")],
        runtime_args=["--debug"],
        current_pid=9876,
        urlopen_fn=fake_urlopen,
        popen_fn=fake_popen,
    )
    result = SimpleNamespace(
        latest_tag_name="v0.3.20",
        installer_url="https://example.com/download/MultiScreenPass-Setup-0.3.20.exe",
    )

    prepared = manager.prepare_update(result, relaunch_mode="tray")

    assert opened == [("https://example.com/download/MultiScreenPass-Setup-0.3.20.exe", 30.0)]
    assert prepared.installer_path.read_bytes() == b"installer-bytes"
    manifest = json.loads(prepared.manifest_path.read_text(encoding="utf-8"))
    assert manifest["wait_pid"] == 9876
    assert manifest["wait_process_names"] == [
        "MultiScreenPass.exe",
        "MultiScreenPassRecoveryWatchdog.exe",
    ]
    assert manifest["relaunch_command"] == [
        str(tmp_path / "installed-app" / "MultiScreenPass.exe"),
        "--debug",
        "--tray",
    ]
    assert launched["command"][:2] == [sys.executable, str(root_dir / "scripts" / "update_installer.py")]
    assert launched["command"][2:] == ["--manifest", str(prepared.manifest_path)]


def test_app_update_manager_embeds_remote_update_metadata(tmp_path):
    def fake_urlopen(_request, timeout=None, **_kwargs):
        return _FakeResponse(b"installer-bytes")

    def fake_popen(command, **kwargs):
        return SimpleNamespace(pid=4321, command=command, kwargs=kwargs)

    manager = AppUpdateManager(
        root_dir=tmp_path / "repo",
        install_dir=tmp_path / "installed-app",
        update_root=tmp_path / "updates",
        base_launch_command=[str(tmp_path / "installed-app" / "MultiScreenPass.exe")],
        current_pid=9876,
        urlopen_fn=fake_urlopen,
        popen_fn=fake_popen,
    )
    result = SimpleNamespace(
        latest_tag_name="v0.3.20",
        installer_url="https://example.com/download/MultiScreenPass-Setup-0.3.20.exe",
    )

    prepared = manager.prepare_update(
        result,
        relaunch_mode="tray",
        remote_update_requester_id="A",
        remote_update_target_id="B",
        remote_update_session_id="session-1",
        remote_update_current_version="0.3.19",
        remote_update_latest_version="0.3.20",
    )

    manifest = json.loads(prepared.manifest_path.read_text(encoding="utf-8"))
    assert manifest["remote_update_requester_id"] == "A"
    assert manifest["remote_update_target_id"] == "B"
    assert manifest["remote_update_session_id"] == "session-1"
    assert manifest["remote_update_current_version"] == "0.3.19"
    assert manifest["remote_update_latest_version"] == "0.3.20"


def test_run_update_handoff_waits_for_exit_then_relaunches(tmp_path):
    installer = tmp_path / "MultiScreenPass-Setup-0.3.20.exe"
    installer.write_bytes(b"stub")
    manifest_path = tmp_path / "update.json"
    manifest_path.write_text(
        json.dumps(
            {
                "wait_pid": 100,
                "wait_process_names": ["MultiScreenPassRecoveryWatchdog.exe"],
                "installer_path": str(installer),
                "install_dir": str(tmp_path / "installed-app"),
                "installer_log_path": str(tmp_path / "install.log"),
                "relaunch_command": ["C:/Program Files/Multi Screen Pass/MultiScreenPass.exe", "--tray"],
                "relaunch_cwd": str(tmp_path),
                "relaunch_on_failure": True,
                "update_root": str(tmp_path / "updates"),
                "remote_update_requester_id": "A",
                "remote_update_target_id": "B",
                "remote_update_session_id": "session-1",
                "remote_update_current_version": "0.3.19",
                "remote_update_latest_version": "0.3.20",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    alive_states = [True, False]
    process_states = [True, False]
    installer_commands = []
    relaunched = []

    exit_code = run_update_handoff(
        manifest_path,
        is_process_alive_fn=lambda _pid: alive_states.pop(0),
        process_name_running_fn=lambda _name: process_states.pop(0),
        sleep_fn=lambda _seconds: None,
        time_fn=lambda: 0.0,
        run_fn=lambda command, **kwargs: installer_commands.append((list(command), kwargs)) or SimpleNamespace(returncode=0),
        popen_fn=lambda command, **kwargs: relaunched.append((list(command), kwargs)) or SimpleNamespace(pid=55),
    )

    assert exit_code == 0
    assert installer_commands[0][0][0] == str(installer)
    assert "/VERYSILENT" in installer_commands[0][0]
    assert "/NORESTART" in installer_commands[0][0]
    assert "/NOICONS" in installer_commands[0][0]
    assert relaunched == [
        (
            ["C:/Program Files/Multi Screen Pass/MultiScreenPass.exe", "--tray"],
            {
                "stdin": -3,
                "stdout": -3,
                "stderr": -3,
                "creationflags": DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
                "close_fds": True,
                "cwd": str(tmp_path),
            },
        )
    ]
    outcomes = consume_remote_update_outcomes(update_root=tmp_path / "updates")
    assert len(outcomes) == 1
    assert outcomes[0]["requester_id"] == "A"
    assert outcomes[0]["target_id"] == "B"
    assert outcomes[0]["status"] == "completed"
    assert outcomes[0]["detail"] == ""
    assert outcomes[0]["event_id"]
    assert outcomes[0]["session_id"] == "session-1"
    assert outcomes[0]["current_version"] == "0.3.19"
    assert outcomes[0]["latest_version"] == "0.3.20"


def test_write_remote_update_outcome_keeps_distinct_events_with_same_timestamp(monkeypatch, tmp_path):
    monkeypatch.setattr("runtime.app_update.time.time", lambda: 1713427200.123)

    first = write_remote_update_outcome(
        tmp_path / "updates",
        requester_id="A",
        target_id="B",
        status="checking",
        event_id="evt-1",
    )
    second = write_remote_update_outcome(
        tmp_path / "updates",
        requester_id="A",
        target_id="B",
        status="downloading",
        event_id="evt-2",
    )

    assert first != second
    assert first.exists() is True
    assert second.exists() is True


def test_cleanup_update_workspace_removes_transient_update_artifacts(tmp_path):
    update_root = tmp_path / "updates"
    for dirname in ("downloads", "manifests", "tools"):
        target = update_root / dirname
        target.mkdir(parents=True)
        (target / "stale.txt").write_text("x", encoding="utf-8")
    logs = update_root / "logs"
    logs.mkdir(parents=True)
    for index in range(24):
        (logs / f"log-{index:02d}.log").write_text("log", encoding="utf-8")

    cleanup_update_workspace(update_root)

    assert not (update_root / "downloads").exists()
    assert not (update_root / "manifests").exists()
    assert not (update_root / "tools").exists()
    assert len(list(logs.glob("*.log"))) == 20
