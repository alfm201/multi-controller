"""Helpers for coordinator-mediated update checks and shared installer delivery."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlsplit
from uuid import uuid4

from app.update.app_version import UpdateCheckResult


GROUP_UPDATE_QUERY_CACHE_TTL_SEC = 30.0
GROUP_UPDATE_SHARE_TTL_SEC = 15 * 60.0


def serialize_update_check_result(result: UpdateCheckResult) -> dict[str, str]:
    payload = asdict(result)
    return {key: "" if value is None else str(value) for key, value in payload.items()}


def deserialize_update_check_result(payload: dict | None) -> UpdateCheckResult | None:
    if not isinstance(payload, dict):
        return None
    try:
        return UpdateCheckResult(
            current_version=str(payload.get("current_version") or ""),
            latest_version=str(payload.get("latest_version") or ""),
            latest_tag_name=str(payload.get("latest_tag_name") or ""),
            release_url=_optional_text(payload.get("release_url")),
            installer_url=_optional_text(payload.get("installer_url")),
            status=str(payload.get("status") or ""),
        )
    except Exception:
        return None


def build_update_cache_key(*, tag_name: str, installer_url: str) -> str:
    return f"{str(tag_name or '').strip()}|{str(installer_url or '').strip()}"


def build_cached_installer_dir(update_root: str | Path, *, tag_name: str) -> Path:
    root = Path(update_root)
    safe_tag = _safe_path_segment(str(tag_name or "latest"))
    return root / "cache" / safe_tag


def build_cached_installer_path(update_root: str | Path, *, tag_name: str, installer_url: str) -> Path:
    cache_dir = build_cached_installer_dir(update_root, tag_name=tag_name)
    filename = _installer_filename_from_url(installer_url)
    return cache_dir / filename


def compute_file_sha256(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_cached_installer(path: str | Path, *, expected_sha256: str = "", expected_size: int = 0) -> None:
    resolved = Path(path)
    if expected_size and resolved.stat().st_size != int(expected_size):
        raise ValueError("cached installer size mismatch")
    if expected_sha256:
        actual_hash = compute_file_sha256(resolved)
        if actual_hash.lower() != str(expected_sha256).strip().lower():
            raise ValueError("cached installer sha256 mismatch")


def build_shared_installer_url(host: str, port: int, share_id: str, token: str) -> str:
    safe_host = str(host or "").strip() or "127.0.0.1"
    return f"http://{safe_host}:{int(port)}/installer/{quote(str(share_id))}?token={quote(str(token))}"


class InstallerShareManager:
    def __init__(
        self,
        *,
        ttl_sec: float = GROUP_UPDATE_SHARE_TTL_SEC,
        stream_chunk_size: int = 256 * 1024,
        stream_delay_sec: float = 0.0,
    ):
        self._ttl_sec = float(ttl_sec)
        self._lock = threading.Lock()
        self._transfer_condition = threading.Condition()
        self._next_transfer_ticket = 0
        self._active_transfer_ticket = 0
        self._stream_chunk_size = max(1, int(stream_chunk_size))
        self._stream_delay_sec = max(0.0, float(stream_delay_sec))
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._shares: dict[str, dict[str, object]] = {}

    def share_file(
        self,
        file_path: str | Path,
        *,
        sha256: str,
        size_bytes: int,
    ) -> dict[str, object]:
        resolved = Path(file_path).resolve()
        if not resolved.is_file():
            raise FileNotFoundError(resolved)
        with self._lock:
            self._ensure_server_locked()
            self._purge_expired_locked()
            share_id = uuid4().hex
            token = uuid4().hex
            self._shares[share_id] = {
                "path": resolved,
                "token": token,
                "sha256": str(sha256 or ""),
                "size_bytes": int(size_bytes),
                "expires_at": time.monotonic() + self._ttl_sec,
            }
            assert self._server is not None
            return {
                "share_id": share_id,
                "share_token": token,
                "share_port": int(self._server.server_port),
                "sha256": str(sha256 or ""),
                "size_bytes": int(size_bytes),
            }

    def get_share(self, share_id: str) -> dict[str, object] | None:
        with self._lock:
            self._purge_expired_locked()
            share = self._shares.get(str(share_id or ""))
            if share is None:
                return None
            return dict(share)

    def close(self) -> None:
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
            self._shares.clear()
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=1.0)

    def _ensure_server_locked(self) -> None:
        if self._server is not None:
            return

        manager = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                manager._handle_get(self)

            def log_message(self, _format, *_args):  # noqa: A003
                return

        server = ThreadingHTTPServer(("0.0.0.0", 0), _Handler)
        thread = threading.Thread(
            target=server.serve_forever,
            daemon=True,
            name="update-share-http",
        )
        thread.start()
        self._server = server
        self._thread = thread

    def _purge_expired_locked(self) -> None:
        now = time.monotonic()
        expired = [
            share_id
            for share_id, share in self._shares.items()
            if float(share.get("expires_at", 0.0)) <= now
        ]
        for share_id in expired:
            self._shares.pop(share_id, None)

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        split = urlsplit(handler.path)
        parts = [part for part in split.path.split("/") if part]
        if len(parts) != 2 or parts[0] != "installer":
            handler.send_error(404)
            return
        share_id = parts[1]
        params = parse_qs(split.query or "")
        token = str(params.get("token", [""])[0] or "")
        share = self.get_share(share_id)
        if share is None:
            handler.send_error(404)
            return
        if token != str(share.get("token") or ""):
            handler.send_error(403)
            return
        path = Path(str(share["path"]))
        ticket = self._acquire_transfer_ticket()
        try:
            size_bytes = int(share.get("size_bytes") or path.stat().st_size)
            handler.send_response(200)
            handler.send_header("Content-Type", "application/octet-stream")
            handler.send_header("Content-Length", str(size_bytes))
            handler.send_header("Cache-Control", "no-store")
            handler.end_headers()
            with path.open("rb") as file_handle:
                while True:
                    chunk = file_handle.read(self._stream_chunk_size)
                    if not chunk:
                        break
                    handler.wfile.write(chunk)
                    if self._stream_delay_sec > 0:
                        time.sleep(self._stream_delay_sec)
        except BrokenPipeError:
            return
        except OSError:
            handler.send_error(500)
        finally:
            self._release_transfer_ticket(ticket)

    def _acquire_transfer_ticket(self) -> int:
        with self._transfer_condition:
            ticket = self._next_transfer_ticket
            self._next_transfer_ticket += 1
            while ticket != self._active_transfer_ticket:
                self._transfer_condition.wait()
            return ticket

    def _release_transfer_ticket(self, ticket: int) -> None:
        with self._transfer_condition:
            if ticket != self._active_transfer_ticket:
                return
            self._active_transfer_ticket += 1
            self._transfer_condition.notify_all()


def ensure_cached_installer(
    update_root: str | Path,
    *,
    tag_name: str,
    installer_url: str,
    urlopen_fn=None,
    progress_callback=None,
):
    from app.update.app_update import download_update_installer

    cache_path = build_cached_installer_path(update_root, tag_name=tag_name, installer_url=installer_url)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if not cache_path.exists():
        download_update_installer(
            installer_url,
            destination_dir=cache_path.parent,
            urlopen_fn=urlopen_fn,
            progress_callback=progress_callback,
        )
    size_bytes = int(cache_path.stat().st_size)
    sha256 = compute_file_sha256(cache_path)
    return SimpleNamespace(
        path=cache_path,
        size_bytes=size_bytes,
        sha256=sha256,
    )


def _safe_path_segment(value: str) -> str:
    sanitized = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in str(value or "").strip())
    return sanitized.strip(".-") or "latest"


def _installer_filename_from_url(installer_url: str) -> str:
    filename = Path(urlsplit(str(installer_url or "")).path).name or "MultiScreenPass-Setup.exe"
    if not filename.lower().endswith(".exe"):
        filename = "MultiScreenPass-Setup.exe"
    return filename


def _optional_text(value) -> str | None:
    text = str(value or "").strip()
    return text or None
