"""Tests for runtime/http_utils.py."""

from __future__ import annotations

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
        output_path = Path(command[-1])
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
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["creationflags"] == 0x08000000
    assert Path(captured["command"][-1]).exists() is False


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
