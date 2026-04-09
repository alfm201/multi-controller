"""Config loading and validation helpers."""

import json
import logging
import os
import sys
from pathlib import Path

ALLOWED_ROLES = frozenset({"controller", "target", "coordinator"})


def _candidate_paths(explicit_path=None):
    if explicit_path:
        yield Path(explicit_path)
        return

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        yield exe_dir / "config.json"

    project_root = Path(__file__).resolve().parent.parent
    yield project_root / "config.json"
    yield Path.cwd() / "config.json"


def resolve_config_path(explicit_path=None):
    tried = []
    for candidate in _candidate_paths(explicit_path):
        tried.append(str(candidate))
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("config.json was not found. Tried: " + ", ".join(tried))


def load_config(explicit_path=None):
    path = resolve_config_path(explicit_path)
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    validate_config(data)
    logging.info("[CONFIG] loaded from %s", path)
    return data, path


def save_config(config, path):
    validate_config(config)
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def _validate_roles(roles, field_name):
    if roles is None:
        return
    if not isinstance(roles, list):
        raise ValueError(f"{field_name} must be a list")
    unknown = [role for role in roles if role not in ALLOWED_ROLES]
    if unknown:
        raise ValueError(f"{field_name} has unknown roles: {unknown}")


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("config root must be an object")

    default_roles = config.get("default_roles")
    _validate_roles(default_roles, "config.default_roles")

    nodes = config.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("config.nodes must be a non-empty list")

    seen_names = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise ValueError(f"nodes[{index}] must be an object")
        for key in ("name", "ip", "port"):
            if key not in node:
                raise ValueError(f"nodes[{index}].{key} is required")

        name = node["name"]
        if not isinstance(name, str) or not name:
            raise ValueError(f"nodes[{index}].name must be a non-empty string")
        if name in seen_names:
            raise ValueError(f"nodes[{index}].name is duplicated: {name}")
        seen_names.add(name)

        if not isinstance(node["ip"], str) or not node["ip"]:
            raise ValueError(f"nodes[{index}].ip must be a non-empty string")

        try:
            port = int(node["port"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"nodes[{index}].port must be an integer") from exc
        if port <= 0:
            raise ValueError(f"nodes[{index}].port must be positive")

        _validate_roles(node.get("roles"), f"nodes[{index}].roles")

    coord = config.get("coordinator")
    if coord is not None:
        if not isinstance(coord, dict):
            raise ValueError("config.coordinator must be an object")
        candidates = coord.get("candidates", [])
        if not isinstance(candidates, list):
            raise ValueError("config.coordinator.candidates must be a list")
        for candidate in candidates:
            if candidate not in seen_names:
                raise ValueError(
                    f"coordinator.candidates entry '{candidate}' is not defined in nodes"
                )
