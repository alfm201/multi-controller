"""PC layout, monitor topology, and auto-switch configuration models."""

from __future__ import annotations

from dataclasses import dataclass

from runtime.monitor_inventory import (
    MonitorInventorySnapshot,
    deserialize_monitor_inventory_snapshot,
    snapshot_to_logical_rows,
)


@dataclass(frozen=True)
class AutoSwitchSettings:
    """Boundary-based auto target switching settings."""

    enabled: bool = True
    cooldown_ms: int = 250
    return_guard_ms: int = 350


@dataclass(frozen=True)
class LayoutDisplay:
    """A unit display tile in either logical or physical monitor space."""

    display_id: str
    x: int
    y: int
    width: int = 1
    height: int = 1

    @property
    def left(self) -> int:
        return self.x

    @property
    def top(self) -> int:
        return self.y

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height


@dataclass(frozen=True)
class MonitorTopology:
    """Logical and physical monitor layouts tied together by display_id."""

    logical: tuple[LayoutDisplay, ...]
    physical: tuple[LayoutDisplay, ...]

    def display_ids(self) -> tuple[str, ...]:
        return tuple(display.display_id for display in self.logical)

    def get_logical_display(self, display_id: str) -> LayoutDisplay | None:
        for display in self.logical:
            if display.display_id == display_id:
                return display
        return None

    def get_physical_display(self, display_id: str) -> LayoutDisplay | None:
        for display in self.physical:
            if display.display_id == display_id:
                return display
        return None


@dataclass(frozen=True)
class LayoutNode:
    """A PC node inside the shared 2D layout."""

    node_id: str
    x: int
    y: int
    width: int = 1
    height: int = 1
    monitor_topology: MonitorTopology | None = None
    monitor_source: str = "fallback"
    monitor_override_active: bool = False

    @property
    def left(self) -> int:
        return self.x

    @property
    def top(self) -> int:
        return self.y

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def monitors(self) -> MonitorTopology:
        if self.monitor_topology is not None:
            return self.monitor_topology
        return build_default_monitor_topology(self.width, self.height)


@dataclass(frozen=True)
class LayoutConfig:
    """Full PC layout and auto-switch settings."""

    nodes: tuple[LayoutNode, ...]
    auto_switch: AutoSwitchSettings = AutoSwitchSettings()

    def get_node(self, node_id: str) -> LayoutNode | None:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        return None


@dataclass(frozen=True)
class DisplayRef:
    """A specific display inside a node."""

    node_id: str
    display_id: str


def build_layout_config(config: dict, nodes) -> LayoutConfig:
    """Build a runtime layout model from config and known nodes."""
    layout_section = config.get("layout") or {}
    raw_nodes = layout_section.get("nodes") or {}
    override_nodes = (config.get("monitor_overrides") or {}).get("nodes") or {}
    inventory_nodes = (config.get("monitor_inventory") or {}).get("nodes") or {}
    auto_switch = _build_auto_switch_settings(layout_section.get("auto_switch") or {})

    layout_nodes = []
    for index, node in enumerate(nodes):
        raw = raw_nodes.get(node.node_id) or {}
        base_width = max(int(raw.get("width", 1)), 1)
        base_height = max(int(raw.get("height", 1)), 1)
        monitor_topology, monitor_source, override_active = resolve_monitor_topology(
            raw_layout_node=raw,
            raw_override=override_nodes.get(node.node_id),
            raw_inventory=inventory_nodes.get(node.node_id),
            fallback_width=base_width,
            fallback_height=base_height,
        )
        physical_min_x, physical_min_y, physical_max_x, physical_max_y = display_bounds(
            monitor_topology.physical
        )
        physical_width = max(physical_max_x - physical_min_x, 1)
        physical_height = max(physical_max_y - physical_min_y, 1)
        layout_nodes.append(
            LayoutNode(
                node_id=node.node_id,
                x=int(raw.get("x", index)),
                y=int(raw.get("y", 0)),
                width=physical_width,
                height=physical_height,
                monitor_topology=monitor_topology,
                monitor_source=monitor_source,
                monitor_override_active=override_active,
            )
        )

    return LayoutConfig(nodes=tuple(layout_nodes), auto_switch=auto_switch)


