"""Application version helpers and GitHub release update checks."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from urllib.request import Request, urlopen

from runtime.app_identity import APP_GITHUB_REPOSITORY, APP_VERSION

_VERSION_PART_RE = re.compile(r"^(\d+)")


@dataclass(frozen=True)
class LatestReleaseInfo:
    version: str
    tag_name: str
    release_url: str | None = None
    published_at: str | None = None


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    latest_version: str
    latest_tag_name: str
    release_url: str | None
    status: str


def get_current_version() -> str:
    return APP_VERSION


def get_current_version_label() -> str:
    return format_version_label(get_current_version())


def format_version_label(version: str) -> str:
    normalized = normalize_version_tag(version)
    return f"v{normalized}" if normalized else "버전 정보 없음"


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
    opener = urlopen if urlopen_fn is None else urlopen_fn
    request = Request(
        f"https://api.github.com/repos/{repository}/releases/latest",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "multi-controller-update-check",
        },
    )
    with opener(request, timeout=timeout_sec) as response:
        payload = json.loads(response.read().decode("utf-8"))

    tag_name = str(payload.get("tag_name") or "").strip()
    if not tag_name:
        raise ValueError("GitHub Release 응답에 tag_name이 없습니다.")

    return LatestReleaseInfo(
        version=normalize_version_tag(tag_name),
        tag_name=tag_name,
        release_url=payload.get("html_url"),
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
        status=status,
    )


def build_update_status_text(result: UpdateCheckResult) -> tuple[str, str]:
    current_label = format_version_label(result.current_version)
    latest_label = format_version_label(result.latest_version)
    if result.status == "update_available":
        text = f"새 버전 {latest_label}이 있습니다. 현재 버전은 {current_label}입니다."
        if result.release_url:
            text += f"\n릴리스: {result.release_url}"
        return text, "accent"
    if result.status == "ahead_of_latest":
        return (
            f"현재 버전 {current_label}이 GitHub 최신 릴리스 {latest_label}보다 높습니다.",
            "warning",
        )
    return f"현재 최신 버전({current_label})을 사용 중입니다.", "success"


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
