"""Tests for runtime/http_utils.py."""

from __future__ import annotations

import base64
from pathlib import Path
import ssl
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from runtime import http_utils


def test_resolve_certifi_bundle_uses_env_bundle(tmp_path, monkeypatch):
    bundle = tmp_path / "env-cacert.pem"
    bundle.write_text("test", encoding="utf-8")
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)

    assert http_utils.resolve_certifi_bundle() == str(bundle)


def test_resolve_certifi_bundle_falls_back_to_meipass_when_certifi_where_fails(tmp_path, monkeypatch):
    bundle = tmp_path / "certifi" / "cacert.pem"
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_text("test", encoding="utf-8")

    class BrokenCertifi:
        @staticmethod
        def where():
            raise RuntimeError("broken certifi")

    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)
    monkeypatch.setattr(http_utils, "certifi", BrokenCertifi())
    monkeypatch.setattr(http_utils.sys, "_MEIPASS", str(tmp_path), raising=False)

    assert http_utils.resolve_certifi_bundle() == str(bundle)


def test_configure_ca_bundle_env_sets_default_bundle(tmp_path, monkeypatch):
    bundle = tmp_path / "certifi" / "cacert.pem"
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_text("test", encoding="utf-8")

    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)
    monkeypatch.setattr(http_utils, "resolve_certifi_bundle", lambda: str(bundle))

    resolved = http_utils.configure_ca_bundle_env()

    assert resolved == str(bundle)
    assert http_utils.os.environ["SSL_CERT_FILE"] == str(bundle)
    assert http_utils.os.environ["REQUESTS_CA_BUNDLE"] == str(bundle)
    assert http_utils.os.environ["CURL_CA_BUNDLE"] == str(bundle)