def serialize_layout_config(layout: LayoutConfig, *, include_monitor_maps: bool = True) -> dict:
    """Serialize a runtime layout model back to config shape."""
    data = {
        "nodes": {},
        "auto_switch": {
            "enabled": layout.auto_switch.enabled,
            "cooldown_ms": layout.auto_switch.cooldown_ms,
            "return_guard_ms": layout.auto_switch.return_guard_ms,
        },
    }
    for node in layout.nodes:
        node_payload = {
            "x": node.x,
            "y": node.y,
            "width": node.width,
            "height": node.height,
        }
        if include_monitor_maps:
            monitor_payload = serialize_monitor_topology(
                node.monitors(),
                width=node.width,
                height=node.height,
            )
            if monitor_payload is not None:
                node_payload["monitors"] = monitor_payload
        data["nodes"][node.node_id] = node_payload
    return data


def serialize_monitor_overrides(
    layout: LayoutConfig,
    monitor_inventories: dict[str, MonitorInventorySnapshot] | None,
) -> dict:
    """Persist only user physical overrides keyed by detected monitor ids."""
    monitor_inventories = {} if monitor_inventories is None else dict(monitor_inventories)
    nodes = {}
    for node in layout.nodes:
        snapshot = monitor_inventories.get(node.node_id)
        if snapshot is None or not snapshot.monitors:
            continue
        logical_rows = snapshot_to_logical_rows(snapshot)
        physical_rows = monitor_topology_to_rows(node.monitors(), logical=False)
        if _display_id_set(logical_rows) != _display_id_set(physical_rows):
            continue
        if logical_rows != physical_rows:
            nodes[node.node_id] = {"physical": physical_rows}
    return {"nodes": nodes} if nodes else {}


def replace_layout_node(layout: LayoutConfig, node_id: str, *, x: int, y: int) -> LayoutConfig:
    """Return a copy of the layout with one node moved."""
    nodes = []
    for node in layout.nodes:
        if node.node_id == node_id:
            nodes.append(
                LayoutNode(
                    node_id=node.node_id,
                    x=int(x),
                    y=int(y),
                    width=node.width,
                    height=node.height,
                    monitor_topology=node.monitor_topology,
                    monitor_source=node.monitor_source,
                    monitor_override_active=node.monitor_override_active,
                )
            )
        else:
            nodes.append(node)
    return LayoutConfig(nodes=tuple(nodes), auto_switch=layout.auto_switch)


def replace_layout_monitors(
    layout: LayoutConfig,
    node_id: str,
    *,
    logical_rows: list[list[str | None]],
    physical_rows: list[list[str | None]],
) -> LayoutConfig:
    """Return a copy of the layout with one node's monitor topology replaced."""
    topology = build_monitor_topology(
        {"logical": logical_rows, "physical": physical_rows},
        fallback_width=1,
        fallback_height=1,
    )
    _, _, physical_max_x, physical_max_y = display_bounds(topology.physical)
    nodes = []
    for node in layout.nodes:
        if node.node_id == node_id:
            nodes.append(
                LayoutNode(
                    node_id=node.node_id,
                    x=node.x,
                    y=node.y,
                    width=max(physical_max_x, 1),
                    height=max(physical_max_y, 1),
                    monitor_topology=topology,
                    monitor_source="manual",
                    monitor_override_active=monitor_topology_to_rows(topology, logical=True)
                    != monitor_topology_to_rows(topology, logical=False),
                )
            )
        else:
            nodes.append(node)
    return LayoutConfig(nodes=tuple(nodes), auto_switch=layout.auto_switch)


def replace_auto_switch_settings(
    layout: LayoutConfig,
    *,
    enabled: bool | None = None,
    cooldown_ms: int | None = None,
    return_guard_ms: int | None = None,
) -> LayoutConfig:
    """Return a copy of the layout with updated auto-switch settings."""
    current = layout.auto_switch
    return LayoutConfig(
        nodes=layout.nodes,
        auto_switch=AutoSwitchSettings(
            enabled=current.enabled if enabled is None else bool(enabled),
            cooldown_ms=current.cooldown_ms if cooldown_ms is None else int(cooldown_ms),
            return_guard_ms=(
                current.return_guard_ms if return_guard_ms is None else int(return_guard_ms)
            ),
        ),
    )


