"""Tests for runtime/app_version.py."""

from __future__ import annotations

import json
from pathlib import Path
import tomllib

from runtime.app_identity import APP_COMPATIBILITY_VERSION, APP_VERSION
from runtime.app_version import (
    build_version_compatibility_report,
    build_update_status_text,
    check_for_updates,
    compare_versions,
    format_version_label,
    resolve_update_install_url,
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


def test_compatibility_version_matches_current_release_for_now():
    assert APP_COMPATIBILITY_VERSION == APP_VERSION


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
                "assets": [
                    {
                        "name": "MultiScreenPass-Setup-0.3.18.exe",
                        "browser_download_url": "https://example.com/download/MultiScreenPass-Setup-0.3.18.exe",
                    }
                ],
            }
        )

    result = check_for_updates(current_version="0.3.17", urlopen_fn=fake_urlopen)

    assert result.status == "update_available"
    assert result.latest_version == "0.3.18"
    assert result.release_url == "https://example.com/release/v0.3.18"
    assert result.installer_url == "https://example.com/download/MultiScreenPass-Setup-0.3.18.exe"


def test_build_update_status_text_for_latest_version():
    result = check_for_updates(
        current_version="0.3.17",
        urlopen_fn=lambda request, timeout=0: FakeResponse({"tag_name": "0.3.17"}),
    )

    text, tone = build_update_status_text(result)

    assert tone == "success"
    assert text == f"현재 최신 버전({format_version_label('0.3.17')})을 사용 중입니다."


def test_build_version_compatibility_report_marks_outdated_peer():
    report = build_version_compatibility_report(
        current_version="0.3.17",
        compatibility_version="0.3.17",
        local_compatibility_version="0.3.18",
    )

    assert report.is_compatible is False
    assert report.status == "outdated"
    assert report.status_label == "업데이트 필요"
    assert "오래된 버전" in report.tooltip


def test_build_version_compatibility_report_marks_newer_peer_as_ahead():
    report = build_version_compatibility_report(
        current_version="0.3.25",
        compatibility_version="0.3.25",
        local_compatibility_version="0.3.24",
    )

    assert report.is_compatible is False
    assert report.status == "ahead"
    assert report.status_label == "상대가 더 최신"
    assert "더 최신 버전" in report.tooltip


def test_resolve_update_install_url_prefers_installer_asset():
    result = check_for_updates(
        current_version="0.3.17",
        urlopen_fn=lambda request, timeout=0: FakeResponse(
            {
                "tag_name": "v0.3.18",
                "html_url": "https://example.com/release/v0.3.18",
                "assets": [
                    {
                        "name": "MultiScreenPass-Setup-0.3.18.exe",
                        "browser_download_url": "https://example.com/download/MultiScreenPass-Setup-0.3.18.exe",
                    }
                ],
            }
        ),
    )

    assert (
        resolve_update_install_url(result)
        == "https://example.com/download/MultiScreenPass-Setup-0.3.18.exe"
    )
