"""Control-plane 메시지 팩토리."""

DEFAULT_LEASE_TTL_MS = 3000


def make_claim(target_id: str, controller_id: str) -> dict:
    return {
        "kind": "ctrl.claim",
        "target_id": target_id,
        "controller_id": controller_id,
    }


def make_release(target_id: str, controller_id: str) -> dict:
    return {
        "kind": "ctrl.release",
        "target_id": target_id,
        "controller_id": controller_id,
    }


def make_local_input_override(target_id: str, controller_id: str) -> dict:
    return {
        "kind": "ctrl.local_input_override",
        "target_id": target_id,
        "controller_id": controller_id,
    }


def make_heartbeat(target_id: str, controller_id: str) -> dict:
    return {
        "kind": "ctrl.heartbeat",
        "target_id": target_id,
        "controller_id": controller_id,
    }


def make_grant(
    target_id: str,
    controller_id: str,
    coordinator_epoch: str,
    lease_ttl_ms: int = DEFAULT_LEASE_TTL_MS,
) -> dict:
    return {
        "kind": "ctrl.grant",
        "target_id": target_id,
        "controller_id": controller_id,
        "coordinator_epoch": coordinator_epoch,
        "lease_ttl_ms": lease_ttl_ms,
    }


def make_deny(
    target_id: str,
    controller_id: str,
    reason: str,
    coordinator_epoch: str,
) -> dict:
    return {
        "kind": "ctrl.deny",
        "target_id": target_id,
        "controller_id": controller_id,
        "reason": reason,
        "coordinator_epoch": coordinator_epoch,
    }


def make_lease_update(
    target_id: str,
    controller_id: str | None,
    coordinator_epoch: str,
    lease_ttl_ms: int = DEFAULT_LEASE_TTL_MS,
) -> dict:
    return {
        "kind": "ctrl.lease_update",
        "target_id": target_id,
        "controller_id": controller_id,
        "coordinator_epoch": coordinator_epoch,
        "lease_ttl_ms": lease_ttl_ms,
    }


def make_layout_edit_begin(editor_id: str) -> dict:
    return {
        "kind": "ctrl.layout_edit_begin",
        "editor_id": editor_id,
    }


def make_layout_edit_end(editor_id: str) -> dict:
    return {
        "kind": "ctrl.layout_edit_end",
        "editor_id": editor_id,
    }


def make_layout_edit_grant(editor_id: str, coordinator_epoch: str) -> dict:
    return {
        "kind": "ctrl.layout_edit_grant",
        "editor_id": editor_id,
        "coordinator_epoch": coordinator_epoch,
    }


def make_layout_edit_deny(
    editor_id: str,
    reason: str,
    coordinator_epoch: str,
    current_editor_id: str | None = None,
) -> dict:
    return {
        "kind": "ctrl.layout_edit_deny",
        "editor_id": editor_id,
        "reason": reason,
        "current_editor_id": current_editor_id,
        "coordinator_epoch": coordinator_epoch,
    }


def make_layout_state(editor_id: str | None, coordinator_epoch: str) -> dict:
    return {
        "kind": "ctrl.layout_state",
        "editor_id": editor_id,
        "coordinator_epoch": coordinator_epoch,
    }


def make_layout_update_request(layout: dict, editor_id: str, persist: bool = True) -> dict:
    return {
        "kind": "ctrl.layout_update_request",
        "layout": layout,
        "editor_id": editor_id,
        "persist": persist,
    }


def make_layout_update(
    layout: dict,
    editor_id: str,
    coordinator_epoch: str,
    revision: int,
    persist: bool = True,
    bootstrap: bool = False,
    change_kind: str | None = None,
    requester_id: str | None = None,
    request_id: str | None = None,
) -> dict:
    frame = {
        "kind": "ctrl.layout_update",
        "layout": layout,
        "editor_id": editor_id,
        "coordinator_epoch": coordinator_epoch,
        "revision": revision,
        "persist": persist,
        "bootstrap": bool(bootstrap),
    }
    if change_kind:
        frame["change_kind"] = str(change_kind)
    if requester_id:
        frame["requester_id"] = str(requester_id)
    if request_id:
        frame["request_id"] = str(request_id)
    return frame