def append_layout_node(layout: LayoutConfig, node_id: str) -> LayoutConfig:
    """Append a new node at the next available x position."""
    if any(node.node_id == node_id for node in layout.nodes):
        raise ValueError(f"duplicate node id: {node_id}")
    next_x = max((node.right for node in layout.nodes), default=0)
    nodes = tuple(layout.nodes) + (
        LayoutNode(node_id=node_id, x=next_x, y=0, width=1, height=1),
    )
    return LayoutConfig(nodes=nodes, auto_switch=layout.auto_switch)


def rename_layout_node(layout: LayoutConfig, old_node_id: str, new_node_id: str) -> LayoutConfig:
    """Rename a layout node while preserving its geometry and monitor state."""
    if old_node_id == new_node_id:
        return layout
    if any(node.node_id == new_node_id for node in layout.nodes):
        raise ValueError(f"duplicate node id: {new_node_id}")
    nodes = []
    renamed = False
    for node in layout.nodes:
        if node.node_id == old_node_id:
            nodes.append(
                LayoutNode(
                    node_id=new_node_id,
                    x=node.x,
                    y=node.y,
                    width=node.width,
                    height=node.height,
                    monitor_topology=node.monitor_topology,
                    monitor_source=node.monitor_source,
                    monitor_override_active=node.monitor_override_active,
                )
            )
            renamed = True
        else:
            nodes.append(node)
    if not renamed:
        raise ValueError(f"unknown node id: {old_node_id}")
    return LayoutConfig(nodes=tuple(nodes), auto_switch=layout.auto_switch)


def remove_layout_node(layout: LayoutConfig, node_id: str) -> LayoutConfig:
    """Remove a node from the shared PC layout."""
    return LayoutConfig(
        nodes=tuple(node for node in layout.nodes if node.node_id != node_id),
        auto_switch=layout.auto_switch,
    )


def layout_bounds(layout: LayoutConfig) -> tuple[int, int, int, int]:
    """Return the outer bounds of the full PC layout."""
    if not layout.nodes:
        return (0, 0, 1, 1)
    min_x = min(node.left for node in layout.nodes)
    min_y = min(node.top for node in layout.nodes)
    max_x = max(node.right for node in layout.nodes)
    max_y = max(node.bottom for node in layout.nodes)
    return (min_x, min_y, max_x, max_y)


def display_bounds(displays: tuple[LayoutDisplay, ...]) -> tuple[int, int, int, int]:
    """Return bounds of a logical or physical display collection."""
    if not displays:
        return (0, 0, 1, 1)
    min_x = min(display.left for display in displays)
    min_y = min(display.top for display in displays)
    max_x = max(display.right for display in displays)
    max_y = max(display.bottom for display in displays)
    return (min_x, min_y, max_x, max_y)


def find_overlapping_nodes(layout: LayoutConfig) -> list[tuple[str, str]]:
    """Return node pairs that overlap in the shared physical PC layout."""
    overlaps = []
    for index, left in enumerate(layout.nodes):
        for right in layout.nodes[index + 1 :]:
            if _rectangles_overlap(left, right):
                overlaps.append((left.node_id, right.node_id))
    return overlaps


def find_adjacent_node(
    layout: LayoutConfig,
    current_node_id: str,
    direction: str,
    cross_axis_ratio: float,
) -> LayoutNode | None:
    """Find the neighboring node by global physical PC adjacency."""
    current = layout.get_node(current_node_id)
    if current is None:
        return None

    ratio = min(max(float(cross_axis_ratio), 0.0), 1.0)
    if direction in {"left", "right"}:
        point = current.top + ratio * current.height
        candidates = [
            node
            for node in layout.nodes
            if node.node_id != current.node_id and _is_horizontal_neighbor(current, node, direction)
        ]
        return _pick_by_vertical_overlap(candidates, point)

    if direction in {"up", "down"}:
        point = current.left + ratio * current.width
        candidates = [
            node
            for node in layout.nodes
            if node.node_id != current.node_id and _is_vertical_neighbor(current, node, direction)
        ]
        return _pick_by_horizontal_overlap(candidates, point)

    raise ValueError(f"unknown direction: {direction}")


