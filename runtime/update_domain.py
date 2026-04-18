"""Common update-domain helpers shared by self and remote update flows."""

from __future__ import annotations

from uuid import uuid4

from runtime.app_version import format_version_label

UPDATE_TARGET_SELF = "self"
UPDATE_TARGET_REMOTE_NODE = "remote_node"
UPDATE_TARGET_SELECTED_NODES = "selected_nodes"

UPDATE_ACTION_SCHEDULED_CHECK = "scheduled_check"
UPDATE_ACTION_STARTUP_CHECK = "startup_check"
UPDATE_ACTION_MANUAL_CHECK = "manual_check"
UPDATE_ACTION_REMOTE_REQUEST = "request"
UPDATE_ACTION_DOWNLOAD = "download"
UPDATE_ACTION_INSTALL = "install"
UPDATE_ACTION_VERSION_SYNC = "version_sync"

UPDATE_ORIGIN_AUTO = "auto"
UPDATE_ORIGIN_STARTUP = "startup"
UPDATE_ORIGIN_MANUAL = "manual"
UPDATE_ORIGIN_REMOTE_COMMAND = "remote_command"
UPDATE_ORIGIN_OUTCOME_REPLAY = "outcome_replay"

UPDATE_STAGE_IDLE = "idle"
UPDATE_STAGE_CHECKING = "checking"
UPDATE_STAGE_UPDATE_AVAILABLE = "update_available"
UPDATE_STAGE_NO_UPDATE = "no_update"
UPDATE_STAGE_REQUEST_SENT = "request_sent"
UPDATE_STAGE_DOWNLOADING = "downloading"
UPDATE_STAGE_DOWNLOADED = "downloaded"
UPDATE_STAGE_INSTALLING = "installing"
UPDATE_STAGE_COMPLETED = "completed"
UPDATE_STAGE_FAILED = "failed"
UPDATE_STAGE_TIMEOUT = "timeout"
REMOTE_UPDATE_BUSY_DETAIL = "이미 업데이트 확인 또는 설치 작업이 진행 중입니다."

UPDATE_TERMINAL_STAGES = {
    UPDATE_STAGE_NO_UPDATE,
    UPDATE_STAGE_COMPLETED,
    UPDATE_STAGE_FAILED,
    UPDATE_STAGE_TIMEOUT,
}

_STAGE_ALIASES = {
    "requested": UPDATE_STAGE_REQUEST_SENT,
    "starting": UPDATE_STAGE_INSTALLING,
}

_REMOTE_WIRE_STATUSES = {
    UPDATE_STAGE_REQUEST_SENT: "requested",
    UPDATE_STAGE_CHECKING: UPDATE_STAGE_CHECKING,
    UPDATE_STAGE_NO_UPDATE: UPDATE_STAGE_NO_UPDATE,
    UPDATE_STAGE_DOWNLOADING: UPDATE_STAGE_DOWNLOADING,
    UPDATE_STAGE_INSTALLING: UPDATE_STAGE_INSTALLING,
    UPDATE_STAGE_COMPLETED: UPDATE_STAGE_COMPLETED,
    UPDATE_STAGE_FAILED: UPDATE_STAGE_FAILED,
    UPDATE_STAGE_TIMEOUT: UPDATE_STAGE_TIMEOUT,
}


def normalize_update_stage(stage: str | None) -> str:
    normalized = str(stage or "").strip().lower()
    return _STAGE_ALIASES.get(normalized, normalized)


def remote_status_for_stage(stage: str | None) -> str:
    resolved = normalize_update_stage(stage)
    return _REMOTE_WIRE_STATUSES.get(resolved, resolved)


def new_update_event_id() -> str:
    return uuid4().hex


def new_update_session_id() -> str:
    return uuid4().hex


def make_update_event(
    *,
    stage: str,
    target_kind: str = UPDATE_TARGET_SELF,
    target_id: str = "",
    requester_id: str = "",
    action: str = "",
    origin: str = "",
    detail: str = "",
    event_id: str | None = None,
    session_id: str | None = None,
    current_version: str | None = None,
    target_version: str | None = None,
    tag_name: str | None = None,
) -> dict[str, str]:
    resolved_stage = normalize_update_stage(stage)
    resolved_target_kind = str(target_kind or "").strip() or (
        UPDATE_TARGET_REMOTE_NODE if str(target_id or "").strip() else UPDATE_TARGET_SELF
    )
    resolved_target_version = str(target_version or "").strip()
    return {
        "event_id": str(event_id or new_update_event_id()),
        "session_id": str(session_id or "").strip(),
        "target_kind": resolved_target_kind,
        "target_id": str(target_id or "").strip(),
        "requester_id": str(requester_id or "").strip(),
        "action": str(action or _default_action_for_stage(resolved_stage, resolved_target_kind)).strip(),
        "origin": str(origin or "").strip(),
        "stage": resolved_stage,
        "status": remote_status_for_stage(resolved_stage),
        "detail": str(detail or ""),
        "current_version": str(current_version or "").strip(),
        "target_version": resolved_target_version,
        "latest_version": resolved_target_version,
        "tag_name": str(tag_name or "").strip(),
    }