def make_auto_switch_update_request(enabled: bool, requester_id: str, request_id: str = "") -> dict:
    return {
        "kind": "ctrl.auto_switch_update_request",
        "enabled": bool(enabled),
        "requester_id": requester_id,
        "request_id": str(request_id or ""),
    }


def make_monitor_inventory_publish(snapshot: dict) -> dict:
    return {
        "kind": "ctrl.monitor_inventory_publish",
        "snapshot": snapshot,
    }


def make_monitor_inventory_state(snapshot: dict, coordinator_epoch: str) -> dict:
    return {
        "kind": "ctrl.monitor_inventory_state",
        "snapshot": snapshot,
        "coordinator_epoch": coordinator_epoch,
    }


def make_monitor_inventory_refresh_request(
    node_id: str,
    requester_id: str,
    request_id: str = "",
) -> dict:
    return {
        "kind": "ctrl.monitor_inventory_refresh_request",
        "node_id": node_id,
        "requester_id": requester_id,
        "request_id": str(request_id or ""),
    }


def make_monitor_inventory_refresh_status(
    node_id: str,
    requester_id: str,
    status: str,
    detail: str,
    coordinator_epoch: str,
    request_id: str = "",
) -> dict:
    return {
        "kind": "ctrl.monitor_inventory_refresh_status",
        "node_id": node_id,
        "requester_id": requester_id,
        "status": status,
        "detail": detail,
        "coordinator_epoch": coordinator_epoch,
        "request_id": str(request_id or ""),
    }


def make_remote_update_request(target_id: str, requester_id: str, request_id: str = "") -> dict:
    return {
        "kind": "ctrl.remote_update_request",
        "target_id": target_id,
        "requester_id": requester_id,
        "request_id": str(request_id or ""),
    }


def make_remote_update_command(
    target_id: str,
    requester_id: str,
    coordinator_epoch: str,
    request_id: str = "",
) -> dict:
    return {
        "kind": "ctrl.remote_update_command",
        "target_id": target_id,
        "requester_id": requester_id,
        "coordinator_epoch": coordinator_epoch,
        "request_id": str(request_id or ""),
    }


def make_remote_update_status(
    target_id: str,
    requester_id: str,
    status: str,
    detail: str,
    coordinator_epoch: str,
    *,
    reason: str = "",
    request_id: str = "",
    event_id: str = "",
    session_id: str = "",
    current_version: str = "",
    latest_version: str = "",
) -> dict:
    return {
        "kind": "ctrl.remote_update_status",
        "target_id": target_id,
        "requester_id": requester_id,
        "status": status,
        "detail": detail,
        "reason": str(reason or ""),
        "coordinator_epoch": coordinator_epoch,
        "request_id": str(request_id or ""),
        "event_id": str(event_id or ""),
        "session_id": str(session_id or ""),
        "current_version": str(current_version or ""),
        "latest_version": str(latest_version or ""),
    }


def make_update_check_request(requester_id: str, request_id: str = "") -> dict:
    return {
        "kind": "ctrl.update_check_request",
        "requester_id": str(requester_id or ""),
        "request_id": str(request_id or ""),
    }


def make_update_check_command(job_id: str, coordinator_epoch: str) -> dict:
    return {
        "kind": "ctrl.update_check_command",
        "job_id": str(job_id or ""),
        "coordinator_epoch": coordinator_epoch,
    }


def make_update_check_result(
    job_id: str,
    status: str,
    detail: str,
    coordinator_epoch: str,
    *,
    result: dict | None = None,
    source_id: str = "",
) -> dict:
    frame = {
        "kind": "ctrl.update_check_result",
        "job_id": str(job_id or ""),
        "status": str(status or ""),
        "detail": str(detail or ""),
        "coordinator_epoch": coordinator_epoch,
        "source_id": str(source_id or ""),
    }
    if result is not None:
        frame["result"] = dict(result)
    return frame


def make_update_check_state(
    requester_id: str,
    request_id: str,
    status: str,
    detail: str,
    coordinator_epoch: str,
    *,
    result: dict | None = None,
    source_id: str = "",
) -> dict:
    frame = {
        "kind": "ctrl.update_check_state",
        "requester_id": str(requester_id or ""),
        "request_id": str(request_id or ""),
        "status": str(status or ""),
        "detail": str(detail or ""),
        "coordinator_epoch": coordinator_epoch,
        "source_id": str(source_id or ""),
    }
    if result is not None:
        frame["result"] = dict(result)
    return frame


