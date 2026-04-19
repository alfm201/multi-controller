"""Compatibility wrappers for legacy remote-update helpers.

Internal code should prefer app.update.update_domain for new update modeling.
"""

from __future__ import annotations

from app.update.update_domain import (
    UPDATE_STAGE_CHECKING,
    UPDATE_STAGE_COMPLETED,
    UPDATE_STAGE_DOWNLOADING,
    UPDATE_STAGE_FAILED,
    UPDATE_STAGE_INSTALLING,
    UPDATE_STAGE_NO_UPDATE,
    UPDATE_STAGE_REQUEST_SENT,
    build_update_event_message,
    make_remote_update_status_payload as _make_remote_payload,
    new_update_event_id,
    new_update_session_id,
    normalize_update_event,
    remote_status_for_stage,
)

REMOTE_UPDATE_STATUS_REQUESTED = "requested"
REMOTE_UPDATE_STATUS_CHECKING = UPDATE_STAGE_CHECKING
REMOTE_UPDATE_STATUS_DOWNLOADING = UPDATE_STAGE_DOWNLOADING
REMOTE_UPDATE_STATUS_INSTALLING = UPDATE_STAGE_INSTALLING
REMOTE_UPDATE_STATUS_COMPLETED = UPDATE_STAGE_COMPLETED
REMOTE_UPDATE_STATUS_FAILED = UPDATE_STAGE_FAILED
REMOTE_UPDATE_STATUS_NO_UPDATE = UPDATE_STAGE_NO_UPDATE

REMOTE_UPDATE_TERMINAL_STATUSES = {
    REMOTE_UPDATE_STATUS_COMPLETED,
    REMOTE_UPDATE_STATUS_FAILED,
    REMOTE_UPDATE_STATUS_NO_UPDATE,
}


def normalize_remote_update_status(status: str | None) -> str:
    return remote_status_for_stage(status)


def new_remote_update_event_id() -> str:
    return new_update_event_id()


def new_remote_update_session_id() -> str:
    return new_update_session_id()


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
) -> dict[str, str]:
    return _make_remote_payload(
        target_id=target_id,
        requester_id=requester_id,
        status=status,
        detail=detail,
        event_id=event_id,
        session_id=session_id,
        current_version=current_version,
        latest_version=latest_version,
    )


def build_remote_update_status_message(
    *,
    node_label: str,
    status: str,
    detail: str = "",
    current_version: str = "",
    latest_version: str = "",
) -> tuple[str, str]:
    event = normalize_update_event(
        {
            "target_id": node_label,
            "status": status,
            "detail": detail,
            "current_version": current_version,
            "latest_version": latest_version,
        },
        default_target_kind="remote_node",
    )
    event["target_id"] = node_label
    if event["stage"] == UPDATE_STAGE_REQUEST_SENT:
        event["status"] = REMOTE_UPDATE_STATUS_REQUESTED
    return build_update_event_message(event, node_label=node_label)
