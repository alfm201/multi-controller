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
) -> dict:
    return {
        "kind": "ctrl.layout_update",
        "layout": layout,
        "editor_id": editor_id,
        "coordinator_epoch": coordinator_epoch,
        "revision": revision,
        "persist": persist,
        "bootstrap": bool(bootstrap),
    }


def make_auto_switch_update_request(enabled: bool, requester_id: str) -> dict:
    return {
        "kind": "ctrl.auto_switch_update_request",
        "enabled": bool(enabled),
        "requester_id": requester_id,
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
) -> dict:
    return {
        "kind": "ctrl.monitor_inventory_refresh_request",
        "node_id": node_id,
        "requester_id": requester_id,
    }


def make_monitor_inventory_refresh_status(
    node_id: str,
    requester_id: str,
    status: str,
    detail: str,
    coordinator_epoch: str,
) -> dict:
    return {
        "kind": "ctrl.monitor_inventory_refresh_status",
        "node_id": node_id,
        "requester_id": requester_id,
        "status": status,
        "detail": detail,
        "coordinator_epoch": coordinator_epoch,
    }


def make_remote_update_request(target_id: str, requester_id: str) -> dict:
    return {
        "kind": "ctrl.remote_update_request",
        "target_id": target_id,
        "requester_id": requester_id,
    }


def make_remote_update_command(
    target_id: str,
    requester_id: str,
    coordinator_epoch: str,
) -> dict:
    return {
        "kind": "ctrl.remote_update_command",
        "target_id": target_id,
        "requester_id": requester_id,
        "coordinator_epoch": coordinator_epoch,
    }


def make_node_note_update_request(node_id: str, note: str, requester_id: str) -> dict:
    return {
        "kind": "ctrl.node_note_update_request",
        "node_id": node_id,
        "note": str(note or ""),
        "requester_id": requester_id,
    }


def make_node_note_update_state(node_id: str, note: str, coordinator_epoch: str) -> dict:
    return {
        "kind": "ctrl.node_note_update_state",
        "node_id": node_id,
        "note": str(note or ""),
        "coordinator_epoch": coordinator_epoch,
    }


def make_node_list_update_request(
    nodes: list[dict],
    requester_id: str,
    *,
    rename_map: dict[str, str] | None = None,
) -> dict:
    return {
        "kind": "ctrl.node_list_update_request",
        "nodes": list(nodes),
        "requester_id": requester_id,
        "rename_map": {} if rename_map is None else dict(rename_map),
    }


def make_node_list_state(
    nodes: list[dict],
    coordinator_epoch: str,
    *,
    rename_map: dict[str, str] | None = None,
) -> dict:
    return {
        "kind": "ctrl.node_list_state",
        "nodes": list(nodes),
        "rename_map": {} if rename_map is None else dict(rename_map),
        "coordinator_epoch": coordinator_epoch,
    }
