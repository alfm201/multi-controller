"""Tests for runtime/app_version.py."""

from __future__ import annotations

import json
from pathlib import Path
import tomllib

from runtime.app_identity import APP_VERSION
from runtime.app_version import (
    build_update_status_text,
    check_for_updates,
    compare_versions,
    format_version_label,
)


class FakeResponse:
    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_app_version_matches_pyproject():
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

    assert APP_VERSION == project["project"]["version"]


def test_compare_versions_handles_prefix_and_padding():
    assert compare_versions("v0.3.17", "0.3.17") == 0
    assert compare_versions("0.3.17", "0.3.18") == -1
    assert compare_versions("0.3.17", "0.3.17.0") == 0
    assert compare_versions("0.3.18", "0.3.17.9") == 1


def test_check_for_updates_reports_newer_release():
    def fake_urlopen(request, timeout=0):
        assert request.full_url.endswith("/releases/latest")
        assert timeout == 5.0
        return FakeResponse(
            {
                "tag_name": "v0.3.18",
                "html_url": "https://example.com/release/v0.3.18",
            }
        )

    result = check_for_updates(current_version="0.3.17", urlopen_fn=fake_urlopen)

    assert result.status == "update_available"
    assert result.latest_version == "0.3.18"
    assert result.release_url == "https://example.com/release/v0.3.18"


def test_build_update_status_text_for_latest_version():
    result = check_for_updates(
        current_version="0.3.17",
        urlopen_fn=lambda request, timeout=0: FakeResponse({"tag_name": "0.3.17"}),
    )

    text, tone = build_update_status_text(result)

    assert tone == "success"
    assert text == f"현재 최신 버전({format_version_label('0.3.17')})을 사용 중입니다."
