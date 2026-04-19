"""Centralized config bundle migrations."""

from __future__ import annotations

import uuid

SCHEMA_VERSION_KEY = "schema_version"
LEGACY_SCHEMA_VERSION = 1
CURRENT_SCHEMA_VERSION = 3
SCHEMA_MIGRATIONS = {
    1: ("v1_to_v2", 2, lambda config: _migrate_v1_to_v2(config)),
    2: ("v2_to_v3", 3, lambda config: _migrate_v2_to_v3(config)),
}


def migrate_config_data(config: dict) -> tuple[dict, bool, tuple[str, ...]]:
    if not isinstance(config, dict):
        return config, False, ()

    migrated = dict(config)
    version = determine_schema_version(migrated)
    applied_steps: list[str] = []

    while version < CURRENT_SCHEMA_VERSION:
        migration = SCHEMA_MIGRATIONS.get(version)
        if migration is None:
            raise ValueError(f"unsupported config schema version: {version}")
        step_name, next_version, migrate = migration
        migrated = migrate(migrated)
        applied_steps.append(step_name)
        version = next_version

    migrated, repaired = _repair_current_schema_config(migrated)
    if repaired:
        applied_steps.append("repair_current_schema")

    changed = bool(applied_steps)
    if migrated.get(SCHEMA_VERSION_KEY) != CURRENT_SCHEMA_VERSION:
        migrated = dict(migrated)
        migrated[SCHEMA_VERSION_KEY] = CURRENT_SCHEMA_VERSION
        changed = True
    return migrated, changed, tuple(applied_steps)


def determine_schema_version(config: dict) -> int:
    raw_version = config.get(SCHEMA_VERSION_KEY)
    if raw_version is None:
        return LEGACY_SCHEMA_VERSION
    try:
        version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{SCHEMA_VERSION_KEY} 값은 정수여야 합니다.") from exc
    if version < LEGACY_SCHEMA_VERSION:
        raise ValueError(f"{SCHEMA_VERSION_KEY} 값은 {LEGACY_SCHEMA_VERSION} 이상이어야 합니다.")
    if version > CURRENT_SCHEMA_VERSION:
        raise ValueError(
            f"설정 파일 버전 {version}은(는) 현재 지원하는 버전 {CURRENT_SCHEMA_VERSION}보다 높습니다."
        )
    return version


def _migrate_v1_to_v2(config: dict) -> dict:
    migrated = dict(config)
    raw_nodes = migrated.get("nodes")
    if not isinstance(raw_nodes, list):
        migrated[SCHEMA_VERSION_KEY] = 2
        return migrated

    next_nodes = []
    rename_map: dict[str, str] = {}
    used_identifiers: set[str] = set()

    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict):
            next_nodes.append(raw_node)
            continue
        node = dict(raw_node)
        name = str(node.get("name") or "").strip()
        explicit_node_id = str(node.get("node_id") or "").strip()
        node_id = explicit_node_id or _generate_migrated_node_id(node, used_identifiers)
        node["name"] = name
        node["node_id"] = node_id
        next_nodes.append(node)

        if name and name != node_id:
            rename_map[name] = node_id
        if name:
            used_identifiers.add(name)
        if node_id:
            used_identifiers.add(node_id)

    migrated["nodes"] = next_nodes
    migrated["layout"] = _rewrite_keyed_node_section(migrated.get("layout"), rename_map)
    migrated["monitor_overrides"] = _rewrite_keyed_node_section(
        migrated.get("monitor_overrides"),
        rename_map,
    )
    migrated["monitor_inventory"] = _rewrite_keyed_node_section(
        migrated.get("monitor_inventory"),
        rename_map,
        sync_payload_node_id=True,
    )
    migrated["coordinator"] = _rewrite_coordinator_candidates(
        migrated.get("coordinator"),
        rename_map,
    )
    migrated[SCHEMA_VERSION_KEY] = 2
    return migrated


def _migrate_v2_to_v3(config: dict) -> dict:
    migrated = dict(config)
    migrated.pop("default_roles", None)
    migrated.pop("role", None)

    raw_nodes = migrated.get("nodes")
    if isinstance(raw_nodes, list):
        migrated["nodes"] = [_strip_legacy_role_fields(node) for node in raw_nodes]

    migrated[SCHEMA_VERSION_KEY] = 3
    return migrated


