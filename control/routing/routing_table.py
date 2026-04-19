"""Precomputed edge-routing lookup tables derived from layout geometry."""

from __future__ import annotations

from dataclasses import dataclass

from model.display.layouts import DisplayRef


@dataclass(frozen=True)
class EdgeCandidate:
    """One candidate destination reachable from an edge."""

    node_id: str
    display_id: str
    range_start: float
    range_end: float
    center: float


@dataclass(frozen=True)
class EdgeSlot:
    """Precomputed routing inputs for one node/display/edge."""

    axis_start: float
    axis_end: float
    physical_candidates: tuple[EdgeCandidate, ...]
    logical_candidates: tuple[EdgeCandidate, ...]

    def point_for_ratio(self, cross_axis_ratio: float) -> float:
        ratio = min(max(float(cross_axis_ratio), 0.0), 1.0)
        span = max(self.axis_end - self.axis_start, 0.0)
        return self.axis_start + ratio * span

    def pick_physical(self, cross_axis_ratio: float) -> DisplayRef | None:
        chosen = _pick_candidate(self.physical_candidates, self.point_for_ratio(cross_axis_ratio))
        if chosen is None:
            return None
        return DisplayRef(node_id=chosen.node_id, display_id=chosen.display_id)

    def pick_logical_display_id(self, cross_axis_ratio: float) -> str | None:
        chosen = _pick_candidate(self.logical_candidates, self.point_for_ratio(cross_axis_ratio))
        if chosen is None:
            return None
        return chosen.display_id


class EdgeRoutingTable:
    """Precompute edge candidates for a given immutable layout."""

    def __init__(self, layout):
        self.layout = layout
        self._slots = _build_slots(layout)

    def slot_for(self, node_id: str, display_id: str, direction: str) -> EdgeSlot | None:
        return self._slots.get((node_id, display_id, direction))


def _build_slots(layout) -> dict[tuple[str, str, str], EdgeSlot]:
    slots = {}
    for node in layout.nodes:
        logical_displays = tuple(node.monitors().logical)
        physical_displays = tuple(node.monitors().physical)
        logical_by_id = {display.display_id: display for display in logical_displays}
        for current_display in physical_displays:
            current_rect = _offset_rect(current_display, node.x, node.y)
            current_logical = logical_by_id.get(current_display.display_id)
            for direction in ("left", "right", "up", "down"):
                axis_start, axis_end = _axis_range(current_rect, direction)
                physical_candidates = []
                for other_node in layout.nodes:
                    for other_display in other_node.monitors().physical:
                        if other_node.node_id == node.node_id and other_display.display_id == current_display.display_id:
                            continue
                        other_rect = _offset_rect(other_display, other_node.x, other_node.y)
                        if _is_neighbor(current_rect, other_rect, direction):
                            physical_candidates.append(
                                EdgeCandidate(
                                    node_id=other_node.node_id,
                                    display_id=other_display.display_id,
                                    range_start=_candidate_range_start(other_rect, direction),
                                    range_end=_candidate_range_end(other_rect, direction),
                                    center=_candidate_center(other_rect, direction),
                                )
                            )

                logical_candidates = []
                if current_logical is not None:
                    for other_display in logical_displays:
                        if other_display.display_id == current_logical.display_id:
                            continue
                        if _is_neighbor(current_logical, other_display, direction):
                            logical_candidates.append(
                                EdgeCandidate(
                                    node_id=node.node_id,
                                    display_id=other_display.display_id,
                                    range_start=_candidate_range_start(other_display, direction),
                                    range_end=_candidate_range_end(other_display, direction),
                                    center=_candidate_center(other_display, direction),
                                )
                            )

                slots[(node.node_id, current_display.display_id, direction)] = EdgeSlot(
                    axis_start=axis_start,
                    axis_end=axis_end,
                    physical_candidates=tuple(physical_candidates),
                    logical_candidates=tuple(logical_candidates),
                )
    return slots


def _offset_rect(display, offset_x: int, offset_y: int):
    return _Rect(
        left=display.left + offset_x,
        top=display.top + offset_y,
        right=display.right + offset_x,
        bottom=display.bottom + offset_y,
    )


@dataclass(frozen=True)
class _Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


def _axis_range(rect, direction: str) -> tuple[float, float]:
    if direction in {"left", "right"}:
        return float(rect.top), float(rect.bottom)
    return float(rect.left), float(rect.right)


def _candidate_range_start(rect, direction: str) -> float:
    return float(rect.top if direction in {"left", "right"} else rect.left)


def _candidate_range_end(rect, direction: str) -> float:
    return float(rect.bottom if direction in {"left", "right"} else rect.right)


def _candidate_center(rect, direction: str) -> float:
    if direction in {"left", "right"}:
        return (rect.top + rect.bottom) / 2
    return (rect.left + rect.right) / 2


def _is_neighbor(current, other, direction: str) -> bool:
    if direction == "left":
        if other.right != current.left:
            return False
        return max(current.top, other.top) < min(current.bottom, other.bottom)
    if direction == "right":
        if other.left != current.right:
            return False
        return max(current.top, other.top) < min(current.bottom, other.bottom)
    if direction == "up":
        if other.bottom != current.top:
            return False
        return max(current.left, other.left) < min(current.right, other.right)
    if direction == "down":
        if other.top != current.bottom:
            return False
        return max(current.left, other.left) < min(current.right, other.right)
    return False


def _pick_candidate(candidates: tuple[EdgeCandidate, ...], point: float) -> EdgeCandidate | None:
    if not candidates:
        return None
    containing = [candidate for candidate in candidates if candidate.range_start <= point < candidate.range_end]
    if containing:
        return min(containing, key=lambda candidate: abs(candidate.center - point))
    return min(candidates, key=lambda candidate: abs(candidate.center - point))
