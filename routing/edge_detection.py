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


def detect_edge_crossing(display_rect, previous_event: dict | None, event: dict) -> EdgePress | None:
    """Return the first crossed edge when a segment leaves the rect between samples."""
    if previous_event is None:
        return None
    if previous_event.get("x") is None or previous_event.get("y") is None:
        return None
    if event.get("x") is None or event.get("y") is None:
        return None

    left, top, right, bottom = display_rect
    previous_x = int(previous_event["x"])
    previous_y = int(previous_event["y"])
    current_x = int(event["x"])
    current_y = int(event["y"])
    delta_x = current_x - previous_x
    delta_y = current_y - previous_y

    candidates: list[tuple[float, str, float]] = []

    if delta_x < 0 and previous_x >= left and current_x < left:
        t = (left - previous_x) / float(delta_x)
        cross_y = previous_y + (delta_y * t)
        if 0.0 <= t <= 1.0 and top <= cross_y <= bottom:
            candidates.append((t, "left", axis_ratio(cross_y, top, bottom)))

    if delta_x > 0 and previous_x <= right and current_x > right:
        t = (right - previous_x) / float(delta_x)
        cross_y = previous_y + (delta_y * t)
        if 0.0 <= t <= 1.0 and top <= cross_y <= bottom:
            candidates.append((t, "right", axis_ratio(cross_y, top, bottom)))

    if delta_y < 0 and previous_y >= top and current_y < top:
        t = (top - previous_y) / float(delta_y)
        cross_x = previous_x + (delta_x * t)
        if 0.0 <= t <= 1.0 and left <= cross_x <= right:
            candidates.append((t, "up", axis_ratio(cross_x, left, right)))

    if delta_y > 0 and previous_y <= bottom and current_y > bottom:
        t = (bottom - previous_y) / float(delta_y)
        cross_x = previous_x + (delta_x * t)
        if 0.0 <= t <= 1.0 and left <= cross_x <= right:
            candidates.append((t, "down", axis_ratio(cross_x, left, right)))

    if not candidates:
        return None

    _t, direction, cross_axis_ratio = min(candidates, key=lambda entry: entry[0])
    return EdgePress(direction=direction, cross_axis_ratio=cross_axis_ratio)


def detect_edge_approach(display_rect, previous_event: dict | None, event: dict, band_px: int) -> EdgePress | None:
    """Return an approaching edge press while the pointer is still inside the rect."""
    if band_px <= 0:
        return None
    if previous_event is None:
        return None
    if previous_event.get("x") is None or previous_event.get("y") is None:
        return None
    if event.get("x") is None or event.get("y") is None:
        return None

    left, top, right, bottom = display_rect
    previous_x = int(previous_event["x"])
    previous_y = int(previous_event["y"])
    current_x = int(event["x"])
    current_y = int(event["y"])

    if not (left <= previous_x <= right and top <= previous_y <= bottom):
        return None
    if not (left <= current_x <= right and top <= current_y <= bottom):
        return None

    delta_x = current_x - previous_x
    delta_y = current_y - previous_y
    band = int(band_px)
    candidates: list[tuple[int, str, float]] = []

    right_distance = right - current_x
    previous_right_distance = right - previous_x
    if delta_x > 0 and 0 < right_distance <= band and previous_right_distance > right_distance:
        candidates.append((right_distance, "right", axis_ratio(current_y, top, bottom)))

    left_distance = current_x - left
    previous_left_distance = previous_x - left
    if delta_x < 0 and 0 < left_distance <= band and previous_left_distance > left_distance:
        candidates.append((left_distance, "left", axis_ratio(current_y, top, bottom)))

    bottom_distance = bottom - current_y
    previous_bottom_distance = bottom - previous_y
    if delta_y > 0 and 0 < bottom_distance <= band and previous_bottom_distance > bottom_distance:
        candidates.append((bottom_distance, "down", axis_ratio(current_x, left, right)))

    top_distance = current_y - top
    previous_top_distance = previous_y - top
    if delta_y < 0 and 0 < top_distance <= band and previous_top_distance > top_distance:
        candidates.append((top_distance, "up", axis_ratio(current_x, left, right)))

    if not candidates:
        return None

    _distance, direction, cross_axis_ratio = min(candidates, key=lambda entry: entry[0])
    return EdgePress(direction=direction, cross_axis_ratio=cross_axis_ratio)


def axis_ratio(value: float, start: float, end: float) -> float:
    """Normalize a cross-axis coordinate into the 0..1 range."""
    span = max(float(end) - float(start), 1.0)
    return min(max((float(value) - float(start)) / span, 0.0), 1.0)
