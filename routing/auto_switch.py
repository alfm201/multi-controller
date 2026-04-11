"""Boundary-based automatic target switching logic."""

import logging
import math
import time

from runtime.display import denormalize_position, get_virtual_screen_bounds, normalize_position
from runtime.layouts import (
    build_anchor_event,
    find_adjacent_display,
    find_adjacent_display_in_node,
    normalized_display_rect,
    resolve_display_for_normalized_point,
)


def detect_edge_direction(event, threshold: float):
    """Legacy whole-screen edge detection helper kept for tests and fallback behavior."""
    try:
        x_norm = float(event["x_norm"])
        y_norm = float(event["y_norm"])
    except (KeyError, TypeError, ValueError):
        return None, None

    distances = [
        ("left", x_norm, y_norm),
        ("right", 1.0 - x_norm, y_norm),
        ("up", y_norm, x_norm),
        ("down", 1.0 - y_norm, x_norm),
    ]
    direction, distance, cross_ratio = min(distances, key=lambda entry: entry[1])
    if distance > float(threshold):
        return None, None
    return direction, min(max(cross_ratio, 0.0), 1.0)


class AutoTargetSwitcher:
    """Watch mouse boundary movement and select the next target automatically."""

    def __init__(
        self,
        ctx,
        router,
        request_target,
        clear_target,
        is_target_online=None,
        pointer_mover=None,
        screen_bounds_provider=None,
        now_fn=None,
    ):
        self.ctx = ctx
        self.router = router
        self.request_target = request_target
        self.clear_target = clear_target
        self.is_target_online = is_target_online
        self.pointer_mover = pointer_mover
        self.screen_bounds_provider = screen_bounds_provider or get_virtual_screen_bounds
        self._now = now_fn or time.monotonic
        self._last_switch_at = 0.0
        self._guard_until = 0.0
        self._anchor_norm = None
        self._anchor_pixel = None

    def process(self, event):
        """Look at mouse_move events and switch target when needed."""
        try:
            return self._process_mouse_move(event)
        except Exception as exc:
            logging.warning("[AUTO SWITCH] failed to process event: %s", exc)
            return event

    def _process_mouse_move(self, event):
        if event.get("kind") != "mouse_move":
            return event
        layout = self.ctx.layout
        if layout is None or not layout.auto_switch.enabled:
            return event

        now = self._now()
        bounds = self.screen_bounds_provider()
        if self._inside_anchor_guard(event, now):
            return event

        cooldown_sec = max(layout.auto_switch.cooldown_ms, 0) / 1000.0
        if now - self._last_switch_at < cooldown_sec:
            return event

        current_node_id = self.router.get_selected_target() or self.ctx.self_node.node_id
        current_node = layout.get_node(current_node_id)
        if current_node is None:
            return event

        current_display, direction, cross_ratio = self._detect_display_edge(
            current_node,
            event,
            bounds,
        )
        if current_display is None or direction is None:
            return event

        next_display = find_adjacent_display(
            layout,
            current_node_id=current_node_id,
            current_display_id=current_display.display_id,
            direction=direction,
            cross_axis_ratio=cross_ratio,
        )
        if next_display is None:
            if current_node_id == self.ctx.self_node.node_id:
                logical_neighbor = find_adjacent_display_in_node(
                    current_node,
                    current_display.display_id,
                    direction,
                    cross_ratio,
                    logical=True,
                )
                if logical_neighbor is not None:
                    anchor_event = self._build_wall_anchor_event(
                        current_node,
                        current_display.display_id,
                        direction,
                        cross_ratio,
                        bounds,
                    )
                    self._record_switch(
                        anchor_event,
                        now,
                        layout.auto_switch.return_guard_ms,
                        bounds,
                    )
                    self._warp_pointer(anchor_event)
                    logging.info(
                        "[AUTO SWITCH] self dead edge blocked on %s via %s edge",
                        current_display.display_id,
                        direction,
                    )
                    return None
            return event

        if (
            next_display.node_id == current_node_id
            and current_node_id == self.ctx.self_node.node_id
            and next_display.display_id != current_display.display_id
        ):
            anchor_event = build_anchor_event(
                current_node,
                next_display.display_id,
                direction,
                cross_ratio,
            )
            self._record_switch(anchor_event, now, layout.auto_switch.return_guard_ms, bounds)
            self._warp_pointer(anchor_event)
            logging.info(
                "[AUTO SWITCH] self internal display %s -> %s via %s edge",
                current_display.display_id,
                next_display.display_id,
                direction,
            )
            return None

        if next_display.node_id == current_node_id:
            logging.debug(
                "[AUTO SWITCH] internal display edge %s:%s -> %s",
                current_node_id,
                current_display.display_id,
                next_display.display_id,
            )
            return event

        next_node = layout.get_node(next_display.node_id)
        if next_node is None:
            return event

        if next_node.node_id != self.ctx.self_node.node_id and not self._target_is_online(next_node.node_id):
            logging.debug(
                "[AUTO SWITCH] skip offline adjacent target=%s from %s:%s via %s edge",
                next_node.node_id,
                current_node_id,
                current_display.display_id,
                direction,
            )
            return event

        anchor_event = build_anchor_event(
            next_node,
            next_display.display_id,
            direction,
            cross_ratio,
        )

        if hasattr(self.router, "prepare_pointer_handoff"):
            self.router.prepare_pointer_handoff(anchor_event)

        if next_node.node_id == self.ctx.self_node.node_id:
            if self.router.get_selected_target() is None:
                return event
            self.clear_target()
            self._record_switch(anchor_event, now, layout.auto_switch.return_guard_ms, bounds)
            self._warp_pointer(anchor_event)
            logging.info(
                "[AUTO SWITCH] %s:%s -> self:%s via %s edge",
                current_node_id,
                current_display.display_id,
                next_display.display_id,
                direction,
            )
            return None

        target = self.ctx.get_node(next_node.node_id)
        if target is None or not target.has_role("target"):
            return event

        self.request_target(next_node.node_id)
        self._record_switch(anchor_event, now, layout.auto_switch.return_guard_ms, bounds)
        self._warp_pointer(anchor_event)
        logging.info(
            "[AUTO SWITCH] %s:%s -> %s:%s via %s edge",
            current_node_id,
            current_display.display_id,
            next_node.node_id,
            next_display.display_id,
            direction,
        )
        return None

    def _target_is_online(self, node_id: str) -> bool:
        if node_id == self.ctx.self_node.node_id:
            return True
        if callable(self.is_target_online):
            try:
                return bool(self.is_target_online(node_id))
            except Exception as exc:
                logging.warning("[AUTO SWITCH] online check failed for %s: %s", node_id, exc)
                return False
        return True

    def _record_switch(self, anchor_event, now: float, return_guard_ms: int, bounds):
        self._last_switch_at = now
        self._guard_until = now + max(int(return_guard_ms), 0) / 1000.0
        self._anchor_norm = (
            anchor_event.get("x_norm"),
            anchor_event.get("y_norm"),
        )
        if "x" in anchor_event and "y" in anchor_event:
            self._anchor_pixel = (int(anchor_event["x"]), int(anchor_event["y"]))
            return
        if hasattr(bounds, "left") and hasattr(bounds, "top") and hasattr(bounds, "width") and hasattr(bounds, "height"):
            bounds_arg = (bounds.left, bounds.top, bounds.width, bounds.height)
        else:
            bounds_arg = bounds
        self._anchor_pixel = denormalize_position(anchor_event["x_norm"], anchor_event["y_norm"], bounds_arg)

    def _inside_anchor_guard(self, event, now: float) -> bool:
        if self._anchor_norm is None or now >= self._guard_until:
            return False
        if self._anchor_pixel is not None and event.get("x") is not None and event.get("y") is not None:
            return (
                abs(int(event["x"]) - self._anchor_pixel[0]) <= 1
                and abs(int(event["y"]) - self._anchor_pixel[1]) <= 1
            )
        try:
            x_norm = float(event["x_norm"])
            y_norm = float(event["y_norm"])
        except (KeyError, TypeError, ValueError):
            return False
        return abs(x_norm - self._anchor_norm[0]) <= 1e-9 and abs(y_norm - self._anchor_norm[1]) <= 1e-9

    def _detect_display_edge(self, node, event, bounds):
        display = resolve_display_for_normalized_point(node, event.get("x_norm"), event.get("y_norm"))
        if display is None:
            return None, None, None
        if event.get("x") is None or event.get("y") is None:
            return display, None, None

        left, top, right, bottom = self._display_pixel_rect(node, display.display_id, bounds)
        x = int(event["x"])
        y = int(event["y"])
        distances = []
        if x <= left:
            distances.append(("left", left - x, _axis_ratio(y, top, bottom)))
        if x >= right:
            distances.append(("right", x - right, _axis_ratio(y, top, bottom)))
        if y <= top:
            distances.append(("up", top - y, _axis_ratio(x, left, right)))
        if y >= bottom:
            distances.append(("down", y - bottom, _axis_ratio(x, left, right)))
        if not distances:
            return display, None, None
        direction, _distance, cross_ratio = min(distances, key=lambda entry: entry[1])
        return display, direction, cross_ratio

    def _display_pixel_rect(self, node, display_id: str, bounds):
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

    def _build_wall_anchor_event(self, node, display_id: str, direction: str, cross_axis_ratio: float, bounds):
        left, top, right, bottom = self._display_pixel_rect(node, display_id, bounds)
        if direction == "left":
            x = left
            y = top + round(_axis_ratio(cross_axis_ratio, 0.0, 1.0) * max(bottom - top, 0))
        elif direction == "right":
            x = right
            y = top + round(_axis_ratio(cross_axis_ratio, 0.0, 1.0) * max(bottom - top, 0))
        elif direction == "up":
            x = left + round(_axis_ratio(cross_axis_ratio, 0.0, 1.0) * max(right - left, 0))
            y = top
        elif direction == "down":
            x = left + round(_axis_ratio(cross_axis_ratio, 0.0, 1.0) * max(right - left, 0))
            y = bottom
        else:
            raise ValueError(f"unknown direction: {direction}")
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

    def _warp_pointer(self, anchor_event: dict):
        if self.pointer_mover is None:
            return
        bounds = self.screen_bounds_provider()
        if hasattr(bounds, "left") and hasattr(bounds, "top") and hasattr(bounds, "width") and hasattr(bounds, "height"):
            bounds_arg = (bounds.left, bounds.top, bounds.width, bounds.height)
        else:
            bounds_arg = bounds
        x, y = denormalize_position(anchor_event["x_norm"], anchor_event["y_norm"], bounds_arg)
        self.pointer_mover(x, y)


def _axis_ratio(value: float, start: float, end: float) -> float:
    span = max(float(end) - float(start), 1.0)
    return min(max((float(value) - float(start)) / span, 0.0), 1.0)
