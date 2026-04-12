"""Config loading and validation helpers."""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
from pathlib import Path

from runtime.app_identity import APP_EXECUTABLE_NAME
from runtime.app_settings import load_app_settings
from runtime.monitor_inventory import deserialize_monitor_inventory_snapshot
from runtime.self_detect import get_local_ips

ALLOWED_ROLES = frozenset({"controller", "target"})
CONFIG_DIRNAME = "config"
CONFIG_FILENAME = "config.json"
LAYOUT_FILENAME = "layout.json"
MONITOR_OVERRIDES_FILENAME = "monitor_overrides.json"
MONITOR_INVENTORY_FILENAME = "monitor_inventory.json"
DEFAULT_LISTEN_PORT = 45873


def _candidate_paths(explicit_path=None):
    if explicit_path:
        yield Path(explicit_path)
        return

    if getattr(sys, "frozen", False):
        yield _user_config_path()
        return

    project_root = Path(__file__).resolve().parent.parent
    yield project_root / CONFIG_DIRNAME / CONFIG_FILENAME
    yield project_root / CONFIG_FILENAME
    yield Path.cwd() / CONFIG_DIRNAME / CONFIG_FILENAME
    yield Path.cwd() / CONFIG_FILENAME


def resolve_config_path(explicit_path=None):
    tried = []
    for candidate in _candidate_paths(explicit_path):
        tried.append(str(candidate))
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"{CONFIG_FILENAME} was not found. Tried: "
        + ", ".join(tried)
        + ". Run 'python main.py --init-config' to create a starter config."
    )


def default_config_path(explicit_path=None) -> Path:
    if explicit_path:
        return Path(explicit_path)
    if getattr(sys, "frozen", False):
        return _user_config_path()
    project_root = Path(__file__).resolve().parent.parent
    return project_root / CONFIG_DIRNAME / CONFIG_FILENAME


def ensure_runtime_config(explicit_path=None, *, override_name: str | None = None):
    try:
        config_path = resolve_config_path(explicit_path)
    except FileNotFoundError:
        config_path = default_config_path(explicit_path)
        local_node = _build_local_node(existing_nodes=(), override_name=override_name)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        save_config({"nodes": [local_node]}, config_path)
        logging.info(
            "[CONFIG] created starter config for local node %s at %s",
            local_node["name"],
            config_path,
        )
        return load_config(config_path)

    config, resolved_path = load_config(config_path)
    next_config = _ensure_local_node_present(config, override_name=override_name)
    if next_config is None:
        return config, resolved_path

    save_config(next_config, resolved_path)
    action = "updated" if _has_hostname_match(config.get("nodes") or []) else "added"
    logging.info("[CONFIG] %s local node in %s", action, resolved_path)
    return load_config(resolved_path)


def related_config_paths(config_path) -> dict[str, Path]:
    config_path = Path(config_path)
    base_dir = config_path.resolve().parent
    return {
        "config": config_path.resolve(),
        "layout": base_dir / LAYOUT_FILENAME,
        "monitor_overrides": base_dir / MONITOR_OVERRIDES_FILENAME,
        "monitor_inventory": base_dir / MONITOR_INVENTORY_FILENAME,
    }


def build_starter_config(
    *,
    node_name: str = "A",
    ip: str = "127.0.0.1",
    port: int = DEFAULT_LISTEN_PORT,
) -> dict:
    return {
        "nodes": [
            {
                "name": node_name,
                "ip": ip,
                "port": int(port),
            }
        ]
    }


def init_config(
    explicit_path=None,
    *,
    overwrite: bool = False,
    node_name: str = "A",
    ip: str = "127.0.0.1",
    port: int = DEFAULT_LISTEN_PORT,
) -> Path:
    path = default_config_path(explicit_path)
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    save_config(build_starter_config(node_name=node_name, ip=ip, port=port), path)
    return path