def resolve_display_for_normalized_point(
    node: LayoutNode, x_norm: float, y_norm: float
) -> LayoutDisplay | None:
    """Resolve which logical display contains the normalized pointer position."""
    x_ratio = min(max(float(x_norm), 0.0), 1.0)
    y_ratio = min(max(float(y_norm), 0.0), 1.0)
    topology = node.monitors()
    if not topology.logical:
        return None
    min_x, min_y, max_x, max_y = display_bounds(topology.logical)
    width = max(max_x - min_x, 1)
    height = max(max_y - min_y, 1)

    containing = []
    for display in topology.logical:
        left = (display.left - min_x) / width
        right = (display.right - min_x) / width
        top = (display.top - min_y) / height
        bottom = (display.bottom - min_y) / height
        if left <= x_ratio <= right and top <= y_ratio <= bottom:
            containing.append((display, left, top, right, bottom))

    if containing:
        return min(
            containing,
            key=lambda entry: abs(((entry[1] + entry[3]) / 2) - x_ratio)
            + abs(((entry[2] + entry[4]) / 2) - y_ratio),
        )[0]

    return min(
        topology.logical,
        key=lambda display: abs(_display_center_x(display, min_x, width) - x_ratio)
        + abs(_display_center_y(display, min_y, height) - y_ratio),
    )


def detect_display_edge(
    node: LayoutNode,
    x_norm: float,
    y_norm: float,
    threshold: float,
) -> tuple[LayoutDisplay | None, str | None, float | None]:
    """Resolve the current logical display and its nearest edge hit."""
    display = resolve_display_for_normalized_point(node, x_norm, y_norm)
    if display is None:
        return None, None, None

    left, top, right, bottom = normalized_display_rect(node, display.display_id, logical=True)
    width = max(right - left, 1e-6)
    height = max(bottom - top, 1e-6)
    local_x = min(max((float(x_norm) - left) / width, 0.0), 1.0)
    local_y = min(max((float(y_norm) - top) / height, 0.0), 1.0)

    distances = [
        ("left", local_x, local_y),
        ("right", 1.0 - local_x, local_y),
        ("up", local_y, local_x),
        ("down", 1.0 - local_y, local_x),
    ]
    direction, distance, cross_ratio = min(distances, key=lambda entry: entry[1])
    if distance > float(threshold):
        return display, None, None
    return display, direction, min(max(cross_ratio, 0.0), 1.0)


def normalized_display_rect(
    node: LayoutNode,
    display_id: str,
    *,
    logical: bool,
) -> tuple[float, float, float, float]:
    """Return a display rect normalized into 0..1 for either logical or physical space."""
    topology = node.monitors()
    displays = topology.logical if logical else topology.physical
    target = (
        topology.get_logical_display(display_id)
        if logical
        else topology.get_physical_display(display_id)
    )
    if target is None:
        raise ValueError(f"unknown display_id={display_id!r} for node={node.node_id}")
    min_x, min_y, max_x, max_y = display_bounds(displays)
    width = max(max_x - min_x, 1)
    height = max(max_y - min_y, 1)
    return (
        (target.left - min_x) / width,
        (target.top - min_y) / height,
        (target.right - min_x) / width,
        (target.bottom - min_y) / height,
    )


def build_anchor_event(
    node: LayoutNode,
    display_id: str,
    direction: str,
    cross_axis_ratio: float,
    margin: float = 0.0,
) -> dict:
    """Build a normalized pointer anchor event pinned to a destination display edge."""
    left, top, right, bottom = normalized_display_rect(node, display_id, logical=True)
    width = max(right - left, 1e-6)
    height = max(bottom - top, 1e-6)
    ratio = min(max(float(cross_axis_ratio), 0.0), 1.0)

    if direction == "left":
        x_norm = right
        y_norm = top + (height * ratio)
    elif direction == "right":
        x_norm = left
        y_norm = top + (height * ratio)
    elif direction == "up":
        x_norm = left + (width * ratio)
        y_norm = bottom
    elif direction == "down":
        x_norm = left + (width * ratio)
        y_norm = top
    else:
        raise ValueError(f"unknown direction: {direction}")

    return {
        "kind": "mouse_move",
        "x_norm": min(max(x_norm, 0.0), 1.0),
        "y_norm": min(max(y_norm, 0.0), 1.0),
    }


