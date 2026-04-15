"""Shared HTTPS helpers with certificate fallback for packaged builds."""

from __future__ import annotations

import ssl
from urllib.request import urlopen

try:
    import certifi  # type: ignore
except Exception:
    certifi = None


def create_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    try:
        if certifi is not None:
            context.load_verify_locations(certifi.where())
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