def normalize_update_event(
    payload: dict | None,
    *,
    default_target_kind: str = UPDATE_TARGET_SELF,
    default_action: str = "",
    default_origin: str = "",
) -> dict[str, str]:
    raw = {} if payload is None else dict(payload)
    target_id = str(raw.get("target_id") or raw.get("node_id") or "").strip()
    target_kind = str(raw.get("target_kind") or "").strip() or (
        UPDATE_TARGET_REMOTE_NODE if target_id else default_target_kind
    )
    target_version = str(raw.get("target_version") or raw.get("latest_version") or "").strip()
    return make_update_event(
        stage=str(raw.get("stage") or raw.get("status") or ""),
        target_kind=target_kind,
        target_id=target_id,
        requester_id=str(raw.get("requester_id") or "").strip(),
        action=str(raw.get("action") or default_action or ""),
        origin=str(raw.get("origin") or default_origin or ""),
        detail=str(raw.get("detail") or ""),
        event_id=str(raw.get("event_id") or "").strip() or None,
        session_id=str(raw.get("session_id") or "").strip() or None,
        current_version=str(raw.get("current_version") or "").strip() or None,
        target_version=target_version or None,
        tag_name=str(raw.get("tag_name") or "").strip() or None,
    )


def make_remote_update_status_payload(
    *,
    target_id: str,
    requester_id: str,
    status: str,
    detail: str = "",
    event_id: str | None = None,
    session_id: str | None = None,
    current_version: str | None = None,
    latest_version: str | None = None,
    action: str = UPDATE_ACTION_REMOTE_REQUEST,
    origin: str = UPDATE_ORIGIN_REMOTE_COMMAND,
) -> dict[str, str]:
    return normalize_update_event(
        {
            "target_kind": UPDATE_TARGET_REMOTE_NODE,
            "target_id": target_id,
            "requester_id": requester_id,
            "action": action,
            "origin": origin,
            "status": status,
            "detail": detail,
            "event_id": event_id,
            "session_id": session_id,
            "current_version": current_version,
            "latest_version": latest_version,
        },
        default_target_kind=UPDATE_TARGET_REMOTE_NODE,
        default_action=action,
        default_origin=origin,
    )


def build_update_notice_payload(
    *,
    stage: str,
    current_version: str = "",
    target_version: str = "",
    tag_name: str = "",
    action: str = "",
    origin: str = "",
    target_kind: str = UPDATE_TARGET_SELF,
    detail: str = "",
    auto_trigger: bool = False,
    button_enabled: bool = True,
    button_text: str = "업데이트 설치",
) -> dict[str, object]:
    event = make_update_event(
        stage=stage,
        target_kind=target_kind,
        action=action,
        origin=origin,
        current_version=current_version,
        target_version=target_version,
        tag_name=tag_name,
        detail=detail,
    )
    target_label = _format_version_suffix(event["target_version"])
    if event["stage"] == UPDATE_STAGE_UPDATE_AVAILABLE:
        title = f"새 업데이트 {target_label}이 준비되었습니다!" if target_label else "새 업데이트가 준비되었습니다!"
        detail_text = detail or _build_update_available_detail()
        button_visible = True
    elif event["stage"] in {UPDATE_STAGE_DOWNLOADING, UPDATE_STAGE_INSTALLING}:
        title = "업데이트를 설치하는 중입니다..."
        detail_text = detail
        button_visible = False
    elif event["stage"] == UPDATE_STAGE_DOWNLOADED:
        title = (
            f"업데이트 {target_label} 설치 준비가 완료되었습니다."
            if target_label
            else "업데이트 설치 준비가 완료되었습니다."
        )
        detail_text = detail or _build_install_ready_detail(target_label, auto_trigger=auto_trigger)
        button_visible = False
    else:
        return {
            "visible": False,
            **event,
            "button_visible": False,
            "button_enabled": False,
            "button_text": button_text,
        }
    return {
        "visible": True,
        **event,
        "title": title,
        "detail": detail_text,
        "tag_name": tag_name,
        "button_visible": button_visible,
        "button_enabled": button_enabled,
        "button_text": button_text,
    }


def should_announce_update_notice(payload: dict | None) -> bool:
    event = normalize_update_event(payload)
    if event["stage"] == UPDATE_STAGE_UPDATE_AVAILABLE:
        return bool(str((payload or {}).get("tag_name") or event.get("tag_name") or "").strip())
    return False


def build_update_event_message(event: dict | None, *, node_label: str = "") -> tuple[str, str]:
    normalized = normalize_update_event(event)
    target_kind = normalized["target_kind"]
    if target_kind == UPDATE_TARGET_REMOTE_NODE:
        return _build_remote_update_message(normalized, node_label=node_label)
    return _build_self_update_message(normalized)


