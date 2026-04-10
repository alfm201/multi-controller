"""Config loading and validation helpers."""

import json
import logging
import os
import sys
from pathlib import Path

ALLOWED_ROLES = frozenset({"controller", "target"})


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
    if coord is not None and not isinstance(coord, dict):
        raise ValueError("config.coordinator must be an object")

    _validate_layout(config, seen_names)


def _validate_layout(config, known_node_names):
    layout = config.get("layout")
    if layout is None:
        return
    if not isinstance(layout, dict):
        raise ValueError("config.layout must be an object")

    layout_nodes = layout.get("nodes")
    if layout_nodes is not None:
        if not isinstance(layout_nodes, dict):
            raise ValueError("config.layout.nodes must be an object")
        for node_id, layout_node in layout_nodes.items():
            if node_id not in known_node_names:
                raise ValueError(f"config.layout.nodes has unknown node: {node_id}")
            if not isinstance(layout_node, dict):
                raise ValueError(f"config.layout.nodes.{node_id} must be an object")
            _validate_layout_int(layout_node, "x", node_id)
            _validate_layout_int(layout_node, "y", node_id)
            _validate_layout_int(layout_node, "width", node_id, positive=True)
            _validate_layout_int(layout_node, "height", node_id, positive=True)

    auto_switch = layout.get("auto_switch")
    if auto_switch is not None:
        if not isinstance(auto_switch, dict):
            raise ValueError("config.layout.auto_switch must be an object")
        if "enabled" in auto_switch and not isinstance(auto_switch["enabled"], bool):
            raise ValueError("config.layout.auto_switch.enabled must be a boolean")
        _validate_layout_float(
            auto_switch,
            "edge_threshold",
            minimum=0.0,
            maximum=0.25,
        )
        _validate_layout_float(
            auto_switch,
            "warp_margin",
            minimum=0.0,
            maximum=0.25,
        )
        _validate_layout_int(auto_switch, "cooldown_ms", "auto_switch", minimum=0)
        _validate_layout_int(auto_switch, "return_guard_ms", "auto_switch", minimum=0)
        _validate_layout_float(
            auto_switch,
            "anchor_dead_zone",
            minimum=0.0,
            maximum=0.5,
        )

    for node_id, layout_node in (layout_nodes or {}).items():
        monitors = layout_node.get("monitors")
        if monitors is None:
            continue
        _validate_monitor_topology(monitors, node_id)


def _validate_layout_int(data, key, label, positive=False, minimum=None):
    if key not in data:
        return
    try:
        value = int(data[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}.{key} must be an integer") from exc
    if str(value) != str(data[key]).strip():
        # float like 1.5 should not silently pass here
        if not isinstance(data[key], int):
            raise ValueError(f"{label}.{key} must be an integer")
    if positive and value <= 0:
        raise ValueError(f"{label}.{key} must be positive")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label}.{key} must be >= {minimum}")


def _validate_layout_float(data, key, minimum, maximum):
    if key not in data:
        return
    try:
        value = float(data[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"config.layout.auto_switch.{key} must be a number") from exc
    if value < minimum or value > maximum:
        raise ValueError(
            f"config.layout.auto_switch.{key} must be between {minimum} and {maximum}"
        )


def _validate_monitor_topology(monitors, node_id):
    if not isinstance(monitors, dict):
        raise ValueError(f"{node_id}.monitors must be an object")
    logical = monitors.get("logical")
    physical = monitors.get("physical")
    if logical is None or physical is None:
        raise ValueError(f"{node_id}.monitors must define logical and physical")

    logical_ids = _validate_monitor_grid(logical, f"{node_id}.monitors.logical")
    physical_ids = _validate_monitor_grid(physical, f"{node_id}.monitors.physical")
    if logical_ids != physical_ids:
        raise ValueError(f"{node_id}.monitors logical/physical ids must match")


def _validate_monitor_grid(rows, label):
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{label} must be a non-empty list")
    seen = set()
    for row_index, row in enumerate(rows):
        if not isinstance(row, list) or not row:
            raise ValueError(f"{label}[{row_index}] must be a non-empty list")
        for col_index, cell in enumerate(row):
            if cell in (None, "", "."):
                continue
            if not isinstance(cell, (str, int)):
                raise ValueError(f"{label}[{row_index}][{col_index}] must be string, int, or empty")
            display_id = str(cell).strip()
            if not display_id:
                continue
            if display_id in seen:
                raise ValueError(f"{label} has duplicate display id: {display_id}")
            seen.add(display_id)
    if not seen:
        raise ValueError(f"{label} must contain at least one display id")
    return seen
