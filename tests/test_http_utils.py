"""Tests for runtime/http_utils.py."""

from __future__ import annotations

from pathlib import Path

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