def test_open_url_falls_back_to_windows_native_request(tmp_path, monkeypatch):
    request = Request("https://api.github.com", headers={"User-Agent": "x"})
    captured = {}

    def fake_urlopen(_request, timeout=0, context=None):
        raise URLError(ssl.SSLError("certificate verify failed"))

    def fake_run(command, **kwargs):
        output_path = Path(kwargs["env"]["MC_HTTP_OUTPUT"])
        output_path.write_bytes(b"ok")
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout='{"status_code":200,"headers":{"Content-Length":["2"]}}',
            stderr="",
        )

    monkeypatch.setattr(http_utils, "urlopen", fake_urlopen)
    monkeypatch.setattr(http_utils.subprocess, "run", fake_run)
    monkeypatch.setattr(http_utils.sys, "platform", "win32")
    monkeypatch.setattr(http_utils.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    with http_utils.open_url(request, timeout_sec=5.0) as response:
        assert response.read() == b"ok"
        assert response.headers.get("Content-Length") == "2"

    assert captured["command"][0] == "powershell.exe"
    assert "-EncodedCommand" in captured["command"]
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["creationflags"] == 0x08000000
    assert captured["kwargs"]["env"]["MC_HTTP_URL"] == "https://api.github.com"
    assert Path(captured["kwargs"]["env"]["MC_HTTP_OUTPUT"]).exists() is False


def test_open_url_does_not_fallback_for_http_error(monkeypatch):
    request = Request("https://api.github.com", headers={"User-Agent": "x"})

    def fake_urlopen(_request, timeout=0, context=None):
        raise HTTPError(
            url=request.full_url,
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=None,
        )

    fallback_called = {"value": False}

    monkeypatch.setattr(http_utils, "urlopen", fake_urlopen)
    monkeypatch.setattr(http_utils.sys, "platform", "win32")
    monkeypatch.setattr(
        http_utils.subprocess,
        "run",
        lambda *args, **kwargs: fallback_called.update(value=True),
    )

    with pytest.raises(HTTPError):
        http_utils.open_url(request, timeout_sec=5.0)

    assert fallback_called["value"] is False


def test_open_url_rejects_windows_native_zero_status(tmp_path, monkeypatch):
    request = Request("https://api.github.com", headers={"User-Agent": "x"})
    captured = {}

    def fake_urlopen(_request, timeout=0, context=None):
        raise URLError(ssl.SSLError("certificate verify failed"))

    def fake_run(command, **kwargs):
        output_path = Path(kwargs["env"]["MC_HTTP_OUTPUT"])
        output_path.write_bytes(b'{"tag_name":"v0.3.48"}')
        captured["output_path"] = output_path
        return SimpleNamespace(
            returncode=0,
            stdout='{"status_code":0,"headers":{"Content-Length":["22"]}}',
            stderr="",
        )

    monkeypatch.setattr(http_utils, "urlopen", fake_urlopen)
    monkeypatch.setattr(http_utils.subprocess, "run", fake_run)
    monkeypatch.setattr(http_utils.sys, "platform", "win32")
    monkeypatch.setattr(http_utils.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    with pytest.raises(http_utils.WindowsNativeRequestError, match="invalid HTTP status: 0") as exc_info:
        http_utils.open_url(request, timeout_sec=5.0)

    assert exc_info.value.failure_kind == "invalid_http_status"
    assert exc_info.value.status_code == 0
    assert captured["output_path"].exists() is False


def test_open_url_surfaces_windows_native_missing_status_as_transport_failure(tmp_path, monkeypatch):
    request = Request("https://api.github.com", headers={"User-Agent": "x"})

    def fake_urlopen(_request, timeout=0, context=None):
        raise URLError(ssl.SSLError("certificate verify failed"))

    def fake_run(command, **kwargs):
        output_path = Path(kwargs["env"]["MC_HTTP_OUTPUT"])
        output_path.write_bytes(b"")
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Windows native request did not expose an HTTP status code.\n",
        )

    monkeypatch.setattr(http_utils, "urlopen", fake_urlopen)
    monkeypatch.setattr(http_utils.subprocess, "run", fake_run)
    monkeypatch.setattr(http_utils.sys, "platform", "win32")
    monkeypatch.setattr(http_utils.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    with pytest.raises(http_utils.WindowsNativeRequestError, match="did not expose an HTTP status code") as exc_info:
        http_utils.open_url(request, timeout_sec=5.0)

    assert exc_info.value.failure_kind == "missing_http_status"
    assert exc_info.value.status_code is None


def test_open_url_rejects_windows_native_non_2xx_status(tmp_path, monkeypatch):
    request = Request("https://api.github.com", headers={"User-Agent": "x"})
    captured = {}

    def fake_urlopen(_request, timeout=0, context=None):
        raise URLError(ssl.SSLError("certificate verify failed"))

    def fake_run(command, **kwargs):
        output_path = Path(kwargs["env"]["MC_HTTP_OUTPUT"])
        output_path.write_bytes(b'{"message":"Not Found"}')
        captured["output_path"] = output_path
        return SimpleNamespace(
            returncode=0,
            stdout='{"status_code":404,"headers":{"Content-Length":["23"]}}',
            stderr="",
        )

    monkeypatch.setattr(http_utils, "urlopen", fake_urlopen)
    monkeypatch.setattr(http_utils.subprocess, "run", fake_run)
    monkeypatch.setattr(http_utils.sys, "platform", "win32")
    monkeypatch.setattr(http_utils.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    with pytest.raises(http_utils.WindowsNativeRequestError, match="unexpected HTTP status: 404") as exc_info:
        http_utils.open_url(request, timeout_sec=5.0)

    assert exc_info.value.failure_kind == "unexpected_http_status"
    assert exc_info.value.status_code == 404
    assert captured["output_path"].exists() is False


def test_build_windows_native_command_checks_status_before_integer_cast():
    command = http_utils._build_windows_native_command(timeout_sec=5.0)
    encoded = command[-1]
    script = base64.b64decode(encoded).decode("utf-16le")

    assert "did not expose an HTTP status code" in script
    assert "[int]::TryParse" in script
    assert "-UseBasicParsing" in script
    assert "-AsHashtable" not in script


def test_classify_windows_native_failure_distinguishes_missing_status():
    assert (
        http_utils._classify_windows_native_failure(
            "Windows native request did not expose an HTTP status code."
        )
        == "missing_http_status"
    )
    assert http_utils._classify_windows_native_failure("socket reset by peer") == "transport_failure"