def find_adjacent_display(
    layout: LayoutConfig,
    current_node_id: str,
    current_display_id: str,
    direction: str,
    cross_axis_ratio: float,
) -> DisplayRef | None:
    """Find the next physical display along the requested edge."""
    node = layout.get_node(current_node_id)
    if node is None:
        return None

    current = node.monitors().get_physical_display(current_display_id)
    if current is None:
        return None

    ratio = min(max(float(cross_axis_ratio), 0.0), 1.0)
    current_rect = _offset_display(current, node.x, node.y)
    if direction in {"left", "right"}:
        point = current_rect.top + ratio * current_rect.height
    elif direction in {"up", "down"}:
        point = current_rect.left + ratio * current_rect.width
    else:
        raise ValueError(f"unknown direction: {direction}")

    candidates = []
    for other_node in layout.nodes:
        for display in other_node.monitors().physical:
            if (
                other_node.node_id == current_node_id
                and display.display_id == current_display_id
            ):
                continue
            other_rect = _offset_display(display, other_node.x, other_node.y)
            if direction in {"left", "right"}:
                if not _is_horizontal_neighbor(current_rect, other_rect, direction):
                    continue
                candidates.append((other_node.node_id, display.display_id, other_rect))
            else:
                if not _is_vertical_neighbor(current_rect, other_rect, direction):
                    continue
                candidates.append((other_node.node_id, display.display_id, other_rect))

    if not candidates:
        return None

    if direction in {"left", "right"}:
        chosen = _pick_display_by_vertical_overlap(candidates, point)
    else:
        chosen = _pick_display_by_horizontal_overlap(candidates, point)
    if chosen is None:
        return None
    return DisplayRef(node_id=chosen[0], display_id=chosen[1])


def find_adjacent_display_in_node(
    node: LayoutNode,
    current_display_id: str,
    direction: str,
    cross_axis_ratio: float,
    *,
    logical: bool,
) -> str | None:
    """Find an adjacent display id inside one node using logical or physical topology."""
    topology = node.monitors()
    displays = topology.logical if logical else topology.physical
    current = (
        topology.get_logical_display(current_display_id)
        if logical
        else topology.get_physical_display(current_display_id)
    )
    if current is None:
        return None

    ratio = min(max(float(cross_axis_ratio), 0.0), 1.0)
    if direction in {"left", "right"}:
        point = current.top + ratio * current.height
        candidates = [
            display
            for display in displays
            if display.display_id != current_display_id and _is_horizontal_neighbor(current, display, direction)
        ]
        chosen = _pick_by_vertical_overlap(candidates, point)
    elif direction in {"up", "down"}:
        point = current.left + ratio * current.width
        candidates = [
            display
            for display in displays
            if display.display_id != current_display_id and _is_vertical_neighbor(current, display, direction)
        ]
        chosen = _pick_by_horizontal_overlap(candidates, point)
    else:
        raise ValueError(f"unknown direction: {direction}")
    return None if chosen is None else chosen.display_id


def monitor_topology_to_rows(
    topology: MonitorTopology, *, logical: bool
) -> list[list[str | None]]:
    """Render a monitor topology into editable grid rows."""
    displays = topology.logical if logical else topology.physical
    if not displays:
        return []
    min_x, min_y, max_x, max_y = display_bounds(displays)
    width = max(max_x - min_x, 1)
    height = max(max_y - min_y, 1)
    rows = [[None for _ in range(width)] for _ in range(height)]
    for display in displays:
        for dy in range(display.height):
            for dx in range(display.width):
                rows[display.y - min_y + dy][display.x - min_x + dx] = display.display_id
    return rows


def build_default_monitor_topology(width: int, height: int) -> MonitorTopology:
    """Build a row-major logical/physical monitor topology from node size."""
    total = max(int(width), 1) * max(int(height), 1)
    display_ids = [str(index + 1) for index in range(total)]
    physical_rows = []
    cursor = 0
    for _row in range(max(int(height), 1)):
        physical_rows.append(display_ids[cursor : cursor + max(int(width), 1)])
        cursor += max(int(width), 1)
    logical_rows = [list(display_ids)]
    return build_monitor_topology(
        {"logical": logical_rows, "physical": physical_rows},
        fallback_width=max(int(width), 1),
        fallback_height=max(int(height), 1),
    )


