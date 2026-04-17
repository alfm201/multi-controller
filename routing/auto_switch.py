"""Boundary-based automatic target switching logic."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import time

from capture.input_capture import MoveProcessingResult
from routing.edge_detection import EdgePress, detect_edge_approach, detect_edge_crossing, detect_edge_press
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


@dataclass(frozen=True)
class _RouteSample:
    node_id: str
    display_id: str
    event: dict


class AutoTargetSwitcher:
    """Watch mouse boundary movement and switch between self and remote targets."""

    ROUTE_DEBUG_DEDUP_WINDOW_SEC = 0.25
    SELF_PREBLOCK_BAND_PX = 2

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
        self._last_route_sample_by_node: dict[str, _RouteSample] = {}

    def process(self, event):
        """Inspect mouse moves and convert boundary hits into switching actions."""
        try:
            return self._process_mouse_move(event)
        except Exception as exc:  # pragma: no cover - defensive logging path
            logging.warning("[AUTO SWITCH] failed to process event: %s", exc)
            return event

    def sync_self_pointer_state(self) -> None:
        """Sync cached self display state from the actual local pointer."""
        layout = self.ctx.layout
        if layout is None:
            return
        if hasattr(self.router, "get_active_target") and self.router.get_active_target() is not None:
            return
        hold = self._executor.edge_hold_context(current_node_id=self.ctx.self_node.node_id)
        if hold is not None and hold.uses_local_clip:
            return
        node = layout.get_node(self.ctx.self_node.node_id)
        if node is None:
            return
        self._display_state.sync_self_display_state(node)

    def note_local_hold_risk(self) -> None:
        """Mark the active local hold as externally disturbed without releasing it."""
        layout = self.ctx.layout
        if layout is None:
            return
        if hasattr(self.router, "get_active_target") and self.router.get_active_target() is not None:
            return
        self._executor.mark_local_hold_risk(reason="focus-risk")

    def refresh_self_clip(self) -> None:
        """Legacy wrapper kept for startup/tests; only syncs self pointer state."""
        self.sync_self_pointer_state()

    def on_router_state_change(self, state: str, node_id: str | None) -> None:
        self._clear_route_sample()
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
        routed_event = event
        resolved_event = event

        now = self._now()
        active_target = None
        if hasattr(self.router, "get_active_target"):
            active_target = self.router.get_active_target()
        self._executor.sync_edge_hold(
            now,
            current_node_id=active_target or self.ctx.self_node.node_id,
        )

        if self._executor.should_drop_stale_move(event):
            return MoveProcessingResult(None, True)

        layout = self.ctx.layout
        if layout is None:
            return event

        if active_target:
            return self._process_active_target_mouse_move(layout, active_target, event, now)

        self_node = layout.get_node(self.ctx.self_node.node_id)
        if self_node is not None:
            observed_event = self._display_state.observe_self_event(
                self_node,
                event,
            )
            resolved_event = self._display_state.coerce_self_event(
                self_node,
                observed_event,
                self.screen_bounds_provider(),
            )
            previous_sample = self._last_route_sample_by_node.get(self.ctx.self_node.node_id)
            hold = self._executor.edge_hold_context(current_node_id=self.ctx.self_node.node_id)
            routed_event = observed_event if (hold is not None or previous_sample is not None) else resolved_event

        if self._executor.is_inside_anchor_guard(resolved_event, now):
            return resolved_event

        frame = self._build_frame(layout, routed_event, now)
        if frame is None:
            return resolved_event

        hold_result = self._executor.continue_edge_hold(
            resolved_event,
            frame,
            source_event=raw_event,
        )
        if hold_result is not None:
            if self._executor.edge_hold_context(current_node_id=frame.current_node_id) is None:
                self._clear_route_sample(frame.current_node_id)
            else:
                self._remember_resulting_sample(frame.current_node_id, frame.current_display_id, hold_result)
            return hold_result

        preblock_frame, preblock_press = self._resolve_self_preblock_contact(frame, routed_event)
        if preblock_press is not None:
            route = self._routing.resolve(
                layout=preblock_frame.layout,
                self_node_id=self.ctx.self_node.node_id,
                current_node_id=preblock_frame.current_node_id,
                current_display_id=preblock_frame.current_display_id,
                direction=preblock_press.direction,
                cross_axis_ratio=preblock_press.cross_axis_ratio,
                is_target_online=self._target_is_online,
                allow_remote_switch=bool(preblock_frame.layout.auto_switch.enabled),
            )
            if route.kind == "block" and preblock_frame.current_node_id == self.ctx.self_node.node_id:
                self._log_route_debug_once(
                    preblock_frame.now,
                    (
                        "preblock",
                        preblock_frame.current_node_id,
                        preblock_frame.current_display_id,
                        preblock_press.direction,
                        route.kind,
                        route.reason,
                    ),
                    "[AUTO SWITCH DEBUG] preblock %s:%s %s -> %s",
                    preblock_frame.current_node_id,
                    preblock_frame.current_display_id,
                    preblock_press.direction,
                    describe_edge_route(route),
                )
                transition = EdgeTransition(
                    frame=preblock_frame,
                    direction=preblock_press.direction,
                    cross_ratio=preblock_press.cross_axis_ratio,
                    event=routed_event,
                )
                result = self._executor.apply_route(transition, route)
                self._remember_resulting_sample(preblock_frame.current_node_id, preblock_frame.current_display_id, result)
                return result

        edge_frame, edge_press = self._resolve_edge_contact(frame, routed_event)
        if edge_press is None:
            self._remember_route_sample(frame.current_node_id, frame.current_display_id, routed_event)
            return resolved_event
        direction = edge_press.direction
        cross_ratio = edge_press.cross_axis_ratio

        route = self._routing.resolve(
            layout=edge_frame.layout,
            self_node_id=self.ctx.self_node.node_id,
            current_node_id=edge_frame.current_node_id,
            current_display_id=edge_frame.current_display_id,
            direction=direction,
            cross_axis_ratio=cross_ratio,
            is_target_online=self._target_is_online,
            allow_remote_switch=bool(edge_frame.layout.auto_switch.enabled),
        )
        self._log_route_debug_once(
            edge_frame.now,
            (
                edge_frame.current_node_id,
                edge_frame.current_display_id,
                direction,
                route.kind,
                None if route.destination is None else route.destination.node_id,
                None if route.destination is None else route.destination.display_id,
                route.reason,
            ),
            "[AUTO SWITCH DEBUG] route %s:%s %s -> %s",
            edge_frame.current_node_id,
            edge_frame.current_display_id,
            direction,
            describe_edge_route(route),
        )
        transition = EdgeTransition(
            frame=edge_frame,
            direction=direction,
            cross_ratio=cross_ratio,
            event=resolved_event,
        )
        result = self._executor.apply_route(transition, route)
        self._remember_resulting_sample(edge_frame.current_node_id, edge_frame.current_display_id, result)
        return result

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
        hold = self._executor.edge_hold_context(current_node_id=active_target)
        current_display_id = None if hold is None else hold.display_id
        if current_display_id is None:
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

        hold_result = self._executor.continue_edge_hold(translated, frame)
        if hold_result is not None:
            remote_event = hold_result.event if isinstance(hold_result, MoveProcessingResult) else hold_result
            self._remote_pointer.sync_from_remote_event(
                node_id=active_target,
                display_id=frame.current_display_id,
                event=remote_event,
            )
            if self._executor.edge_hold_context(current_node_id=frame.current_node_id) is None:
                self._clear_route_sample(frame.current_node_id)
            else:
                self._remember_resulting_sample(frame.current_node_id, frame.current_display_id, hold_result)
            if isinstance(hold_result, MoveProcessingResult):
                return hold_result
            return MoveProcessingResult(hold_result, True)

        edge_frame, edge_press = self._resolve_edge_contact(frame, translated)
        if edge_press is None:
            self._remote_pointer.sync_from_remote_event(
                node_id=active_target,
                display_id=frame.current_display_id,
                event=translated,
            )
            self._remember_route_sample(frame.current_node_id, frame.current_display_id, translated)
            return MoveProcessingResult(translated, True)

        route = self._routing.resolve(
            layout=edge_frame.layout,
            self_node_id=self.ctx.self_node.node_id,
            current_node_id=edge_frame.current_node_id,
            current_display_id=edge_frame.current_display_id,
            direction=edge_press.direction,
            cross_axis_ratio=edge_press.cross_axis_ratio,
            is_target_online=self._target_is_online,
            allow_remote_switch=bool(edge_frame.layout.auto_switch.enabled),
        )
        self._log_route_debug_once(
            edge_frame.now,
            (
                edge_frame.current_node_id,
                edge_frame.current_display_id,
                edge_press.direction,
                route.kind,
                None if route.destination is None else route.destination.node_id,
                None if route.destination is None else route.destination.display_id,
                route.reason,
            ),
            "[AUTO SWITCH DEBUG] route %s:%s %s -> %s",
            edge_frame.current_node_id,
            edge_frame.current_display_id,
            edge_press.direction,
            describe_edge_route(route),
        )
        transition = EdgeTransition(
            frame=edge_frame,
            direction=edge_press.direction,
            cross_ratio=edge_press.cross_axis_ratio,
            event=translated,
        )
        result = self._executor.apply_route(transition, route)
        if isinstance(result, MoveProcessingResult):
            if result.event is not None and result.event.get("kind") == "mouse_move":
                next_display_id = self._display_state.state.get(edge_frame.current_node_id, edge_frame.current_display_id)
                self._remote_pointer.sync_from_remote_event(
                    node_id=edge_frame.current_node_id,
                    display_id=next_display_id,
                    event=result.event,
                )
                self._remember_route_sample(edge_frame.current_node_id, next_display_id, result.event)
            else:
                self._clear_route_sample(edge_frame.current_node_id)
            return MoveProcessingResult(result.event, True)
        self._remote_pointer.sync_from_remote_event(
            node_id=edge_frame.current_node_id,
            display_id=edge_frame.current_display_id,
            event=result,
        )
        self._remember_route_sample(edge_frame.current_node_id, edge_frame.current_display_id, result)
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
        hold = self._executor.edge_hold_context(current_node_id=current_node_id)
        if hold is not None:
            current_display_id = hold.display_id
            self._display_state.remember(current_node_id, current_display_id)
        else:
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

    def _remember_route_sample(self, node_id: str, display_id: str, event: dict) -> None:
        if event.get("kind") != "mouse_move":
            self._clear_route_sample(node_id)
            return
        if event.get("x") is None or event.get("y") is None:
            self._clear_route_sample(node_id)
            return
        self._last_route_sample_by_node[node_id] = _RouteSample(
            node_id=node_id,
            display_id=display_id,
            event=dict(event),
        )

    def _remember_resulting_sample(self, node_id: str, display_id: str, result) -> None:
        event = result.event if isinstance(result, MoveProcessingResult) else result
        if isinstance(event, dict) and event.get("kind") == "mouse_move":
            self._remember_route_sample(node_id, display_id, event)
            return
        self._clear_route_sample(node_id)

    def _clear_route_sample(self, node_id: str | None = None) -> None:
        if node_id is None:
            self._last_route_sample_by_node.clear()
            return
        self._last_route_sample_by_node.pop(node_id, None)

    @staticmethod
    def _frame_with_display(frame: AutoSwitchFrame, display_id: str) -> AutoSwitchFrame:
        if frame.current_display_id == display_id:
            return frame
        return AutoSwitchFrame(
            layout=frame.layout,
            current_node_id=frame.current_node_id,
            current_node=frame.current_node,
            current_display_id=display_id,
            bounds=frame.bounds,
            now=frame.now,
        )

    def _resolve_edge_contact(self, frame: AutoSwitchFrame, event: dict) -> tuple[AutoSwitchFrame, EdgePress | None]:
        previous = self._last_route_sample_by_node.get(frame.current_node_id)
        if previous is not None and previous.display_id != frame.current_display_id:
            crossing = detect_edge_crossing(
                self._display_state.display_pixel_rect(frame.current_node, previous.display_id, frame.bounds),
                previous.event,
                event,
            )
            if crossing is not None:
                return self._frame_with_display(frame, previous.display_id), crossing

        edge_press = detect_edge_press(
            self._display_state.display_pixel_rect(frame.current_node, frame.current_display_id, frame.bounds),
            event,
        )
        if edge_press is not None:
            return frame, edge_press

        if previous is None or previous.display_id == frame.current_display_id:
            return frame, None

        crossing = detect_edge_crossing(
            self._display_state.display_pixel_rect(frame.current_node, previous.display_id, frame.bounds),
            previous.event,
            event,
        )
        if crossing is None:
            return frame, None
        return self._frame_with_display(frame, previous.display_id), crossing

    def _resolve_self_preblock_contact(self, frame: AutoSwitchFrame, event: dict) -> tuple[AutoSwitchFrame, EdgePress | None]:
        if frame.current_node_id != self.ctx.self_node.node_id:
            return frame, None
        previous = self._last_route_sample_by_node.get(frame.current_node_id)
        if previous is None:
            return frame, None
        display_id = previous.display_id
        preblock = detect_edge_approach(
            self._display_state.display_pixel_rect(frame.current_node, display_id, frame.bounds),
            previous.event,
            event,
            self.SELF_PREBLOCK_BAND_PX,
        )
        if preblock is None:
            return frame, None
        return self._frame_with_display(frame, display_id), preblock

    def _log_route_debug_once(self, now: float, key, message: str, *args) -> None:
        if key == self._last_route_debug_key and (now - self._last_route_debug_at) < self.ROUTE_DEBUG_DEDUP_WINDOW_SEC:
            return
        self._last_route_debug_key = key
        self._last_route_debug_at = now
        logging.debug(message, *args)
