"""Edge-press detection helpers for pointer boundary routing."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EdgePress:
    """A detected pointer press against one display edge."""

    direction: str
    cross_axis_ratio: float


def detect_edge_press(display_rect, event: dict) -> EdgePress | None:
    """Return which edge is being pressed, or ``None`` when inside the rect."""
    if event.get("x") is None or event.get("y") is None:
        return None

    left, top, right, bottom = display_rect
    x = int(event["x"])
    y = int(event["y"])
    distances = []
    if x <= left:
        distances.append(("left", left - x, axis_ratio(y, top, bottom)))
    if x >= right:
        distances.append(("right", x - right, axis_ratio(y, top, bottom)))
    if y <= top:
        distances.append(("up", top - y, axis_ratio(x, left, right)))
    if y >= bottom:
        distances.append(("down", y - bottom, axis_ratio(x, left, right)))
    if not distances:
        return None

    direction, _distance, cross_axis_ratio = min(distances, key=lambda entry: entry[1])
    return EdgePress(direction=direction, cross_axis_ratio=cross_axis_ratio)


def axis_ratio(value: float, start: float, end: float) -> float:
    """Normalize a cross-axis coordinate into the 0..1 range."""
    span = max(float(end) - float(start), 1.0)
    return min(max((float(value) - float(start)) / span, 0.0), 1.0)