def build_monitor_topology(
    raw: dict | None, *, fallback_width: int, fallback_height: int
) -> MonitorTopology:
    """Parse optional monitor topology config."""
    if not raw:
        return build_default_monitor_topology_rows(
            width=max(int(fallback_width), 1),
            height=max(int(fallback_height), 1),
        )

    logical_rows = raw.get("logical")
    physical_rows = raw.get("physical")
    if logical_rows is None or physical_rows is None:
        raise ValueError("monitors.logical and monitors.physical are both required")

    logical = _rows_to_displays(logical_rows)
    physical = _rows_to_displays(physical_rows)
    logical_ids = {display.display_id for display in logical}
    physical_ids = {display.display_id for display in physical}
    if not logical_ids:
        raise ValueError("monitor topology must contain at least one display")
    if logical_ids != physical_ids:
        raise ValueError("logical and physical monitor layouts must use the same display ids")
    return MonitorTopology(logical=logical, physical=physical)


def build_default_monitor_topology_rows(*, width: int, height: int) -> MonitorTopology:
    """Helper used by the default/fallback monitor topology path."""
    display_ids = [str(index + 1) for index in range(width * height)]
    physical_rows = []
    cursor = 0
    for _row in range(height):
        physical_rows.append(display_ids[cursor : cursor + width])
        cursor += width
    logical_rows = [list(display_ids)]
    return MonitorTopology(
        logical=_rows_to_displays(logical_rows),
        physical=_rows_to_displays(physical_rows),
    )


def serialize_monitor_topology(
    topology: MonitorTopology,
    *,
    width: int,
    height: int,
) -> dict | None:
    """Serialize monitor topology only when it differs from the default view."""
    default_topology = build_default_monitor_topology(width, height)
    logical_rows = monitor_topology_to_rows(topology, logical=True)
    physical_rows = monitor_topology_to_rows(topology, logical=False)
    default_logical_rows = monitor_topology_to_rows(default_topology, logical=True)
    default_physical_rows = monitor_topology_to_rows(default_topology, logical=False)
    if logical_rows == default_logical_rows and physical_rows == default_physical_rows:
        return None
    return {
        "logical": logical_rows,
        "physical": physical_rows,
    }


def resolve_monitor_topology(
    *,
    raw_layout_node: dict,
    raw_override: dict | None,
    raw_inventory: dict | None,
    fallback_width: int,
    fallback_height: int,
) -> tuple[MonitorTopology, str, bool]:
    """Resolve runtime monitor topology from detected inventory and overrides."""
    snapshot = _deserialize_monitor_inventory(raw_inventory)
    if snapshot is not None and snapshot.monitors:
        logical_rows = snapshot_to_logical_rows(snapshot)
        physical_rows = _resolve_override_rows(raw_override, logical_rows)
        return (
            build_monitor_topology(
                {"logical": logical_rows, "physical": physical_rows},
                fallback_width=max(fallback_width, 1),
                fallback_height=max(fallback_height, 1),
            ),
            "detected_override" if physical_rows != logical_rows else "detected",
            physical_rows != logical_rows,
        )

    raw_monitors = raw_layout_node.get("monitors")
    if raw_monitors:
        return (
            build_monitor_topology(
                raw_monitors,
                fallback_width=max(fallback_width, 1),
                fallback_height=max(fallback_height, 1),
            ),
            "legacy",
            False,
        )

    return (
        build_default_monitor_topology_rows(
            width=max(int(fallback_width), 1),
            height=max(int(fallback_height), 1),
        ),
        "fallback",
        False,
    )


def _deserialize_monitor_inventory(
    raw_inventory: dict | None,
) -> MonitorInventorySnapshot | None:
    if not isinstance(raw_inventory, dict):
        return None
    snapshot = deserialize_monitor_inventory_snapshot(raw_inventory)
    if not snapshot.node_id and raw_inventory.get("node_id"):
        return None
    return snapshot


def _resolve_override_rows(
    raw_override: dict | None,
    logical_rows: list[list[str | None]],
) -> list[list[str | None]]:
    if not isinstance(raw_override, dict):
        return logical_rows
    physical_rows = raw_override.get("physical")
    if not isinstance(physical_rows, list):
        return logical_rows
    if _display_id_set(physical_rows) != _display_id_set(logical_rows):
        return logical_rows
    return [list(row) for row in physical_rows]


