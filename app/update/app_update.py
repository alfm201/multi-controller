"""Download and hand off application updates to a detached installer helper."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import Request
from uuid import uuid4

from app.meta.identity import (
    APP_EXECUTABLE_NAME,
    UPDATER_EXECUTABLE_NAME,
    WATCHDOG_EXECUTABLE_NAME,
)
from msp_platform.windows.clip_recovery import (
    CREATE_NEW_PROCESS_GROUP,
    CREATE_NO_WINDOW,
    DETACHED_PROCESS,
    is_process_alive,
)
from app.update.group_update import verify_cached_installer
from app.update.http_utils import open_url


AUTO_UPDATE_CHECK_INTERVAL_SEC = 24 * 60 * 60
UPDATE_DOWNLOAD_TIMEOUT_SEC = 30.0 * 60.0
UPDATE_DOWNLOAD_CHUNK_SIZE = 256 * 1024
UPDATE_PARENT_EXIT_TIMEOUT_SEC = 30.0
UPDATE_PROCESS_EXIT_TIMEOUT_SEC = 15.0
UPDATE_WAIT_POLL_INTERVAL_SEC = 0.25
REMOTE_UPDATE_OUTCOME_DIRNAME = "state"


@dataclass(frozen=True)
class PreparedUpdateInstall:
    installer_path: Path
    manifest_path: Path
    launcher_pid: int | None
    relaunch_mode: str


def get_update_root_dir(*, local_appdata: str | None = None) -> Path:
    root = local_appdata or os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if root:
        return Path(root) / APP_EXECUTABLE_NAME / "updates"
    return _project_root() / "build" / "updates"


def current_base_launch_command(*, root_dir: str | Path | None = None) -> list[str]:
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve())]
    root = Path(root_dir) if root_dir is not None else _project_root()
    return [sys.executable, str((root / "main.py").resolve())]


def current_install_dir(*, root_dir: str | Path | None = None) -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return (Path(root_dir) if root_dir is not None else _project_root()).resolve()


def build_relaunch_command(
    base_command: list[str] | tuple[str, ...],
    runtime_args: list[str] | tuple[str, ...],
    *,
    mode: str,
) -> list[str]:
    command = list(base_command)
    args = list(runtime_args)
    if mode == "tray":
        args = [arg for arg in args if arg not in {"--gui", "--console", "--tray"}]
        args.append("--tray")
    elif mode == "gui":
        args = [arg for arg in args if arg not in {"--gui", "--console", "--tray"}]
    elif mode != "preserve":
        raise ValueError(f"unsupported relaunch mode: {mode}")
    command.extend(args)
    return command


def download_update_installer(
    installer_url: str,
    *,
    destination_dir: str | Path | None = None,
    timeout_sec: float = UPDATE_DOWNLOAD_TIMEOUT_SEC,
    chunk_size: int = UPDATE_DOWNLOAD_CHUNK_SIZE,
    urlopen_fn=None,
    progress_callback=None,
    expected_sha256: str = "",
    expected_size_bytes: int = 0,
) -> Path:
    target_dir = Path(destination_dir) if destination_dir is not None else get_update_root_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = _installer_filename_from_url(installer_url)
    temp_path = target_dir / f"{filename}.part"
    final_path = target_dir / filename
    request = Request(
        installer_url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "multi-controller-updater",
        },
    )
    downloaded = 0
    with open_url(request, timeout_sec=timeout_sec, urlopen_fn=urlopen_fn) as response, temp_path.open(
        "wb"
    ) as handle:
        total_bytes = _content_length(response)
        _emit_update_download_progress(
            progress_callback,
            downloaded_bytes=downloaded,
            total_bytes=total_bytes,
        )
        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                break
            handle.write(chunk)
            downloaded += len(chunk)
            _emit_update_download_progress(
                progress_callback,
                downloaded_bytes=downloaded,
                total_bytes=total_bytes,
            )
    temp_path.replace(final_path)
    try:
        verify_cached_installer(
            final_path,
            expected_sha256=expected_sha256,
            expected_size=int(expected_size_bytes or 0),
        )
    except Exception:
        final_path.unlink(missing_ok=True)
        raise
    return final_path


def build_silent_install_command(
    installer_path: str | Path,
    *,
    install_dir: str | Path,
    log_path: str | Path | None = None,
) -> list[str]:
    command = [
        str(Path(installer_path)),
        "/SP-",
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NOCANCEL",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        "/FORCECLOSEAPPLICATIONS",
        "/NORESTARTAPPLICATIONS",
        "/NOICONS",
        "/MERGETASKS=!desktopicon",
        f"/DIR={Path(install_dir)}",
    ]
    if log_path is not None:
        command.append(f"/LOG={Path(log_path)}")
    return command


def resolve_update_handoff_command(root_dir: str | Path | None = None) -> list[str]:
    root = Path(root_dir) if root_dir is not None else _project_root()
    exe_path = root / f"{UPDATER_EXECUTABLE_NAME}.exe"
    script_path = root / "scripts" / "update_installer.py"
    if getattr(sys, "frozen", False) and exe_path.exists():
        return [str(exe_path)]
    if script_path.exists():
        return [sys.executable, str(script_path)]
    if exe_path.exists():
        return [str(exe_path)]
    return [sys.executable, str(script_path)]


def materialize_update_handoff_command(
    command: list[str] | tuple[str, ...],
    *,
    update_root: str | Path,
) -> list[str]:
    prepared = list(command)
    if len(prepared) != 1:
        return prepared
    source = Path(prepared[0])
    if source.suffix.lower() != ".exe" or not source.exists():
        return prepared
    tools_dir = Path(update_root) / "tools" / _timestamp_slug()
    tools_dir.mkdir(parents=True, exist_ok=True)
    copied = tools_dir / source.name
    shutil.copy2(source, copied)
    return [str(copied)]


def write_update_handoff_manifest(
    manifest: dict,
    *,
    update_root: str | Path | None = None,
) -> Path:
    root = Path(update_root) if update_root is not None else get_update_root_dir()
    manifest_dir = root / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"update-{_timestamp_slug()}.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def launch_update_handoff(
    manifest_path: str | Path,
    *,
    helper_command: list[str] | tuple[str, ...] | None = None,
    popen_fn=None,
) -> subprocess.Popen:
    launcher = subprocess.Popen if popen_fn is None else popen_fn
    command = list(helper_command or resolve_update_handoff_command())
    command.extend(["--manifest", str(Path(manifest_path))])
    return launcher(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        close_fds=True,
        cwd=str(Path(manifest_path).resolve().parent),
    )


def is_process_name_running(
    process_name: str,
    *,
    run_fn=None,
) -> bool:
    runner = subprocess.run if run_fn is None else run_fn
    completed = runner(
        ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=False,
        creationflags=CREATE_NO_WINDOW,
    )
    if getattr(completed, "returncode", 0) != 0:
        return False
    for row in csv.reader(line for line in completed.stdout.splitlines() if line.strip()):
        if row and row[0].strip().lower() == process_name.strip().lower():
            return True
    return False


def wait_for_pid_exit(
    pid: int | None,
    *,
    timeout_sec: float = UPDATE_PARENT_EXIT_TIMEOUT_SEC,
    poll_interval_sec: float = UPDATE_WAIT_POLL_INTERVAL_SEC,
    is_process_alive_fn=None,
    sleep_fn=None,
    time_fn=None,
) -> bool:
    if not pid:
        return True
    alive = is_process_alive if is_process_alive_fn is None else is_process_alive_fn
    sleeper = time.sleep if sleep_fn is None else sleep_fn
    monotonic = time.monotonic if time_fn is None else time_fn
    deadline = monotonic() + max(float(timeout_sec), 0.0)
    while alive(int(pid)):
        if monotonic() >= deadline:
            return False
        sleeper(max(float(poll_interval_sec), 0.01))
    return True


def wait_for_process_names_to_exit(
    process_names: list[str] | tuple[str, ...],
    *,
    timeout_sec: float = UPDATE_PROCESS_EXIT_TIMEOUT_SEC,
    poll_interval_sec: float = UPDATE_WAIT_POLL_INTERVAL_SEC,
    process_name_running_fn=None,
    sleep_fn=None,
    time_fn=None,
) -> bool:
    remaining = [name for name in process_names if name]
    if not remaining:
        return True
    is_running = is_process_name_running if process_name_running_fn is None else process_name_running_fn
    sleeper = time.sleep if sleep_fn is None else sleep_fn
    monotonic = time.monotonic if time_fn is None else time_fn
    deadline = monotonic() + max(float(timeout_sec), 0.0)
    while remaining:
        remaining = [name for name in remaining if is_running(name)]
        if not remaining:
            return True
        if monotonic() >= deadline:
            return False
        sleeper(max(float(poll_interval_sec), 0.01))
    return True


def run_update_handoff(
    manifest_path: str | Path,
    *,
    run_fn=None,
    popen_fn=None,
    is_process_alive_fn=None,
    process_name_running_fn=None,
    sleep_fn=None,
    time_fn=None,
) -> int:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    update_root = Path(manifest.get("update_root") or Path(manifest_path).resolve().parent.parent)
    wait_for_pid_exit(
        manifest.get("wait_pid"),
        is_process_alive_fn=is_process_alive_fn,
        sleep_fn=sleep_fn,
        time_fn=time_fn,
    )
    wait_for_process_names_to_exit(
        manifest.get("wait_process_names") or (),
        process_name_running_fn=process_name_running_fn,
        sleep_fn=sleep_fn,
        time_fn=time_fn,
    )
    installer_command = build_silent_install_command(
        manifest["installer_path"],
        install_dir=manifest["install_dir"],
        log_path=manifest.get("installer_log_path"),
    )
    runner = subprocess.run if run_fn is None else run_fn
    completed = runner(
        installer_command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        cwd=str(Path(manifest["installer_path"]).resolve().parent),
    )
    exit_code = int(getattr(completed, "returncode", 1))
    remote_requester_id = str(manifest.get("remote_update_requester_id") or "").strip()
    remote_target_id = str(manifest.get("remote_update_target_id") or "").strip()
    if remote_requester_id and remote_target_id:
        write_remote_update_outcome(
            update_root,
            requester_id=remote_requester_id,
            target_id=remote_target_id,
            status="completed" if exit_code == 0 else "failed",
            detail="" if exit_code == 0 else f"installer_exit_code={exit_code}",
            request_id=str(manifest.get("remote_update_request_id") or ""),
            event_id=uuid4().hex,
            session_id=str(manifest.get("remote_update_session_id") or ""),
            current_version=str(manifest.get("remote_update_current_version") or ""),
            latest_version=str(manifest.get("remote_update_latest_version") or ""),
        )
    should_relaunch = bool(manifest.get("relaunch_command")) and (
        exit_code == 0 or manifest.get("relaunch_on_failure", True)
    )
    if should_relaunch:
        launcher = subprocess.Popen if popen_fn is None else popen_fn
        launcher(
            list(manifest["relaunch_command"]),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
            close_fds=True,
            cwd=str(manifest.get("relaunch_cwd") or Path(manifest["install_dir"]).resolve()),
        )
    cleanup_update_workspace(update_root)
    return exit_code


def format_update_timestamp(epoch_sec: float | None = None) -> str:
    if epoch_sec is None:
        epoch_sec = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(epoch_sec)))


def seconds_until_next_update_check(
    last_checked_at: str | None,
    *,
    now_epoch_sec: float | None = None,
    interval_sec: int = AUTO_UPDATE_CHECK_INTERVAL_SEC,
) -> int:
    del interval_sec
    now_local = datetime.fromtimestamp(time.time() if now_epoch_sec is None else float(now_epoch_sec))
    next_midnight = datetime.combine(now_local.date() + timedelta(days=1), datetime.min.time())
    if not last_checked_at:
        return 0
    try:
        last_checked = datetime.strptime(last_checked_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return 0
    if last_checked.astimezone().date() != now_local.date():
        return 0
    return max(int((next_midnight - now_local).total_seconds()), 0)


class AppUpdateManager:
    def __init__(
        self,
        *,
        root_dir: str | Path | None = None,
        install_dir: str | Path | None = None,
        update_root: str | Path | None = None,
        base_launch_command: list[str] | tuple[str, ...] | None = None,
        runtime_args: list[str] | tuple[str, ...] | None = None,
        current_pid: int | None = None,
        urlopen_fn=None,
        popen_fn=None,
    ):
        self.root_dir = (Path(root_dir) if root_dir is not None else _project_root()).resolve()
        self.install_dir = (
            Path(install_dir) if install_dir is not None else current_install_dir(root_dir=self.root_dir)
        ).resolve()
        self.update_root = (
            Path(update_root) if update_root is not None else get_update_root_dir()
        ).resolve()
        self.base_launch_command = list(
            base_launch_command if base_launch_command is not None else current_base_launch_command(root_dir=self.root_dir)
        )
        self.runtime_args = list(sys.argv[1:] if runtime_args is None else runtime_args)
        self.current_pid = int(os.getpid() if current_pid is None else current_pid)
        self.urlopen_fn = urlopen_fn
        self.popen_fn = popen_fn

    def prepare_update(
        self,
        result,
        *,
        relaunch_mode: str,
        progress_callback=None,
        installer_url_override: str | None = None,
        expected_sha256: str = "",
        expected_size_bytes: int = 0,
        remote_update_requester_id: str | None = None,
        remote_update_target_id: str | None = None,
        remote_update_request_id: str | None = None,
        remote_update_session_id: str | None = None,
        remote_update_current_version: str | None = None,
        remote_update_latest_version: str | None = None,
    ) -> PreparedUpdateInstall:
        installer_url = str(installer_url_override or getattr(result, "installer_url", "") or "").strip()
        if not installer_url:
            raise ValueError("업데이트 설치 파일 주소를 찾을 수 없습니다.")
        tag_name = str(getattr(result, "latest_tag_name", "") or "latest")
        download_dir = self.update_root / "downloads" / _safe_path_segment(tag_name)
        installer_path = download_update_installer(
            installer_url,
            destination_dir=download_dir,
            urlopen_fn=self.urlopen_fn,
            progress_callback=progress_callback,
            expected_sha256=expected_sha256,
            expected_size_bytes=expected_size_bytes,
        )
        relaunch_command = build_relaunch_command(
            self.base_launch_command,
            self.runtime_args,
            mode=relaunch_mode,
        )
        logs_dir = self.update_root / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "wait_pid": self.current_pid,
            "wait_process_names": self._wait_process_names(),
            "installer_path": str(installer_path),
            "install_dir": str(self.install_dir),
            "installer_log_path": str(logs_dir / f"install-{_timestamp_slug()}.log"),
            "relaunch_command": relaunch_command,
            "relaunch_cwd": str(self.install_dir if self._is_executable_launch() else self.root_dir),
            "relaunch_on_failure": True,
            "update_root": str(self.update_root),
            "remote_update_requester_id": str(remote_update_requester_id or ""),
            "remote_update_target_id": str(remote_update_target_id or ""),
            "remote_update_request_id": str(remote_update_request_id or ""),
            "remote_update_session_id": str(remote_update_session_id or ""),
            "remote_update_current_version": str(remote_update_current_version or ""),
            "remote_update_latest_version": str(remote_update_latest_version or ""),
        }
        manifest_path = write_update_handoff_manifest(manifest, update_root=self.update_root)
        helper_root = (
            self.install_dir
            if getattr(sys, "frozen", False) and self._is_executable_launch()
            else self.root_dir
        )
        helper_command = materialize_update_handoff_command(
            resolve_update_handoff_command(helper_root),
            update_root=self.update_root,
        )
        proc = launch_update_handoff(
            manifest_path,
            helper_command=helper_command,
            popen_fn=self.popen_fn,
        )
        logging.info(
            "[UPDATE] prepared installer=%s manifest=%s launcher_pid=%s mode=%s",
            installer_path,
            manifest_path,
            getattr(proc, "pid", None),
            relaunch_mode,
        )
        return PreparedUpdateInstall(
            installer_path=installer_path,
            manifest_path=manifest_path,
            launcher_pid=getattr(proc, "pid", None),
            relaunch_mode=relaunch_mode,
        )

    def _is_executable_launch(self) -> bool:
        return bool(self.base_launch_command) and Path(self.base_launch_command[0]).suffix.lower() == ".exe"

    def _wait_process_names(self) -> list[str]:
        if not self._is_executable_launch():
            return []
        return [
            f"{APP_EXECUTABLE_NAME}.exe",
            f"{WATCHDOG_EXECUTABLE_NAME}.exe",
        ]


def cleanup_update_workspace(update_root: str | Path) -> None:
    root = Path(update_root)
    for dirname in ("downloads", "manifests", "tools"):
        target = root / dirname
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
    logs_dir = root / "logs"
    if not logs_dir.exists():
        return
    log_files = sorted(
        (path for path in logs_dir.glob("*.log") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stale in log_files[20:]:
        try:
            stale.unlink()
        except OSError:
            continue


def write_remote_update_outcome(
    update_root: str | Path,
    *,
    requester_id: str,
    target_id: str,
    status: str,
    detail: str = "",
    request_id: str = "",
    event_id: str = "",
    session_id: str = "",
    current_version: str = "",
    latest_version: str = "",
) -> Path:
    outcome_dir = Path(update_root) / REMOTE_UPDATE_OUTCOME_DIRNAME
    outcome_dir.mkdir(parents=True, exist_ok=True)
    outcome_key = str(event_id or uuid4().hex)
    outcome_path = outcome_dir / f"remote-update-{_timestamp_slug()}-{outcome_key}.json"
    outcome_path.write_text(
        json.dumps(
            {
                "requester_id": str(requester_id or ""),
                "target_id": str(target_id or ""),
                "status": str(status or ""),
                "detail": str(detail or ""),
                "request_id": str(request_id or ""),
                "event_id": str(event_id or ""),
                "session_id": str(session_id or ""),
                "current_version": str(current_version or ""),
                "latest_version": str(latest_version or ""),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return outcome_path


def read_remote_update_outcomes(
    *,
    update_root: str | Path | None = None,
) -> list[tuple[Path, dict[str, str]]]:
    root = Path(update_root) if update_root is not None else get_update_root_dir()
    outcome_dir = root / REMOTE_UPDATE_OUTCOME_DIRNAME
    if not outcome_dir.exists():
        return []
    outcomes: list[tuple[Path, dict[str, str]]] = []
    for path in sorted(outcome_dir.glob("remote-update-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if not isinstance(payload, dict):
            try:
                path.unlink()
            except OSError:
                pass
            continue
        outcomes.append(
            (
                path,
                {
                    "requester_id": str(payload.get("requester_id") or ""),
                    "target_id": str(payload.get("target_id") or ""),
                    "status": str(payload.get("status") or ""),
                    "detail": str(payload.get("detail") or ""),
                    "request_id": str(payload.get("request_id") or ""),
                    "event_id": str(payload.get("event_id") or ""),
                    "session_id": str(payload.get("session_id") or ""),
                    "current_version": str(payload.get("current_version") or ""),
                    "latest_version": str(payload.get("latest_version") or ""),
                },
            )
        )
    return outcomes


def consume_remote_update_outcomes(
    *,
    update_root: str | Path | None = None,
) -> list[dict[str, str]]:
    outcomes = read_remote_update_outcomes(update_root=update_root)
    consumed: list[dict[str, str]] = []
    for path, payload in outcomes:
        consumed.append(payload)
        try:
            path.unlink()
        except OSError:
            pass
    return consumed
def _installer_filename_from_url(installer_url: str) -> str:
    filename = Path(urlsplit(installer_url).path).name or f"{APP_EXECUTABLE_NAME}-Setup.exe"
    if not filename.lower().endswith(".exe"):
        filename = f"{APP_EXECUTABLE_NAME}-Setup.exe"
    return filename


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _safe_path_segment(value: str) -> str:
    sanitized = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value.strip())
    return sanitized.strip(".-") or "latest"


def _timestamp_slug() -> str:
    epoch = time.time()
    seconds = int(epoch)
    millis = int((epoch - seconds) * 1000)
    return f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(seconds))}-{millis:03d}"


def _content_length(response) -> int | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get("Content-Length")
    if value is None:
        return None
    try:
        size = int(value)
    except (TypeError, ValueError):
        return None
    return size if size > 0 else None


def _emit_update_download_progress(progress_callback, *, downloaded_bytes: int, total_bytes: int | None) -> None:
    if progress_callback is None:
        return
    progress: int | None = None
    if total_bytes:
        progress = max(0, min(int((downloaded_bytes / total_bytes) * 100), 100))
    progress_callback(progress, downloaded_bytes, total_bytes)
