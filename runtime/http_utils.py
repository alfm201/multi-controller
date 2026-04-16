"""Shared HTTPS helpers with certificate fallback for packaged builds."""

from __future__ import annotations

import os
from pathlib import Path
import ssl
import sys
from urllib.request import urlopen

try:
    import certifi  # type: ignore
except Exception:
    certifi = None


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
        return opener(request, timeout=timeout_sec)


def _existing_ca_bundle_from_env() -> str | None:
    for name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        value = str(os.environ.get(name) or "").strip()
        if value and Path(value).is_file():
            return value
    return None