def _display_id_set(rows: list[list[str | None]]) -> set[str]:
    seen = set()
    for row in rows:
        for cell in row:
            if cell not in (None, "", "."):
                seen.add(str(cell).strip())
    return seen


def _rows_to_displays(rows: list[list[str | None]]) -> tuple[LayoutDisplay, ...]:
    if not isinstance(rows, list) or not rows:
        raise ValueError("monitor layout rows must be a non-empty list")
    displays = []
    seen = set()
    for y, row in enumerate(rows):
        if not isinstance(row, list) or not row:
            raise ValueError("monitor layout rows must be non-empty lists")
        for x, cell in enumerate(row):
            if cell in (None, "", "."):
                continue
            display_id = str(cell).strip()
            if not display_id:
                continue
            if display_id in seen:
                raise ValueError(f"duplicate display id: {display_id}")
            seen.add(display_id)
            displays.append(LayoutDisplay(display_id=display_id, x=x, y=y))
    return tuple(displays)


def _build_auto_switch_settings(raw: dict) -> AutoSwitchSettings:
    return AutoSwitchSettings(
        enabled=bool(raw.get("enabled", True)),
        cooldown_ms=int(raw.get("cooldown_ms", 250)),
        return_guard_ms=int(raw.get("return_guard_ms", 350)),
    )


def _is_horizontal_neighbor(current, other, direction: str) -> bool:
    if direction == "left":
        if other.right != current.left:
            return False
    elif direction == "right":
        if other.left != current.right:
            return False
    else:
        return False
    return max(current.top, other.top) < min(current.bottom, other.bottom)


def _is_vertical_neighbor(current, other, direction: str) -> bool:
    if direction == "up":
        if other.bottom != current.top:
            return False
    elif direction == "down":
        if other.top != current.bottom:
            return False
    else:
        return False
    return max(current.left, other.left) < min(current.right, other.right)


def _pick_by_vertical_overlap(candidates: list, point: float):
    if not candidates:
        return None
    containing = [node for node in candidates if node.top <= point < node.bottom]
    if containing:
        return min(containing, key=lambda node: abs((node.top + node.bottom) / 2 - point))
    return min(candidates, key=lambda node: abs((node.top + node.bottom) / 2 - point))


def _pick_by_horizontal_overlap(candidates: list, point: float):
    if not candidates:
        return None
    containing = [node for node in candidates if node.left <= point < node.right]
    if containing:
        return min(containing, key=lambda node: abs((node.left + node.right) / 2 - point))
    return min(candidates, key=lambda node: abs((node.left + node.right) / 2 - point))


def _pick_display_by_vertical_overlap(
    candidates: list[tuple[str, str, LayoutDisplay]], point: float
):
    if not candidates:
        return None
    containing = [entry for entry in candidates if entry[2].top <= point < entry[2].bottom]
    if containing:
        return min(
            containing, key=lambda entry: abs((entry[2].top + entry[2].bottom) / 2 - point)
        )
    return min(candidates, key=lambda entry: abs((entry[2].top + entry[2].bottom) / 2 - point))


def _pick_display_by_horizontal_overlap(
    candidates: list[tuple[str, str, LayoutDisplay]], point: float
):
    if not candidates:
        return None
    containing = [entry for entry in candidates if entry[2].left <= point < entry[2].right]
    if containing:
        return min(
            containing, key=lambda entry: abs((entry[2].left + entry[2].right) / 2 - point)
        )
    return min(
        candidates, key=lambda entry: abs((entry[2].left + entry[2].right) / 2 - point)
    )


def _display_center_x(display: LayoutDisplay, min_x: int, width: int) -> float:
    return ((display.left + display.right) / 2 - min_x) / width


def _display_center_y(display: LayoutDisplay, min_y: int, height: int) -> float:
    return ((display.top + display.bottom) / 2 - min_y) / height


def _offset_display(display: LayoutDisplay, x_offset: int, y_offset: int) -> LayoutDisplay:
    return LayoutDisplay(
        display_id=display.display_id,
        x=display.x + x_offset,
        y=display.y + y_offset,
        width=display.width,
        height=display.height,
    )


def _rectangles_overlap(left, right) -> bool:
    return (
        left.left < right.right
        and left.right > right.left
        and left.top < right.bottom
        and left.bottom > right.top
    )
