"""Boundary-based automatic target switching logic."""

import logging
import time

from runtime.display import denormalize_position, get_virtual_screen_bounds
from runtime.layouts import (
    build_anchor_event,
    detect_display_edge,
    find_adjacent_display,
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
        pointer_mover=None,
        screen_bounds_provider=None,
        now_fn=None,
    ):
        self.ctx = ctx
        self.router = router
        self.request_target = request_target
        self.clear_target = clear_target
        self.pointer_mover = pointer_mover
        self.screen_bounds_provider = screen_bounds_provider or get_virtual_screen_bounds
        self._now = now_fn or time.monotonic
        self._last_switch_at = 0.0
        self._guard_until = 0.0
        self._anchor_norm = None

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
        if self._inside_anchor_dead_zone(event, layout.auto_switch.anchor_dead_zone, now):
            return event

        cooldown_sec = max(layout.auto_switch.cooldown_ms, 0) / 1000.0
        if now - self._last_switch_at < cooldown_sec:
            return event

        current_node_id = self.router.get_selected_target() or self.ctx.self_node.node_id
        current_node = layout.get_node(current_node_id)
        if current_node is None:
            return event

        current_display, direction, cross_ratio = detect_display_edge(
            current_node,
            x_norm=event.get("x_norm"),
            y_norm=event.get("y_norm"),
            threshold=layout.auto_switch.edge_threshold,
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
            return event

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

        anchor_event = build_anchor_event(
            next_node,
            next_display.display_id,
            direction,
            cross_ratio,
            layout.auto_switch.warp_margin,
        )

        if hasattr(self.router, "prepare_pointer_handoff"):
            self.router.prepare_pointer_handoff(anchor_event)

        if next_node.node_id == self.ctx.self_node.node_id:
            if self.router.get_selected_target() is None:
                return event
            self.clear_target()
            self._record_switch(anchor_event, now, layout.auto_switch.return_guard_ms)
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
        self._record_switch(anchor_event, now, layout.auto_switch.return_guard_ms)
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

    def _record_switch(self, anchor_event, now: float, return_guard_ms: int):
        self._last_switch_at = now
        self._guard_until = now + max(int(return_guard_ms), 0) / 1000.0
        self._anchor_norm = (
            anchor_event.get("x_norm"),
            anchor_event.get("y_norm"),
        )

    def _inside_anchor_dead_zone(self, event, dead_zone: float, now: float) -> bool:
        if self._anchor_norm is None or now >= self._guard_until:
            return False
        try:
            x_norm = float(event["x_norm"])
            y_norm = float(event["y_norm"])
        except (KeyError, TypeError, ValueError):
            return False
        radius = max(float(dead_zone), 0.0)
        return (
            abs(x_norm - self._anchor_norm[0]) <= radius
            and abs(y_norm - self._anchor_norm[1]) <= radius
        )

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
