"""Application version helpers and GitHub release update checks."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from urllib.request import Request

from runtime.app_identity import (
    APP_COMPATIBILITY_VERSION,
    APP_EXECUTABLE_NAME,
    APP_GITHUB_REPOSITORY,
    APP_VERSION,
)
from runtime.http_utils import open_url

_VERSION_PART_RE = re.compile(r"^(\d+)")
_INSTALLER_ASSET_RE = re.compile(
    rf"^{re.escape(APP_EXECUTABLE_NAME)}-Setup-.*\.exe$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LatestReleaseInfo:
    version: str
    tag_name: str
    release_url: str | None = None
    installer_url: str | None = None
    published_at: str | None = None


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    latest_version: str
    latest_tag_name: str
    release_url: str | None
    installer_url: str | None
    status: str


@dataclass(frozen=True)
class VersionCompatibilityReport:
    current_version: str | None
    compatibility_version: str | None
    current_version_label: str
    compatibility_version_label: str
    local_compatibility_version: str | None
    local_compatibility_version_label: str
    status: str
    status_label: str
    is_compatible: bool
    tooltip: str


def get_current_version() -> str:
    return APP_VERSION


def get_current_version_label() -> str:
    return format_version_label(get_current_version())


def get_current_compatibility_version() -> str:
    return APP_COMPATIBILITY_VERSION


def get_current_compatibility_version_label() -> str:
    return format_version_label(get_current_compatibility_version())


def format_version_label(version: str) -> str:
    normalized = normalize_version_tag(version)
    return f"v{normalized}" if normalized else "버전 정보 없음"


def format_optional_version_label(version: str | None, *, unknown_text: str = "알 수 없음") -> str:
    normalized = normalize_version_tag(version)
    return format_version_label(normalized) if normalized else unknown_text


def normalize_version_tag(version: str | None) -> str:
    raw = "" if version is None else str(version).strip()
    if raw.lower().startswith("v"):
        raw = raw[1:]
    return raw.strip()


def compare_versions(left: str, right: str) -> int:
    left_parts = _parse_version_parts(left)
    right_parts = _parse_version_parts(right)
    size = max(len(left_parts), len(right_parts))
    left_padded = left_parts + (0,) * (size - len(left_parts))
    right_padded = right_parts + (0,) * (size - len(right_parts))
    if left_padded < right_padded:
        return -1
    if left_padded > right_padded:
        return 1
    return 0


def fetch_latest_release(
    repository: str = APP_GITHUB_REPOSITORY,
    *,
    timeout_sec: float = 5.0,
    urlopen_fn=None,
) -> LatestReleaseInfo:
    request = Request(
        f"https://api.github.com/repos/{repository}/releases/latest",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "multi-controller-update-check",
        },
    )
    with open_url(request, timeout_sec=timeout_sec, urlopen_fn=urlopen_fn) as response:
        payload = json.loads(response.read().decode("utf-8"))

    tag_name = str(payload.get("tag_name") or "").strip()
    if not tag_name:
        raise ValueError("GitHub Release 응답에 tag_name이 없습니다.")

    return LatestReleaseInfo(
        version=normalize_version_tag(tag_name),
        tag_name=tag_name,
        release_url=payload.get("html_url"),
        installer_url=_extract_installer_url(payload),
        published_at=payload.get("published_at"),
    )


def check_for_updates(
    *,
    current_version: str | None = None,
    repository: str = APP_GITHUB_REPOSITORY,
    timeout_sec: float = 5.0,
    urlopen_fn=None,
) -> UpdateCheckResult:
    resolved_current = normalize_version_tag(current_version or get_current_version())
    latest = fetch_latest_release(
        repository,
        timeout_sec=timeout_sec,
        urlopen_fn=urlopen_fn,
    )
    comparison = compare_versions(resolved_current, latest.version)
    if comparison < 0:
        status = "update_available"
    elif comparison > 0:
        status = "ahead_of_latest"
    else:
        status = "up_to_date"
    return UpdateCheckResult(
        current_version=resolved_current,
        latest_version=latest.version,
        latest_tag_name=latest.tag_name,
        release_url=latest.release_url,
        installer_url=latest.installer_url,
        status=status,
    )


def build_update_status_text(result: UpdateCheckResult) -> tuple[str, str]:
    current_label = format_version_label(result.current_version)
    latest_label = format_version_label(result.latest_version)
    if result.status == "update_available":
        return f"새 버전 {latest_label}이 준비되었습니다.", "accent"
    if result.status == "ahead_of_latest":
        return (
            f"현재 버전 {current_label}이 GitHub 최신 릴리스 {latest_label}보다 높습니다.",
            "warning",
        )
    return f"현재 최신 버전({current_label})을 사용 중입니다.", "success"


def resolve_update_install_url(result: UpdateCheckResult) -> str | None:
    return result.installer_url or result.release_url


def build_version_compatibility_report(
    *,
    current_version: str | None,
    compatibility_version: str | None,
    local_compatibility_version: str | None = None,
) -> VersionCompatibilityReport:
    normalized_current = normalize_version_tag(current_version) or None
    normalized_compatibility = normalize_version_tag(compatibility_version) or None
    normalized_local = normalize_version_tag(
        local_compatibility_version or get_current_compatibility_version()
    ) or None

    current_label = format_optional_version_label(normalized_current)
    compatibility_label = format_optional_version_label(normalized_compatibility)
    local_label = format_optional_version_label(normalized_local)

    if normalized_compatibility is None or normalized_local is None:
        return VersionCompatibilityReport(
            current_version=normalized_current,
            compatibility_version=normalized_compatibility,
            current_version_label=current_label,
            compatibility_version_label=compatibility_label,
            local_compatibility_version=normalized_local,
            local_compatibility_version_label=local_label,
            status="unknown",
            status_label="확인 불가",
            is_compatible=False,
            tooltip=(
                "호환 가능 버전 정보를 아직 받지 못했습니다.\n"
                f"이 노드 버전: {current_label}\n"
                f"이 노드 호환 가능 버전: {compatibility_label}\n"
                f"현재 PC 호환 가능 버전: {local_label}"
            ),
        )

    comparison = compare_versions(normalized_compatibility, normalized_local)
    if comparison == 0:
        return VersionCompatibilityReport(
            current_version=normalized_current,
            compatibility_version=normalized_compatibility,
            current_version_label=current_label,
            compatibility_version_label=compatibility_label,
            local_compatibility_version=normalized_local,
            local_compatibility_version_label=local_label,
            status="compatible",
            status_label="호환 가능",
            is_compatible=True,
            tooltip=(
                f"호환 가능 버전: {compatibility_label}\n"
                f"현재 PC 기준 호환 버전: {local_label}"
            ),
        )

    if comparison < 0:
        return VersionCompatibilityReport(
            current_version=normalized_current,
            compatibility_version=normalized_compatibility,
            current_version_label=current_label,
            compatibility_version_label=compatibility_label,
            local_compatibility_version=normalized_local,
            local_compatibility_version_label=local_label,
            status="outdated",
            status_label="업데이트 필요",
            is_compatible=False,
            tooltip=(
                f"호환 가능 버전: {compatibility_label}\n"
                f"현재 PC 기준 호환 버전: {local_label}\n"
                "이 노드는 현재 PC보다 오래된 버전을 사용 중입니다.\n"
                "버전 셀을 클릭하면 이 노드에 업데이트 명령을 보낼 수 있습니다."
            ),
        )

    return VersionCompatibilityReport(
        current_version=normalized_current,
        compatibility_version=normalized_compatibility,
        current_version_label=current_label,
        compatibility_version_label=compatibility_label,
        local_compatibility_version=normalized_local,
        local_compatibility_version_label=local_label,
        status="ahead",
        status_label="상대가 더 최신",
        is_compatible=False,
        tooltip=(
            f"호환 가능 버전: {compatibility_label}\n"
            f"현재 PC 기준 호환 버전: {local_label}\n"
            "이 노드는 현재 PC보다 더 최신 버전을 사용 중입니다.\n"
            "이 경우 상대 노드가 아니라 현재 PC를 업데이트하면 다시 버전을 맞출 수 있습니다."
        ),
    )


def _parse_version_parts(version: str) -> tuple[int, ...]:
    normalized = normalize_version_tag(version)
    parts = []
    for chunk in normalized.split("."):
        if not chunk:
            continue
        match = _VERSION_PART_RE.match(chunk)
        if match is None:
            break
        parts.append(int(match.group(1)))
    if not parts:
        raise ValueError(f"버전 형식을 해석할 수 없습니다: {version!r}")
    return tuple(parts)


def _extract_installer_url(payload: dict) -> str | None:
    assets = payload.get("assets") or ()
    fallback = None
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "").strip()
        download_url = str(asset.get("browser_download_url") or "").strip()
        if not download_url:
            continue
        if fallback is None and name.lower().endswith(".exe"):
            fallback = download_url
        if _INSTALLER_ASSET_RE.match(name):
            return download_url
    return fallback