def migrate_config(
    source_path=None,
    *,
    destination_path=None,
    overwrite: bool = False,
) -> tuple[Path, Path]:
    config, resolved_source = load_config(source_path)
    destination = (
        default_config_path(destination_path)
        if destination_path is not None
        else _default_migration_destination(resolved_source)
    )
    if destination.exists() and destination != resolved_source and not overwrite:
        raise FileExistsError(f"{destination} already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    save_config(config, destination)
    return resolved_source, destination


def validate_config_file(explicit_path=None) -> tuple[dict, Path]:
    return load_config(explicit_path)


def load_config(explicit_path=None):
    path = resolve_config_path(explicit_path)
    paths = related_config_paths(path)
    data = _read_json(paths["config"])
    layout = _read_optional_json(paths["layout"])
    monitor_overrides = _read_optional_json(paths["monitor_overrides"])
    monitor_inventory = _read_optional_json(paths["monitor_inventory"])
    if layout is not None:
        data["layout"] = layout
    if monitor_overrides is not None:
        data["monitor_overrides"] = monitor_overrides
    if monitor_inventory is not None:
        data["monitor_inventory"] = monitor_inventory
    validate_config(data)
    logging.info("[CONFIG] loaded from %s", path)
    return data, path


def save_config(config, path):
    validate_config(config)
    paths = related_config_paths(path)
    base_config = {
        key: value
        for key, value in config.items()
        if key not in {"layout", "monitor_overrides", "monitor_inventory"}
    }
    _write_json_atomic(paths["config"], base_config)
    _write_section(paths["layout"], config.get("layout"))
    _write_section(paths["monitor_overrides"], config.get("monitor_overrides"))
    _write_section(paths["monitor_inventory"], config.get("monitor_inventory"))


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("config root must be an object")

    default_roles = config.get("default_roles")
    _validate_roles(default_roles, "config.default_roles")

    nodes = config.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("config.nodes must be a non-empty list")

    seen_names = set()
    seen_ips = set()
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
        ip = node["ip"].strip()
        if ip in seen_ips:
            raise ValueError(f"nodes[{index}].ip is duplicated: {ip}")
        seen_ips.add(ip)

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
    _validate_monitor_overrides(config, seen_names)
    _validate_monitor_inventory(config, seen_names)
    _validate_settings(config)


def _validate_roles(roles, field_name):
    if roles is None:
        return
    if not isinstance(roles, list):
        raise ValueError(f"{field_name} must be a list")
    unknown = [role for role in roles if role not in ALLOWED_ROLES]
    if unknown:
        raise ValueError(f"{field_name} has unknown roles: {unknown}")


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
            monitors = layout_node.get("monitors")
            if monitors is not None:
                _validate_monitor_topology(monitors, node_id)

    auto_switch = layout.get("auto_switch")
    if auto_switch is not None:
        if not isinstance(auto_switch, dict):
            raise ValueError("config.layout.auto_switch must be an object")
        if "enabled" in auto_switch and not isinstance(auto_switch["enabled"], bool):
            raise ValueError("config.layout.auto_switch.enabled must be a boolean")
        _validate_layout_int(auto_switch, "cooldown_ms", "auto_switch", minimum=0)
        _validate_layout_int(auto_switch, "return_guard_ms", "auto_switch", minimum=0)


def _validate_monitor_overrides(config, known_node_names):
    overrides = config.get("monitor_overrides")
    if overrides is None:
        return
    if not isinstance(overrides, dict):
        raise ValueError("config.monitor_overrides must be an object")
    nodes = overrides.get("nodes")
    if nodes is None:
        return
    if not isinstance(nodes, dict):
        raise ValueError("config.monitor_overrides.nodes must be an object")
    for node_id, payload in nodes.items():
        if node_id not in known_node_names:
            raise ValueError(f"config.monitor_overrides.nodes has unknown node: {node_id}")
        if not isinstance(payload, dict):
            raise ValueError(f"config.monitor_overrides.nodes.{node_id} must be an object")
        physical = payload.get("physical")
        if physical is None:
            raise ValueError(f"{node_id}.monitor_overrides.physical is required")
        _validate_monitor_grid(physical, f"{node_id}.monitor_overrides.physical")


def _validate_monitor_inventory(config, known_node_names):
    inventories = config.get("monitor_inventory")
    if inventories is None:
        return
    if not isinstance(inventories, dict):
        raise ValueError("config.monitor_inventory must be an object")
    nodes = inventories.get("nodes")
    if nodes is None:
        return
    if not isinstance(nodes, dict):
        raise ValueError("config.monitor_inventory.nodes must be an object")
    for node_id, payload in nodes.items():
        if node_id not in known_node_names:
            raise ValueError(f"config.monitor_inventory.nodes has unknown node: {node_id}")
        if not isinstance(payload, dict):
            raise ValueError(f"config.monitor_inventory.nodes.{node_id} must be an object")
        snapshot = deserialize_monitor_inventory_snapshot(payload)
        if snapshot.node_id and snapshot.node_id != node_id:
            raise ValueError(f"config.monitor_inventory.nodes.{node_id}.node_id must match key")


def _validate_settings(config):
    settings = config.get("settings")
    if settings is None:
        return
    if not isinstance(settings, dict):
        raise ValueError("config.settings must be an object")
    load_app_settings(config)


def _validate_layout_int(data, key, label, positive=False, minimum=None):
    if key not in data:
        return
    try:
        value = int(data[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}.{key} must be an integer") from exc
    if str(value) != str(data[key]).strip():
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
                raise ValueError(
                    f"{label}[{row_index}][{col_index}] must be string, int, or empty"
                )
            display_id = str(cell).strip()
            if not display_id:
                continue
            if display_id in seen:
                raise ValueError(f"{label} has duplicate display id: {display_id}")
            seen.add(display_id)
    if not seen:
        raise ValueError(f"{label} must contain at least one display id")
    return seen


def _read_json(path: Path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_optional_json(path: Path):
    if not path.is_file():
        return None
    return _read_json(path)


def _write_section(path: Path, payload):
    if _is_empty_section(payload):
        if path.exists():
            path.unlink()
        return
    _write_json_atomic(path, payload)


def _write_json_atomic(path: Path, payload):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def _is_empty_section(payload) -> bool:
    if payload is None:
        return True
    if isinstance(payload, dict) and not payload:
        return True
    if isinstance(payload, dict) and payload.keys() == {"nodes"} and not payload["nodes"]:
        return True
    return False


def _default_migration_destination(source_path: Path) -> Path:
    source_path = Path(source_path)
    if source_path.parent.name == CONFIG_DIRNAME and source_path.name == CONFIG_FILENAME:
        return source_path
    return source_path.parent / CONFIG_DIRNAME / CONFIG_FILENAME


def _user_config_path() -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if local_appdata:
        return Path(local_appdata) / APP_EXECUTABLE_NAME / CONFIG_DIRNAME / CONFIG_FILENAME
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        return exe_dir / CONFIG_DIRNAME / CONFIG_FILENAME
    project_root = Path(__file__).resolve().parent.parent
    return project_root / CONFIG_DIRNAME / CONFIG_FILENAME


def _ensure_local_node_present(config: dict, *, override_name: str | None) -> dict | None:
    nodes = list(config.get("nodes") or [])
    if _has_local_node_match(nodes, override_name=override_name):
        return None

    next_config = dict(config)
    next_nodes = [dict(node) if isinstance(node, dict) else node for node in nodes]
    local_ip = _preferred_local_ip()
    hostname = socket.gethostname().strip() or "LOCALHOST"

    if not override_name:
        for index, node in enumerate(next_nodes):
            if not isinstance(node, dict):
                continue
            name = str(node.get("name") or "").strip()
            if name.lower() != hostname.lower():
                continue
            updated = dict(node)
            updated["ip"] = local_ip
            try:
                port = int(updated.get("port", DEFAULT_LISTEN_PORT))
            except (TypeError, ValueError):
                port = DEFAULT_LISTEN_PORT
            used_ports = _used_ports_for_ip(next_nodes, local_ip, skip_name=name)
            while port in used_ports:
                port += 1
            updated["port"] = port
            next_nodes[index] = updated
            next_config["nodes"] = next_nodes
            return next_config

    next_nodes.append(_build_local_node(existing_nodes=next_nodes, override_name=override_name))
    next_config["nodes"] = next_nodes
    return next_config


def _has_local_node_match(nodes, *, override_name: str | None) -> bool:
    if override_name:
        return any(
            isinstance(node, dict) and str(node.get("name") or "").strip() == override_name
            for node in nodes
        )
    local_ips = get_local_ips()
    return any(
        isinstance(node, dict) and str(node.get("ip") or "").strip() in local_ips
        for node in nodes
    )


def _has_hostname_match(nodes) -> bool:
    hostname = socket.gethostname().strip()
    if not hostname:
        return False
    return any(
        isinstance(node, dict)
        and str(node.get("name") or "").strip().lower() == hostname.lower()
        for node in nodes
    )


def _build_local_node(*, existing_nodes, override_name: str | None) -> dict:
    local_ip = _preferred_local_ip()
    requested_name = (socket.gethostname() or "LOCALHOST").strip() or "LOCALHOST"
    name = _unique_node_name(requested_name, existing_nodes)
    port = _choose_listen_port(existing_nodes, local_ip)
    return {
        "name": name,
        "ip": local_ip,
        "port": port,
    }


def _preferred_local_ip() -> str:
    ips = sorted(ip for ip in get_local_ips() if ip and ip != "127.0.0.1")
    return ips[0] if ips else "127.0.0.1"


def _choose_listen_port(existing_nodes, local_ip: str) -> int:
    used_ports = _used_ports_for_ip(existing_nodes, local_ip)
    port = DEFAULT_LISTEN_PORT
    while port in used_ports:
        port += 1
    return port


def _used_ports_for_ip(existing_nodes, local_ip: str, *, skip_name: str | None = None) -> set[int]:
    used_ports: set[int] = set()
    for node in existing_nodes:
        if not isinstance(node, dict):
            continue
        if str(node.get("ip") or "").strip() != local_ip:
            continue
        if skip_name is not None and str(node.get("name") or "").strip() == skip_name:
            continue
        try:
            port = int(node.get("port"))
        except (TypeError, ValueError):
            continue
        if port > 0:
            used_ports.add(port)
    return used_ports


def _unique_node_name(name: str, existing_nodes) -> str:
    existing_names = {
        str(node.get("name") or "").strip()
        for node in existing_nodes
        if isinstance(node, dict)
    }
    if name not in existing_names:
        return name
    suffix = 2
    while f"{name}-{suffix}" in existing_names:
        suffix += 1
    return f"{name}-{suffix}"
