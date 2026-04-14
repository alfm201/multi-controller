"""Download and hand off application updates to a detached installer helper."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import calendar
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from runtime.app_identity import (
    APP_EXECUTABLE_NAME,
    UPDATER_EXECUTABLE_NAME,
    WATCHDOG_EXECUTABLE_NAME,
)
from runtime.clip_recovery import (
    CREATE_NEW_PROCESS_GROUP,
    CREATE_NO_WINDOW,
    DETACHED_PROCESS,
    is_process_alive,
)


AUTO_UPDATE_CHECK_INTERVAL_SEC = 24 * 60 * 60
UPDATE_DOWNLOAD_TIMEOUT_SEC = 30.0
UPDATE_DOWNLOAD_CHUNK_SIZE = 256 * 1024
UPDATE_PARENT_EXIT_TIMEOUT_SEC = 30.0
UPDATE_PROCESS_EXIT_TIMEOUT_SEC = 15.0
UPDATE_WAIT_POLL_INTERVAL_SEC = 0.25


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
) -> Path:
    target_dir = Path(destination_dir) if destination_dir is not None else get_update_root_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = _installer_filename_from_url(installer_url)
    temp_path = target_dir / f"{filename}.part"
    final_path = target_dir / filename
    opener = urlopen if urlopen_fn is None else urlopen_fn
    request = Request(
        installer_url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "multi-controller-updater",
        },
    )
    with opener(request, timeout=timeout_sec) as response, temp_path.open("wb") as handle:
        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                break
            handle.write(chunk)
    temp_path.replace(final_path)
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
    if not last_checked_at:
        return 0
    now_value = time.time() if now_epoch_sec is None else float(now_epoch_sec)
    try:
        last_struct = time.strptime(last_checked_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return 0
    elapsed = max(int(now_value - calendar.timegm(last_struct)), 0)
    return max(int(interval_sec) - elapsed, 0)


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
    ) -> PreparedUpdateInstall:
        installer_url = str(getattr(result, "installer_url", "") or "").strip()
        if not installer_url:
            raise ValueError("업데이트 설치 파일 주소를 찾을 수 없습니다.")
        tag_name = str(getattr(result, "latest_tag_name", "") or "latest")
        download_dir = self.update_root / "downloads" / _safe_path_segment(tag_name)
        installer_path = download_update_installer(
            installer_url,
            destination_dir=download_dir,
            urlopen_fn=self.urlopen_fn,
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
    return time.strftime("%Y%m%d-%H%M%S", time.localtime())