def _default_action_for_stage(stage: str, target_kind: str) -> str:
    if stage == UPDATE_STAGE_REQUEST_SENT:
        return UPDATE_ACTION_REMOTE_REQUEST
    if stage in {UPDATE_STAGE_CHECKING, UPDATE_STAGE_UPDATE_AVAILABLE, UPDATE_STAGE_NO_UPDATE}:
        return UPDATE_ACTION_MANUAL_CHECK if target_kind == UPDATE_TARGET_SELF else UPDATE_ACTION_REMOTE_REQUEST
    if stage in {
        UPDATE_STAGE_DOWNLOADING,
        UPDATE_STAGE_DOWNLOADED,
        UPDATE_STAGE_INSTALLING,
        UPDATE_STAGE_COMPLETED,
        UPDATE_STAGE_FAILED,
        UPDATE_STAGE_TIMEOUT,
    }:
        return UPDATE_ACTION_INSTALL
    return UPDATE_ACTION_MANUAL_CHECK if target_kind == UPDATE_TARGET_SELF else UPDATE_ACTION_REMOTE_REQUEST


def _format_version_suffix(version: str) -> str:
    normalized = str(version or "").strip()
    return format_version_label(normalized) if normalized else ""


def _build_update_available_detail() -> str:
    return "설치 버튼을 눌러 새 버전 준비를 시작할 수 있습니다."


def _build_install_ready_detail(target_label: str, *, auto_trigger: bool) -> str:
    if auto_trigger and target_label:
        return f"트레이 모드로 다시 시작해 {target_label} 설치를 이어갈 준비가 완료되었습니다."
    if auto_trigger:
        return "트레이 모드로 다시 시작해 설치를 이어갈 준비가 완료되었습니다."
    if target_label:
        return f"앱이 종료되면 백그라운드에서 {target_label} 설치가 이어집니다."
    return "앱이 종료되면 백그라운드 설치가 이어집니다."


def _build_remote_update_message(event: dict[str, str], *, node_label: str) -> tuple[str, str]:
    label = node_label or event["target_id"] or "원격 노드"
    detail = event["detail"]
    version_suffix = _remote_version_suffix(event["current_version"], event["target_version"])
    stage = event["stage"]
    if stage == UPDATE_STAGE_REQUEST_SENT:
        return f"{label} 노드에 업데이트 요청을 전송했습니다.", "accent"
    if stage == UPDATE_STAGE_CHECKING:
        return f"{label} 노드가 업데이트 확인을 시작했습니다{version_suffix}.", "accent"
    if stage == UPDATE_STAGE_DOWNLOADING:
        return f"{label} 노드가 업데이트 다운로드를 시작했습니다{version_suffix}.", "accent"
    if stage == UPDATE_STAGE_INSTALLING:
        return f"{label} 노드가 업데이트 설치를 시작했습니다{version_suffix}.", "accent"
    if stage == UPDATE_STAGE_COMPLETED:
        return f"{label} 노드 업데이트가 완료되었습니다{version_suffix}.", "success"
    if stage == UPDATE_STAGE_NO_UPDATE:
        target_label = _format_version_suffix(event["target_version"])
        if target_label:
            return f"{label} 노드는 이미 최신 버전 {target_label}을 사용 중입니다.", "success"
        return f"{label} 노드는 이미 최신 버전을 사용 중입니다.", "success"
    if stage in {UPDATE_STAGE_FAILED, UPDATE_STAGE_TIMEOUT}:
        if detail == REMOTE_UPDATE_BUSY_DETAIL:
            return f"{label} 노드는 이미 업데이트 작업 중입니다.", "warning"
        message = f"{label} 노드 업데이트에 실패했습니다."
        if detail:
            message = f"{message} ({detail})"
        return message, "warning"
    return f"{label} 노드 업데이트 상태: {stage}", "neutral"


def _build_self_update_message(event: dict[str, str]) -> tuple[str, str]:
    stage = event["stage"]
    target_label = _format_version_suffix(event["target_version"])
    detail = event["detail"]
    if stage == UPDATE_STAGE_UPDATE_AVAILABLE:
        return _build_update_available_detail(), "accent"
    if stage == UPDATE_STAGE_NO_UPDATE:
        current_label = _format_version_suffix(event["current_version"])
        if current_label:
            return f"현재 최신 버전({current_label})을 사용 중입니다.", "success"
        return "현재 최신 버전을 사용 중입니다.", "success"
    if stage == UPDATE_STAGE_DOWNLOADING:
        return "업데이트 다운로드를 시작했습니다.", "accent"
    if stage == UPDATE_STAGE_INSTALLING:
        return "업데이트 설치를 시작했습니다.", "accent"
    if stage == UPDATE_STAGE_COMPLETED:
        if target_label:
            return f"{target_label} 업데이트가 완료되었습니다.", "success"
        return "업데이트가 완료되었습니다.", "success"
    if stage in {UPDATE_STAGE_FAILED, UPDATE_STAGE_TIMEOUT}:
        message = "업데이트에 실패했습니다."
        if detail:
            message = f"{message} ({detail})"
        return message, "warning"
    return f"업데이트 상태: {stage}", "neutral"


def _remote_version_suffix(current_version: str, target_version: str) -> str:
    current_label = _format_version_suffix(current_version)
    target_label = _format_version_suffix(target_version)
    if current_label and target_label and current_label != target_label:
        return f" ({current_label} -> {target_label})"
    if target_label:
        return f" ({target_label})"
    return ""
