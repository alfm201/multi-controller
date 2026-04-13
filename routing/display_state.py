"""Display-state tracking helpers for edge-based routing."""

from __future__ import annotations

import math

from runtime.display import ScreenBounds, normalize_position
from runtime.layouts import normalized_display_rect, resolve_display_for_normalized_point


class DisplayStateTracker:
    """Track which display is currently active for self and remote targets."""

    def __init__(self, ctx, actual_pointer_provider=None):
        self.ctx = ctx
        self.actual_pointer_provider = actual_pointer_provider
        self._display_state_by_node: dict[str, str] = {}

    @property
    def state(self) -> dict[str, str]:
        return self._display_state_by_node

    def remember(self, node_id: str, display_id: str) -> None:
        self._display_state_by_node[node_id] = display_id

    def current_display_id(self, current_node_id: str, node, event: dict) -> str | None:
        cached = self._display_state_by_node.get(current_node_id)
        if self.display_by_id(node, cached) is not None:
            return cached

        if current_node_id == self.ctx.self_node.node_id:
            resolved = self.sync_self_display_state(node)
            if resolved is not None:
                return resolved

        display = resolve_display_for_normalized_point(node, event.get("x_norm"), event.get("y_norm"))
        if display is None:
            logical_displays = node.monitors().logical
            if logical_displays:
                display = logical_displays[0]
        if display is None:
            return None
        self.remember(current_node_id, display.display_id)
        return display.display_id

    def sync_self_display_state(self, node) -> str | None:
        current_pos = self.actual_pointer_position(node)
        if current_pos is None:
            return self._display_state_by_node.get(self.ctx.self_node.node_id)
        display = self.resolve_actual_self_display(node, int(current_pos[0]), int(current_pos[1]))
        if display is None:
            return self._display_state_by_node.get(self.ctx.self_node.node_id)
        self.remember(self.ctx.self_node.node_id, display.display_id)
        return display.display_id

    def actual_pointer_position(self, node) -> tuple[int, int] | None:
        if node.node_id != self.ctx.self_node.node_id or not callable(self.actual_pointer_provider):
            return None
        return self.actual_pointer_provider()

    def display_pixel_rect(self, node, display_id: str, bounds):
        actual_rect = self.actual_self_display_rect(node, display_id)
        if actual_rect is not None:
            return actual_rect

        left, top, right, bottom = normalized_display_rect(node, display_id, logical=True)
        if hasattr(bounds, "left") and hasattr(bounds, "top") and hasattr(bounds, "width") and hasattr(bounds, "height"):
            offset_left = int(bounds.left)
            offset_top = int(bounds.top)
            width = max(int(bounds.width), 1)
            height = max(int(bounds.height), 1)
        else:
            offset_left = int(bounds[0])
            offset_top = int(bounds[1])
            width = max(int(bounds[2]), 1)
            height = max(int(bounds[3]), 1)
        left_px = min(max(int(math.floor(left * width)), 0), width - 1)
        top_px = min(max(int(math.floor(top * height)), 0), height - 1)
        right_px = min(max(int(math.ceil(right * width)) - 1, left_px), width - 1)
        bottom_px = min(max(int(math.ceil(bottom * height)) - 1, top_px), height - 1)
        return (
            offset_left + left_px,
            offset_top + top_px,
            offset_left + right_px,
            offset_top + bottom_px,
        )

    def build_edge_anchor_event(
        self,
        node,
        display_id: str,
        direction: str,
        cross_axis_ratio: float,
        bounds,
        source_event: dict | None = None,
        *,
        blocked: bool = False,
    ) -> dict:
        left, top, right, bottom = self.display_pixel_rect(node, display_id, bounds)
        blocked_left, blocked_top, blocked_right, blocked_bottom = self._blocked_edge_rect(
            left,
            top,
            right,
            bottom,
        )
        ratio = min(max(float(cross_axis_ratio), 0.0), 1.0)
        if direction == "left":
            x = blocked_left if blocked else right
            y = top + round(ratio * max(bottom - top, 0))
        elif direction == "right":
            x = blocked_right if blocked else left
            y = top + round(ratio * max(bottom - top, 0))
        elif direction == "up":
            x = left + round(ratio * max(right - left, 0))
            y = blocked_top if blocked else bottom
        elif direction == "down":
            x = left + round(ratio * max(right - left, 0))
            y = blocked_bottom if blocked else top
        else:
            raise ValueError(f"unknown direction: {direction}")

        if blocked and source_event is not None:
            if direction in {"left", "right"} and source_event.get("y") is not None:
                y = min(max(int(source_event["y"]), top), bottom)
            if direction in {"up", "down"} and source_event.get("x") is not None:
                x = min(max(int(source_event["x"]), left), right)

        bounds_arg = (
            bounds.left,
            bounds.top,
            bounds.width,
            bounds.height,
        ) if hasattr(bounds, "left") else bounds
        x_norm, y_norm = normalize_position(x, y, bounds_arg)
        return {
            "kind": "mouse_move",
            "x": x,
            "y": y,
            "x_norm": x_norm,
            "y_norm": y_norm,
        }

    def build_edge_hold_rect(self, node, display_id: str, direction: str, bounds):
        left, top, right, bottom = self.display_pixel_rect(node, display_id, bounds)
        blocked_left, blocked_top, blocked_right, blocked_bottom = self._blocked_edge_rect(
            left,
            top,
            right,
            bottom,
        )
        if direction == "left":
            return (blocked_left, top, blocked_left, bottom)
        if direction == "right":
            return (blocked_right, top, blocked_right, bottom)
        if direction == "up":
            return (left, blocked_top, right, blocked_top)
        if direction == "down":
            return (left, blocked_bottom, right, blocked_bottom)
        raise ValueError(f"unknown direction: {direction}")

    @staticmethod
    def _blocked_edge_rect(left: int, top: int, right: int, bottom: int) -> tuple[int, int, int, int]:
        inward_right = right - 1 if right > left else right
        inward_bottom = bottom - 1 if bottom > top else bottom
        return left, top, inward_right, inward_bottom

    def build_display_center_event(self, node, display_id: str, bounds) -> dict:
        left, top, right, bottom = self.display_pixel_rect(node, display_id, bounds)
        bounds_arg = self._normalize_bounds_arg(bounds)
        x = left + round(max(right - left, 0) / 2)
        y = top + round(max(bottom - top, 0) / 2)
        x_norm, y_norm = normalize_position(x, y, bounds_arg)
        return {
            "kind": "mouse_move",
            "x": x,
            "y": y,
            "x_norm": x_norm,
            "y_norm": y_norm,
        }

    @staticmethod
    def _normalize_bounds_arg(bounds):
        if isinstance(bounds, ScreenBounds):
            return bounds
        if hasattr(bounds, "left") and hasattr(bounds, "top") and hasattr(bounds, "width") and hasattr(bounds, "height"):
            return (int(bounds.left), int(bounds.top), int(bounds.width), int(bounds.height))
        return bounds

    def actual_self_display_rect(self, node, display_id: str):
        if node.node_id != self.ctx.self_node.node_id:
            return None
        snapshot = self.ctx.get_monitor_inventory(node.node_id)
        if snapshot is None:
            return None
        for item in snapshot.monitors:
            if item.monitor_id != display_id:
                continue
            left = int(item.bounds.left)
            top = int(item.bounds.top)
            right = left + max(int(item.bounds.width), 1) - 1
            bottom = top + max(int(item.bounds.height), 1) - 1
            return (left, top, right, bottom)
        return None

    def resolve_actual_self_display(self, node, x: int, y: int):
        if node.node_id != self.ctx.self_node.node_id:
            return None
        snapshot = self.ctx.get_monitor_inventory(node.node_id)
        if snapshot is None or not snapshot.monitors:
            return None

        containing = []
        for item in snapshot.monitors:
            left = int(item.bounds.left)
            top = int(item.bounds.top)
            right = left + max(int(item.bounds.width), 1) - 1
            bottom = top + max(int(item.bounds.height), 1) - 1
            if left <= x <= right and top <= y <= bottom:
                containing.append((item, left, top, right, bottom))

        if containing:
            chosen = min(
                containing,
                key=lambda entry: abs(((entry[1] + entry[3]) / 2) - x)
                + abs(((entry[2] + entry[4]) / 2) - y),
            )[0]
        else:
            chosen = min(
                snapshot.monitors,
                key=lambda item: _distance_to_rect(
                    x,
                    y,
                    int(item.bounds.left),
                    int(item.bounds.top),
                    int(item.bounds.left) + max(int(item.bounds.width), 1) - 1,
                    int(item.bounds.top) + max(int(item.bounds.height), 1) - 1,
                ),
            )
        return node.monitors().get_logical_display(chosen.monitor_id) or node.monitors().get_physical_display(
            chosen.monitor_id
        )

    @staticmethod
    def display_by_id(node, display_id: str | None):
        if not display_id:
            return None
        return node.monitors().get_logical_display(display_id) or node.monitors().get_physical_display(display_id)


def _distance_to_rect(x: int, y: int, left: int, top: int, right: int, bottom: int) -> float:
    dx = 0 if left <= x <= right else min(abs(x - left), abs(x - right))
    dy = 0 if top <= y <= bottom else min(abs(y - top), abs(y - bottom))
    return math.hypot(dx, dy)
