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
from routing.remote_pointer import ActiveRemotePointer
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
        self._actual_pointer_provider = actual_pointer_provider
        self.screen_bounds_provider = screen_bounds_provider or get_virtual_screen_bounds
        self._now = now_fn or time.monotonic

        self._display_state = DisplayStateTracker(
            ctx,
            actual_pointer_provider=actual_pointer_provider,
        )
        self._routing = EdgeRoutingResolver()
        self._remote_pointer = ActiveRemotePointer(
            pointer_mover=pointer_mover,
            now_fn=self._now,
        )
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

    def on_router_state_change(self, state: str, node_id: str | None) -> None:
        if state != "active" or not node_id:
            self._remote_pointer.reset()
            return

        layout = self.ctx.layout
        if layout is None:
            self._remote_pointer.reset()
            return

        node = layout.get_node(node_id)
        if node is None:
            self._remote_pointer.reset()
            return

        display_id = self._display_state.state.get(node_id)
        if display_id is None:
            logical = node.monitors().logical
            display_id = None if not logical else logical[0].display_id
        source_display_id = None
        self_node = layout.get_node(self.ctx.self_node.node_id)
        if self_node is not None:
            source_display_id = self._display_state.sync_self_display_state(self_node)
        anchor_local = None
        if callable(self._actual_pointer_provider):
            try:
                anchor_local = self._actual_pointer_provider()
            except Exception as exc:  # pragma: no cover - defensive logging path
                logging.debug("[AUTO SWITCH DEBUG] failed to read local anchor pointer: %s", exc)
                anchor_local = None
        anchor_event = None
        if hasattr(self.router, "get_last_remote_anchor_event"):
            anchor_event = self.router.get_last_remote_anchor_event()
        self._remote_pointer.begin(
            node_id=node_id,
            display_id=display_id,
            source_node_id=self.ctx.self_node.node_id,
            source_display_id=source_display_id,
            anchor_local=anchor_local,
            initial_event=anchor_event,
        )

    def _process_mouse_move(self, event):
        if event.get("kind") != "mouse_move":
            return event
        raw_event = event

        now = self._now()
        self._executor.release_expired_edge_hold(now)

        if self._executor.should_drop_stale_move(event):
            return MoveProcessingResult(None, True)

        layout = self.ctx.layout
        if layout is None:
            return event

        active_target = None
        if hasattr(self.router, "get_active_target"):
            active_target = self.router.get_active_target()
        if active_target:
            return self._process_active_target_mouse_move(layout, active_target, event, now)

        self_node = layout.get_node(self.ctx.self_node.node_id)
        if self_node is not None:
            event = self._display_state.coerce_self_event(
                self_node,
                event,
                self.screen_bounds_provider(),
            )

        if self._executor.is_inside_anchor_guard(event, now):
            return event

        frame = self._build_frame(layout, event, now)
        if frame is None:
            return event

        if self._executor.maybe_release_edge_hold(raw_event, frame):
            return raw_event

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

    def _process_active_target_mouse_move(self, layout, active_target: str, event: dict, now: float):
        current_node = layout.get_node(active_target)
        if current_node is None:
            return MoveProcessingResult(None, True)

        remote_anchor_event = None
        if hasattr(self.router, "get_last_remote_anchor_event"):
            remote_anchor_event = self.router.get_last_remote_anchor_event()
        explicit_remote_anchor = remote_anchor_event is not None
        if not self._remote_pointer.is_active_for(active_target):
            self.on_router_state_change("active", active_target)

        bounds = self._display_state.node_screen_bounds(
            active_target,
            current_node,
            self.screen_bounds_provider(),
        )
        current_display_id = (
            self._remote_pointer.current_display_id()
            or self._display_state.state.get(active_target)
        )
        if current_display_id is None:
            logical = current_node.monitors().logical
            if not logical:
                return MoveProcessingResult(None, True)
            current_display_id = logical[0].display_id

        if explicit_remote_anchor and self._remote_pointer.current_event() is None:
            self._remote_pointer.sync_from_remote_event(
                node_id=active_target,
                display_id=current_display_id,
                event=remote_anchor_event,
            )

        source_node = layout.get_node(self.ctx.self_node.node_id)
        source_bounds = self.screen_bounds_provider()
        if source_node is not None:
            source_bounds = self._display_state.node_screen_bounds(
                self.ctx.self_node.node_id,
                source_node,
                source_bounds,
            )

        translated = self._remote_pointer.translate_local_move(
            node_id=active_target,
            display_id=current_display_id,
            node=current_node,
            bounds=bounds,
            source_node=source_node,
            source_bounds=source_bounds,
            local_event=event,
            display_state=self._display_state,
        )
        if translated is None and (
            self._remote_pointer.current_event() is None or not explicit_remote_anchor
        ):
            translated = dict(event)
            self._remote_pointer.sync_from_remote_event(
                node_id=active_target,
                display_id=current_display_id,
                event=translated,
            )
        if translated is None:
            return MoveProcessingResult(None, True)

        if self._executor.is_inside_anchor_guard(translated, now):
            self._remote_pointer.sync_from_remote_event(
                node_id=active_target,
                display_id=current_display_id,
                event=translated,
            )
            return MoveProcessingResult(translated, True)

        frame = self._build_frame(layout, translated, now)
        if frame is None:
            return MoveProcessingResult(translated, True)

        self._executor.maybe_release_edge_hold(translated, frame)

        edge_press = detect_edge_press(
            self._display_state.display_pixel_rect(frame.current_node, frame.current_display_id, frame.bounds),
            translated,
        )
        if edge_press is None:
            self._remote_pointer.sync_from_remote_event(
                node_id=active_target,
                display_id=frame.current_display_id,
                event=translated,
            )
            return MoveProcessingResult(translated, True)

        route = self._routing.resolve(
            layout=frame.layout,
            self_node_id=self.ctx.self_node.node_id,
            current_node_id=frame.current_node_id,
            current_display_id=frame.current_display_id,
            direction=edge_press.direction,
            cross_axis_ratio=edge_press.cross_axis_ratio,
            is_target_online=self._target_is_online,
            allow_remote_switch=bool(frame.layout.auto_switch.enabled),
        )
        self._log_route_debug_once(
            frame.now,
            (
                frame.current_node_id,
                frame.current_display_id,
                edge_press.direction,
                route.kind,
                None if route.destination is None else route.destination.node_id,
                None if route.destination is None else route.destination.display_id,
                route.reason,
            ),
            "[AUTO SWITCH DEBUG] route %s:%s %s -> %s",
            frame.current_node_id,
            frame.current_display_id,
            edge_press.direction,
            describe_edge_route(route),
        )
        transition = EdgeTransition(
            frame=frame,
            direction=edge_press.direction,
            cross_ratio=edge_press.cross_axis_ratio,
            event=translated,
        )
        result = self._executor.apply_route(transition, route)
        if isinstance(result, MoveProcessingResult):
            if result.event is not None and result.event.get("kind") == "mouse_move":
                next_display_id = self._display_state.state.get(frame.current_node_id, frame.current_display_id)
                self._remote_pointer.sync_from_remote_event(
                    node_id=frame.current_node_id,
                    display_id=next_display_id,
                    event=result.event,
                )
            return MoveProcessingResult(result.event, True)
        return MoveProcessingResult(result, True)

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

        bounds = self._display_state.node_screen_bounds(
            current_node_id,
            current_node,
            self.screen_bounds_provider(),
        )
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
