"""Pure edge routing decisions for self/target boundary transitions."""

from __future__ import annotations

from dataclasses import dataclass

from routing.routing_table import EdgeRoutingTable
from runtime.layouts import DisplayRef


@dataclass(frozen=True)
class EdgeRoute:
    kind: str
    destination: DisplayRef | None = None
    logical_neighbor_display_id: str | None = None
    reason: str | None = None


def describe_edge_route(route: EdgeRoute) -> str:
    """Return a compact debug string for one resolved route."""
    parts = [route.kind]
    if route.destination is not None:
        parts.append(f"dest={route.destination.node_id}:{route.destination.display_id}")
    if route.logical_neighbor_display_id is not None:
        parts.append(f"logical={route.logical_neighbor_display_id}")
    if route.reason:
        parts.append(f"reason={route.reason}")
    return " ".join(parts)


class EdgeRoutingResolver:
    """Cache layout-derived routing inputs so move handling stays cheap."""

    def __init__(self):
        self._layout_identity = None
        self._table: EdgeRoutingTable | None = None

    def resolve(
        self,
        *,
        layout,
        self_node_id: str,
        current_node_id: str,
        current_display_id: str,
        direction: str,
        cross_axis_ratio: float,
        is_target_online,
    ) -> EdgeRoute:
        table = self._ensure_table(layout)
        return resolve_edge_route(
            layout=layout,
            self_node_id=self_node_id,
            current_node_id=current_node_id,
            current_display_id=current_display_id,
            direction=direction,
            cross_axis_ratio=cross_axis_ratio,
            is_target_online=is_target_online,
            routing_table=table,
        )

    def _ensure_table(self, layout) -> EdgeRoutingTable:
        identity = id(layout)
        if self._table is None or self._layout_identity != identity:
            self._table = EdgeRoutingTable(layout)
            self._layout_identity = identity
        return self._table


def resolve_edge_route(
    *,
    layout,
    self_node_id: str,
    current_node_id: str,
    current_display_id: str,
    direction: str,
    cross_axis_ratio: float,
    is_target_online,
    routing_table: EdgeRoutingTable | None = None,
) -> EdgeRoute:
    """Resolve what should happen when the current display edge is pressed."""
    current_node = layout.get_node(current_node_id)
    if current_node is None:
        return EdgeRoute("allow")

    table = routing_table or EdgeRoutingTable(layout)
    slot = table.slot_for(current_node_id, current_display_id, direction)
    if slot is None:
        return EdgeRoute("allow")
    next_display = slot.pick_physical(cross_axis_ratio)
    logical_neighbor = slot.pick_logical_display_id(cross_axis_ratio)

    if next_display is None:
        if current_node_id == self_node_id:
            if logical_neighbor is not None:
                return EdgeRoute(
                    "block",
                    logical_neighbor_display_id=logical_neighbor,
                    reason="self-logical-gap",
                )
            return EdgeRoute("block", reason="self-dead-edge")
        if logical_neighbor is not None:
            return EdgeRoute(
                "block",
                logical_neighbor_display_id=logical_neighbor,
                reason="target-logical-gap",
            )
        return EdgeRoute("allow")

    if next_display.node_id == current_node_id:
        if next_display.display_id == current_display_id:
            return EdgeRoute("allow")
        return EdgeRoute("self-warp", destination=next_display)

    if next_display.node_id != self_node_id and not is_target_online(next_display.node_id):
        if current_node_id == self_node_id:
            return EdgeRoute("block", destination=next_display, reason="offline-target")
        return EdgeRoute("allow")

    return EdgeRoute("target-switch", destination=next_display)
