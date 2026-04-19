"""Shared HTTPS helpers with certificate fallback for packaged builds."""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

try:
    import certifi  # type: ignore
except Exception:
    certifi = None


class WindowsNativeRequestError(OSError):
    """Windows native fallback failure with preserved transport/status context."""

    def __init__(
        self,
        message: str,
        *,
        failure_kind: str,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_kind = str(failure_kind)
        self.status_code = None if status_code is None else int(status_code)


def resolve_certifi_bundle() -> str | None:
    bundle = _existing_ca_bundle_from_env()
    if bundle is not None:
        return bundle

    candidates: list[Path] = []
    if certifi is not None:
        try:
            candidates.append(Path(certifi.where()))
        except Exception:
            pass

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        base = Path(meipass)
        candidates.extend(
            [
                base / "certifi" / "cacert.pem",
                base / "_internal" / "certifi" / "cacert.pem",
                base / "cacert.pem",
            ]
        )

    exe_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    candidates.extend(
        [
            exe_dir / "certifi" / "cacert.pem",
            exe_dir / "_internal" / "certifi" / "cacert.pem",
            exe_dir / "cacert.pem",
        ]
    )

    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return None


def configure_ca_bundle_env() -> str | None:
    bundle = resolve_certifi_bundle()
    if bundle is None:
        return None
    os.environ.setdefault("SSL_CERT_FILE", bundle)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", bundle)
    os.environ.setdefault("CURL_CA_BUNDLE", bundle)
    return bundle


def create_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    try:
        bundle = configure_ca_bundle_env()
        if bundle is not None:
            context.load_verify_locations(bundle)
        else:
            context.load_default_certs()
    except Exception:
        context.load_default_certs()
    return context


def open_url(request, *, timeout_sec: float, urlopen_fn=None):
    opener = urlopen if urlopen_fn is None else urlopen_fn
    context = create_ssl_context()
    try:
        return opener(request, timeout=timeout_sec, context=context)
    except TypeError:
        try:
            return opener(request, timeout=timeout_sec)
        except Exception as exc:
            if _should_use_windows_native_fallback(exc, urlopen_fn=urlopen_fn):
                return _open_url_windows_native(request, timeout_sec=timeout_sec)
            raise
    except Exception as exc:
        if _should_use_windows_native_fallback(exc, urlopen_fn=urlopen_fn):
            return _open_url_windows_native(request, timeout_sec=timeout_sec)
        raise


def _existing_ca_bundle_from_env() -> str | None:
    for name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        value = str(os.environ.get(name) or "").strip()
        if value and Path(value).is_file():
            return value
    return None


def _should_use_windows_native_fallback(exc: Exception, *, urlopen_fn) -> bool:
    if urlopen_fn is not None or sys.platform != "win32":
        return False
    return _is_tls_or_connection_failure(exc)


def _is_tls_or_connection_failure(exc: Exception) -> bool:
    if isinstance(exc, HTTPError):
        return False
    if isinstance(exc, URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, Exception):
            return _is_tls_or_connection_failure(reason)
        text = str(reason or exc).lower()
        return _looks_like_tls_or_connection_failure(text)
    if isinstance(exc, (ssl.SSLError, ConnectionError, TimeoutError, OSError)):
        return _looks_like_tls_or_connection_failure(str(exc).lower())
    return False


def _looks_like_tls_or_connection_failure(text: str) -> bool:
    markers = (
        "ssl",
        "tls",
        "certificate",
        "handshake",
        "connection was reset",
        "connection reset",
        "connection aborted",
        "unexpected eof",
    )
    return any(marker in text for marker in markers)


def _open_url_windows_native(request, *, timeout_sec: float):
    if getattr(request, "data", None):
        raise ValueError("Windows native fallback only supports requests without a body.")

    body_file = tempfile.NamedTemporaryFile(prefix="mc-http-", suffix=".bin", delete=False)
    body_path = Path(body_file.name)
    body_file.close()
    command = _build_windows_native_command(
        timeout_sec=timeout_sec,
    )
    env = os.environ.copy()
    env.update(
        {
            "MC_HTTP_URL": request.full_url,
            "MC_HTTP_HEADERS": json.dumps(dict(request.header_items()), ensure_ascii=True),
            "MC_HTTP_METHOD": request.get_method(),
            "MC_HTTP_TIMEOUT": str(timeout_sec),
            "MC_HTTP_OUTPUT": str(body_path),
        }
    )
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        creationflags=_windows_subprocess_creationflags(),
        env=env,
    )
    if completed.returncode != 0:
        _safe_unlink(body_path)
        message = completed.stderr.strip() or completed.stdout.strip() or "Windows native request failed."
        raise WindowsNativeRequestError(
            message,
            failure_kind=_classify_windows_native_failure(message),
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        _safe_unlink(body_path)
        raise WindowsNativeRequestError(
            "Windows native request returned invalid metadata.",
            failure_kind="invalid_metadata",
        ) from exc
    try:
        status_code = int(payload["status_code"])
    except (KeyError, TypeError, ValueError) as exc:
        _safe_unlink(body_path)
        raise WindowsNativeRequestError(
            "Windows native request returned invalid HTTP status.",
            failure_kind="invalid_http_status",
        ) from exc
    if status_code < 100 or status_code > 599:
        _safe_unlink(body_path)
        raise WindowsNativeRequestError(
            f"Windows native request returned invalid HTTP status: {status_code}",
            failure_kind="invalid_http_status",
            status_code=status_code,
        )
    if status_code < 200 or status_code >= 300:
        _safe_unlink(body_path)
        raise WindowsNativeRequestError(
            f"Windows native request returned unexpected HTTP status: {status_code}",
            failure_kind="unexpected_http_status",
            status_code=status_code,
        )
    logging.info("[HTTP] Windows native fallback succeeded url=%s status=%s", request.full_url, status_code)
    return _WindowsNativeResponse(
        body_path=body_path,
        headers=payload.get("headers") or {},
        status_code=status_code,
    )


def _build_windows_native_command(
    *,
    timeout_sec: float,
) -> list[str]:
    script = (
        "$ErrorActionPreference='Stop';"
        "$ProgressPreference='SilentlyContinue';"
        "try{"
        "$headers=@{};"
        "if($env:MC_HTTP_HEADERS){"
        "$headersJson=ConvertFrom-Json $env:MC_HTTP_HEADERS;"
        "if($headersJson){"
        "foreach($prop in $headersJson.PSObject.Properties){$headers[$prop.Name]=[string]$prop.Value}"
        "}"
        "};"
        "$resp=Invoke-WebRequest -Uri $env:MC_HTTP_URL -Headers $headers -Method $env:MC_HTTP_METHOD "
        "-TimeoutSec ([int][Math]::Ceiling([double]$env:MC_HTTP_TIMEOUT)) -UseBasicParsing -OutFile $env:MC_HTTP_OUTPUT -PassThru;"
        "$statusCode=$null;"
        "if($resp -and $resp.PSObject.Properties.Match('StatusCode').Count -gt 0){$statusCode=$resp.StatusCode};"
        "if($null -eq $statusCode -or [string]::IsNullOrWhiteSpace([string]$statusCode)){throw 'Windows native request did not expose an HTTP status code.'};"
        "$statusCodeInt=0;"
        "if(-not [int]::TryParse([string]$statusCode,[ref]$statusCodeInt)){throw ('Windows native request returned a non-integer HTTP status: ' + [string]$statusCode)};"
        "$payload=@{status_code=$statusCodeInt;headers=@{}};"
        "foreach($name in $resp.Headers.Keys){"
        "$headerValue=$resp.Headers[$name];"
        "if($null -eq $headerValue){$payload.headers[$name]=@()}"
        "elseif($headerValue -is [System.Array]){$payload.headers[$name]=@($headerValue | ForEach-Object {[string]$_})}"
        "else{$payload.headers[$name]=@([string]$headerValue)}"
        "};"
        "$payload|ConvertTo-Json -Compress -Depth 6;"
        "}catch{[Console]::Error.WriteLine($_.Exception.Message);exit 1}"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        encoded,
    ]


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _windows_subprocess_creationflags() -> int:
    if sys.platform != "win32":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _classify_windows_native_failure(message: str) -> str:
    text = str(message or "").strip().lower()
    if not text:
        return "transport_failure"
    if "did not expose an http status code" in text:
        return "missing_http_status"
    if "non-integer http status" in text or "invalid http status" in text:
        return "invalid_http_status"
    return "transport_failure"


class _HeaderMap:
    def __init__(self, headers: dict[str, list[str] | str]):
        self._headers = {str(name): value for name, value in headers.items()}

    def get(self, name: str, default=None):
        for header_name, value in self._headers.items():
            if header_name.lower() != name.lower():
                continue
            if isinstance(value, (list, tuple)):
                return ", ".join(str(item) for item in value)
            return str(value)
        return default


class _WindowsNativeResponse:
    def __init__(self, *, body_path: Path, headers: dict[str, list[str] | str], status_code: int):
        self._body_path = Path(body_path)
        self._handle = None
        self.headers = _HeaderMap(headers)
        self.status = status_code

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def read(self, size: int = -1) -> bytes:
        if self._handle is None:
            self._handle = self._body_path.open("rb")
        return self._handle.read(size)

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
        _safe_unlink(self._body_path)
