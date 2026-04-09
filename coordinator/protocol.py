"""Factories for control-plane messages."""

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


def make_heartbeat(target_id: str, controller_id: str) -> dict:
    return {
        "kind": "ctrl.heartbeat",
        "target_id": target_id,
        "controller_id": controller_id,
    }


def make_grant(
    target_id: str,
    controller_id: str,
    lease_ttl_ms: int = DEFAULT_LEASE_TTL_MS,
) -> dict:
    return {
        "kind": "ctrl.grant",
        "target_id": target_id,
        "controller_id": controller_id,
        "lease_ttl_ms": lease_ttl_ms,
    }


def make_deny(target_id: str, controller_id: str, reason: str) -> dict:
    return {
        "kind": "ctrl.deny",
        "target_id": target_id,
        "controller_id": controller_id,
        "reason": reason,
    }


def make_lease_update(
    target_id: str,
    controller_id: str | None,
    lease_ttl_ms: int = DEFAULT_LEASE_TTL_MS,
) -> dict:
    return {
        "kind": "ctrl.lease_update",
        "target_id": target_id,
        "controller_id": controller_id,
        "lease_ttl_ms": lease_ttl_ms,
    }