def _repair_current_schema_config(config: dict) -> tuple[dict, bool]:
    migrated = dict(config)
    changed = False

    if "default_roles" in migrated:
        migrated.pop("default_roles", None)
        changed = True
    if "role" in migrated:
        migrated.pop("role", None)
        changed = True

    raw_nodes = migrated.get("nodes")
    rename_map: dict[str, str] = {}
    if isinstance(raw_nodes, list):
        next_nodes = []
        for raw_node in raw_nodes:
            if not isinstance(raw_node, dict):
                next_nodes.append(raw_node)
                continue
            node = dict(raw_node)
            cleaned_node = _strip_legacy_role_fields(node)
            name = str(cleaned_node.get("name") or "").strip()
            node_id = str(cleaned_node.get("node_id") or name).strip()
            cleaned_node["name"] = name
            cleaned_node["node_id"] = node_id
            if name and node_id and name != node_id:
                rename_map[name] = node_id
            if cleaned_node != raw_node:
                changed = True
            next_nodes.append(cleaned_node)
        migrated["nodes"] = next_nodes

    if rename_map:
        next_layout = _rewrite_keyed_node_section(migrated.get("layout"), rename_map)
        next_monitor_overrides = _rewrite_keyed_node_section(
            migrated.get("monitor_overrides"),
            rename_map,
        )
        next_monitor_inventory = _rewrite_keyed_node_section(
            migrated.get("monitor_inventory"),
            rename_map,
            sync_payload_node_id=True,
        )
        next_coordinator = _rewrite_coordinator_candidates(migrated.get("coordinator"), rename_map)
        if next_layout != migrated.get("layout"):
            migrated["layout"] = next_layout
            changed = True
        if next_monitor_overrides != migrated.get("monitor_overrides"):
            migrated["monitor_overrides"] = next_monitor_overrides
            changed = True
        if next_monitor_inventory != migrated.get("monitor_inventory"):
            migrated["monitor_inventory"] = next_monitor_inventory
            changed = True
        if next_coordinator != migrated.get("coordinator"):
            migrated["coordinator"] = next_coordinator
            changed = True

    return migrated, changed


def _strip_legacy_role_fields(node):
    if not isinstance(node, dict):
        return node
    migrated = dict(node)
    migrated.pop("roles", None)
    migrated.pop("role", None)
    return migrated


def _rewrite_keyed_node_section(
    section,
    rename_map: dict[str, str],
    *,
    sync_payload_node_id: bool = False,
):
    if not isinstance(section, dict):
        return section
    raw_nodes = section.get("nodes")
    if not isinstance(raw_nodes, dict):
        return section

    rewritten_nodes = {}
    for raw_key, raw_payload in raw_nodes.items():
        next_key = rename_map.get(str(raw_key), str(raw_key))
        if next_key in rewritten_nodes:
            raise ValueError(f"migration would create duplicate node key: {next_key}")
        payload = raw_payload if not isinstance(raw_payload, dict) else dict(raw_payload)
        if sync_payload_node_id and isinstance(payload, dict):
            raw_payload_node_id = str(payload.get("node_id") or "").strip()
            if not raw_payload_node_id or raw_payload_node_id == str(raw_key):
                payload["node_id"] = next_key
            elif raw_payload_node_id in rename_map:
                payload["node_id"] = rename_map[raw_payload_node_id]
        rewritten_nodes[next_key] = payload

    rewritten = dict(section)
    rewritten["nodes"] = rewritten_nodes
    return rewritten


def _rewrite_coordinator_candidates(section, rename_map: dict[str, str]):
    if not isinstance(section, dict):
        return section
    candidates = section.get("candidates")
    if not isinstance(candidates, list):
        return section
    rewritten = dict(section)
    rewritten["candidates"] = [
        rename_map.get(str(candidate), str(candidate))
        for candidate in candidates
    ]
    return rewritten


def _generate_migrated_node_id(node: dict, used_identifiers: set[str]) -> str:
    seed = "|".join(
        (
            str(node.get("name") or "").strip(),
            str(node.get("ip") or "").strip(),
            str(node.get("port") or "").strip(),
        )
    )
    suffix = 0
    while True:
        candidate_seed = seed if suffix == 0 else f"{seed}|{suffix}"
        candidate = str(uuid.uuid5(uuid.NAMESPACE_URL, f"multiscreenpass:{candidate_seed}"))
        if candidate not in used_identifiers:
            return candidate
        suffix += 1
