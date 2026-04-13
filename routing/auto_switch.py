"""Boundary-based automatic target switching logic."""

from __future__ import annotations

import logging
import time

from capture.input_capture import MoveProcessingResult
from routing.edge_detection import detect_edge_press
from routing.edge_runtime import AutoSwitchFrame, EdgeTransition
from routing.display_state import DisplayStateTracker
from routing.edge_actions import EdgeActionExecutor
from routing.edge_routing import EdgeRoutingResolver, describe_edge_route
from runtime.display import get_virtual_screen_bounds


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
    """Watch mouse boundary movement and switch between self and remote targets."""

    REPOSITION_STALE_MOVE_WINDOW_SEC = 0.05
    ROUTE_DEBUG_DEDUP_WINDOW_SEC = 0.25

    def __init__(
        self,
        ctx,
        router,
        request_target,
        clear_target,
        is_target_online=None,
        pointer_mover=None,
        pointer_clipper=None,
        actual_pointer_provider=None,
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

        self._display_state = DisplayStateTracker(
            ctx,
            actual_pointer_provider=actual_pointer_provider,
        )
        self._routing = EdgeRoutingResolver()
        self._executor = EdgeActionExecutor(
            ctx=ctx,
            router=router,
            request_target=request_target,
            clear_target=clear_target,
            pointer_mover=pointer_mover,
            pointer_clipper=pointer_clipper,
            display_state=self._display_state,
        )
        self._last_route_debug_key = None
        self._last_route_debug_at = 0.0

    def process(self, event):
        """Inspect mouse moves and convert boundary hits into switching actions."""
        try:
            return self._process_mouse_move(event)
        except Exception as exc:  # pragma: no cover - defensive logging path
            logging.warning("[AUTO SWITCH] failed to process event: %s", exc)
            return event

    def refresh_self_clip(self) -> None:
        """Legacy hook name kept for callers; now it only syncs self display state."""
        layout = self.ctx.layout
        if layout is None:
            return
        if hasattr(self.router, "get_active_target") and self.router.get_active_target() is not None:
            return
        node = layout.get_node(self.ctx.self_node.node_id)
        if node is None:
            return
        self._display_state.sync_self_display_state(node)

    def _process_mouse_move(self, event):
        if event.get("kind") != "mouse_move":
            return event

        now = self._now()
        self._executor.release_expired_edge_hold(now)

        if self._executor.should_drop_stale_move(event):
            return MoveProcessingResult(None, True)

        layout = self.ctx.layout
        if layout is None:
            return event

        if self._executor.is_inside_anchor_guard(event, now):
            return event

        frame = self._build_frame(layout, event, now)
        if frame is None:
            return event

        self._executor.maybe_release_edge_hold(event, frame)

        edge_press = detect_edge_press(
            self._display_state.display_pixel_rect(frame.current_node, frame.current_display_id, frame.bounds),
            event,
        )
        if edge_press is None:
            return event
        direction = edge_press.direction
        cross_ratio = edge_press.cross_axis_ratio

        route = self._routing.resolve(
            layout=frame.layout,
            self_node_id=self.ctx.self_node.node_id,
            current_node_id=frame.current_node_id,
            current_display_id=frame.current_display_id,
            direction=direction,
            cross_axis_ratio=cross_ratio,
            is_target_online=self._target_is_online,
            allow_remote_switch=bool(frame.layout.auto_switch.enabled),
        )
        self._log_route_debug_once(
            frame.now,
            (
                frame.current_node_id,
                frame.current_display_id,
                direction,
                route.kind,
                None if route.destination is None else route.destination.node_id,
                None if route.destination is None else route.destination.display_id,
                route.reason,
            ),
            "[AUTO SWITCH DEBUG] route %s:%s %s -> %s",
            frame.current_node_id,
            frame.current_display_id,
            direction,
            describe_edge_route(route),
        )
        transition = EdgeTransition(
            frame=frame,
            direction=direction,
            cross_ratio=cross_ratio,
            event=event,
        )
        return self._executor.apply_route(transition, route)

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

    @property
    def _display_state_by_node(self):
        return self._display_state.state

    def _build_frame(self, layout, event: dict, now: float) -> AutoSwitchFrame | None:
        active_target = None
        if hasattr(self.router, "get_active_target"):
            active_target = self.router.get_active_target()
        current_node_id = active_target or self.ctx.self_node.node_id
        current_node = layout.get_node(current_node_id)
        if current_node is None:
            logging.debug("[AUTO SWITCH DEBUG] frame missing node=%s", current_node_id)
            return None

        bounds = self.screen_bounds_provider()
        current_display_id = self._display_state.current_display_id(current_node_id, current_node, event)
        if current_display_id is None:
            logging.debug(
                "[AUTO SWITCH DEBUG] frame missing display node=%s raw=(%s,%s) norm=(%s,%s)",
                current_node_id,
                event.get("x"),
                event.get("y"),
                event.get("x_norm"),
                event.get("y_norm"),
            )
            return None

        return AutoSwitchFrame(
            layout=layout,
            current_node_id=current_node_id,
            current_node=current_node,
            current_display_id=current_display_id,
            bounds=bounds,
            now=now,
        )

    def _log_route_debug_once(self, now: float, key, message: str, *args) -> None:
        if key == self._last_route_debug_key and (now - self._last_route_debug_at) < self.ROUTE_DEBUG_DEDUP_WINDOW_SEC:
            return
        self._last_route_debug_key = key
        self._last_route_debug_at = now
        logging.debug(message, *args)