def make_update_download_request(
    requester_id: str,
    request_id: str,
    *,
    tag_name: str,
    installer_url: str,
    current_version: str = "",
    latest_version: str = "",
) -> dict:
    return {
        "kind": "ctrl.update_download_request",
        "requester_id": str(requester_id or ""),
        "request_id": str(request_id or ""),
        "tag_name": str(tag_name or ""),
        "installer_url": str(installer_url or ""),
        "current_version": str(current_version or ""),
        "latest_version": str(latest_version or ""),
    }


def make_update_download_command(
    job_id: str,
    coordinator_epoch: str,
    *,
    tag_name: str,
    installer_url: str,
) -> dict:
    return {
        "kind": "ctrl.update_download_command",
        "job_id": str(job_id or ""),
        "coordinator_epoch": coordinator_epoch,
        "tag_name": str(tag_name or ""),
        "installer_url": str(installer_url or ""),
    }


def make_update_download_result(
    job_id: str,
    status: str,
    detail: str,
    coordinator_epoch: str,
    *,
    source_id: str = "",
    share_port: int = 0,
    share_id: str = "",
    share_token: str = "",
    sha256: str = "",
    size_bytes: int = 0,
) -> dict:
    return {
        "kind": "ctrl.update_download_result",
        "job_id": str(job_id or ""),
        "status": str(status or ""),
        "detail": str(detail or ""),
        "coordinator_epoch": coordinator_epoch,
        "source_id": str(source_id or ""),
        "share_port": int(share_port or 0),
        "share_id": str(share_id or ""),
        "share_token": str(share_token or ""),
        "sha256": str(sha256 or ""),
        "size_bytes": int(size_bytes or 0),
    }


def make_update_download_state(
    requester_id: str,
    request_id: str,
    status: str,
    detail: str,
    coordinator_epoch: str,
    *,
    source_id: str = "",
    share_port: int = 0,
    share_id: str = "",
    share_token: str = "",
    sha256: str = "",
    size_bytes: int = 0,
) -> dict:
    return {
        "kind": "ctrl.update_download_state",
        "requester_id": str(requester_id or ""),
        "request_id": str(request_id or ""),
        "status": str(status or ""),
        "detail": str(detail or ""),
        "coordinator_epoch": coordinator_epoch,
        "source_id": str(source_id or ""),
        "share_port": int(share_port or 0),
        "share_id": str(share_id or ""),
        "share_token": str(share_token or ""),
        "sha256": str(sha256 or ""),
        "size_bytes": int(size_bytes or 0),
    }


def make_node_note_update_request(node_id: str, note: str, requester_id: str, request_id: str = "") -> dict:
    return {
        "kind": "ctrl.node_note_update_request",
        "node_id": node_id,
        "note": str(note or ""),
        "requester_id": requester_id,
        "request_id": str(request_id or ""),
    }


def make_node_note_update_state(node_id: str, note: str, coordinator_epoch: str, request_id: str = "") -> dict:
    return {
        "kind": "ctrl.node_note_update_state",
        "node_id": node_id,
        "note": str(note or ""),
        "coordinator_epoch": coordinator_epoch,
        "request_id": str(request_id or ""),
    }


def make_node_list_update_request(
    nodes: list[dict],
    requester_id: str,
    *,
    base_revision: int = 0,
    rename_map: dict[str, str] | None = None,
    request_id: str = "",
) -> dict:
    return {
        "kind": "ctrl.node_list_update_request",
        "nodes": list(nodes),
        "requester_id": requester_id,
        "base_revision": int(base_revision),
        "rename_map": {} if rename_map is None else dict(rename_map),
        "request_id": str(request_id or ""),
    }


def make_node_list_state(
    nodes: list[dict],
    coordinator_epoch: str,
    *,
    revision: int = 0,
    rename_map: dict[str, str] | None = None,
    reject_reason: str | None = None,
    request_id: str = "",
) -> dict:
    frame = {
        "kind": "ctrl.node_list_state",
        "nodes": list(nodes),
        "revision": int(revision),
        "rename_map": {} if rename_map is None else dict(rename_map),
        "coordinator_epoch": coordinator_epoch,
    }
    if reject_reason:
        frame["reject_reason"] = str(reject_reason)
    if request_id:
        frame["request_id"] = str(request_id)
    return frame
