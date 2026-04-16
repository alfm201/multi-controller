"""Edge action execution for auto-switch routing."""

from __future__ import annotations

from dataclasses import dataclass

from capture.input_capture import MoveProcessingResult
from runtime.app_logging import log_detail
from runtime.display import normalize_position
from routing.edge_runtime import EdgeTransition


@dataclass
class _EdgeHold:
    node_id: str
    display_id: str
    direction: str
    rect: tuple[int, int, int, int]
    until: float
    uses_local_clip: bool


class EdgeActionExecutor:
    """Execute resolved edge routes while keeping switch guard state."""

    REPOSITION_STALE_MOVE_WINDOW_SEC = 0.05
    ACTION_LOG_DEDUP_WINDOW_SEC = 0.25
    BLOCK_EDGE_HOLD_SEC = 0.06

    def __init__(
        self,
        *,
        ctx,
        router,
        request_target,
        clear_target,
        pointer_mover,
        pointer_clipper,
        display_state,
    ):
        self.ctx = ctx
        self.router = router
        self.request_target = request_target
        self.clear_target = clear_target
        self.pointer_mover = pointer_mover
        self.pointer_clipper = pointer_clipper
        self.display_state = display_state

        self._last_switch_at = 0.0
        self._guard_until = 0.0
        self._anchor_norm = None
        self._anchor_pixel = None
        self._drop_moves_until_ts = 0.0
        self._last_action_log_key = None
        self._last_action_log_at = 0.0
        self._edge_hold: _EdgeHold | None = None

    def should_drop_stale_move(self, event: dict) -> bool:
        event_ts = _safe_event_ts(event)
        if event_ts is None or event_ts > self._drop_moves_until_ts:
            return False
        return True

    def release_expired_edge_hold(self, now: float, *, force: bool = False) -> None:
        hold = self._edge_hold
        if hold is None:
            return False
        if not force and now < hold.until:
            return
        if hold.uses_local_clip and self.pointer_clipper is not None:
            self.pointer_clipper.clear_clip()
        self._edge_hold = None
        return True

    def sync_edge_hold(self, now: float, *, current_node_id: str | None = None) -> None:
        hold = self._edge_hold
        if hold is None:
            return
        if current_node_id is not None and hold.node_id != current_node_id:
            self.release_expired_edge_hold(now, force=True)
            return
        self.release_expired_edge_hold(now)

    def continue_edge_hold(self, event: dict, frame, *, source_event: dict | None = None):
        state = self._edge_hold_state(event, frame)
        if state is None:
            return None
        hold = state["hold"]
        source_state = None
        if source_event is not None:
            source_state = self._edge_hold_state(source_event, frame)

        rebound = bool(event.get("__self_event_rebound__")) and hold.uses_local_clip
        source_axis_delta = None
        if source_event is not None:
            source_axis_delta = self._hold_axis_delta(event, source_event, hold.direction)

        moved_inward = state["moved_inward"] or (
            source_state is not None
            and source_state["moved_inward"]
            and (
                not rebound
                or source_axis_delta is None
                or source_axis_delta > 1
            )
        )
        pressing_blocked_edge = state["pressing_blocked_edge"] or (
            source_state is not None and source_state["pressing_blocked_edge"]
        )

        if moved_inward:
            self.release_expired_edge_hold(frame.now, force=True)
            return event
        if not pressing_blocked_edge:
            return None
        hold.until = frame.now + self.BLOCK_EDGE_HOLD_SEC
        if hold.uses_local_clip:
            return event
        return self._pin_edge_hold_event(event, frame, hold)

    def apply_edge_hold_routing_hint(self, event: dict, *, current_node_id: str) -> dict:
        hold = self._edge_hold
        if hold is None or not hold.uses_local_clip:
            return event
        if not event.get("__self_event_rebound__"):
            return event
        if hold.node_id != current_node_id:
            return event
        hinted = dict(event)
        hinted["__routing_display_id__"] = hold.display_id
        return hinted

    def is_inside_anchor_guard(self, event: dict, now: float) -> bool:
        if self._anchor_norm is None or now >= self._guard_until:
            return False
        if self._anchor_pixel is not None and event.get("x") is not None and event.get("y") is not None:
            inside = (
                abs(int(event["x"]) - self._anchor_pixel[0]) <= 1
                and abs(int(event["y"]) - self._anchor_pixel[1]) <= 1
            )
            if inside:
                return True
            self._clear_anchor_guard()
            return False
        try:
            x_norm = float(event["x_norm"])
            y_norm = float(event["y_norm"])
        except (KeyError, TypeError, ValueError):
            return False
        inside = abs(x_norm - self._anchor_norm[0]) <= 1e-9 and abs(y_norm - self._anchor_norm[1]) <= 1e-9
        if inside:
            return True
        self._clear_anchor_guard()
        return False

    def apply_route(self, transition: EdgeTransition, route):
        frame = transition.frame
        if route.kind == "allow":
            self.release_expired_edge_hold(frame.now)
            return transition.event

        if route.kind == "block":
            return self._apply_block(
                transition=transition,
                route=route,
            )

        destination = route.destination
        if destination is None:
            self.release_expired_edge_hold(frame.now)
            return transition.event

        if route.kind == "self-warp":
            self.release_expired_edge_hold(frame.now, force=True)
            return self._apply_internal_warp(
                transition=transition,
                destination=destination,
            )

        if route.kind != "target-switch":
            self.release_expired_edge_hold(frame.now)
            return transition.event

        if hasattr(self.router, "has_pressed_mouse_buttons") and self.router.has_pressed_mouse_buttons():
            self._log_action_once(
                frame.now,
                ("drag-switch-block", frame.current_node_id, frame.current_display_id, transition.direction),
                "[AUTO SWITCH] target switch blocked while dragging on %s:%s via %s edge",
                frame.current_node_id,
                frame.current_display_id,
                transition.direction,
            )
            return transition.event

        cooldown_sec = max(frame.layout.auto_switch.cooldown_ms, 0) / 1000.0
        if frame.now - self._last_switch_at < cooldown_sec:
            return transition.event
        self.release_expired_edge_hold(frame.now, force=True)

        destination_node = frame.layout.get_node(destination.node_id)
        if destination_node is None:
            return transition.event

        anchor_event = self.display_state.build_edge_anchor_event(
            destination_node,
            destination.display_id,
            transition.direction,
            transition.cross_ratio,
            self.display_state.node_screen_bounds(
                destination.node_id,
                destination_node,
                frame.bounds,
            ),
        )

        if destination.node_id != self.ctx.self_node.node_id and hasattr(self.router, "prepare_pointer_handoff"):
            self.router.prepare_pointer_handoff(anchor_event)

        if destination.node_id == self.ctx.self_node.node_id:
            if hasattr(self.router, "prepare_local_return"):
                self.router.prepare_local_return(anchor_event)
            self.clear_target()
            log_detail(
                "[AUTO SWITCH] %s:%s -> self:%s via %s edge",
                frame.current_node_id,
                frame.current_display_id,
                destination.display_id,
                transition.direction,
            )
            self.display_state.remember(destination.node_id, destination.display_id)
            self._record_switch(
                anchor_event,
                frame.now,
                frame.layout.auto_switch.return_guard_ms,
                transition.event,
            )
            return MoveProcessingResult(None, True)
        else:
            self.request_target(destination.node_id)
            log_detail(
                "[AUTO SWITCH] %s:%s -> %s:%s via %s edge",
                frame.current_node_id,
                frame.current_display_id,
                destination.node_id,
                destination.display_id,
                transition.direction,
            )
            self.display_state.remember(destination.node_id, destination.display_id)
            self._last_switch_at = frame.now
            return MoveProcessingResult(None, True)

    def _apply_block(
        self,
        *,
        transition: EdgeTransition,
        route,
    ):
        frame = transition.frame
        if frame.current_node_id == self.ctx.self_node.node_id:
            anchor_event = self.display_state.build_edge_anchor_event(
                frame.current_node,
                frame.current_display_id,
                transition.direction,
                transition.cross_ratio,
                frame.bounds,
                source_event=transition.event,
                blocked=True,
            )
            if route.reason == "offline-target" and route.destination is not None:
                self._log_action_once(
                    frame.now,
                    (
                        "self-offline-block",
                        frame.current_display_id,
                        transition.direction,
                        route.destination.node_id,
                    ),
                    "[AUTO SWITCH] self offline edge blocked on %s via %s edge (target=%s)",
                    frame.current_display_id,
                    transition.direction,
                    route.destination.node_id,
                )
            elif route.reason == "self-logical-gap":
                self._log_action_once(
                    frame.now,
                    (
                        "self-logical-gap",
                        frame.current_display_id,
                        transition.direction,
                        route.logical_neighbor_display_id,
                    ),
                    "[AUTO SWITCH] self logical gap blocked on %s via %s edge (logical=%s)",
                    frame.current_display_id,
                    transition.direction,
                    route.logical_neighbor_display_id or "?",
                )
            elif route.reason == "remote-switch-disabled" and route.destination is not None:
                self._log_action_once(
                    frame.now,
                    (
                        "self-remote-disabled",
                        frame.current_display_id,
                        transition.direction,
                        route.destination.node_id,
                        route.destination.display_id,
                    ),
                    "[AUTO SWITCH] remote switch disabled on %s via %s edge (target=%s:%s)",
                    frame.current_display_id,
                    transition.direction,
                    route.destination.node_id,
                    route.destination.display_id,
                )
            else:
                self._log_action_once(
                    frame.now,
                    ("self-block", frame.current_display_id, transition.direction),
                    "[AUTO SWITCH] self dead edge blocked on %s via %s edge",
                    frame.current_display_id,
                    transition.direction,
                )
            self._warp_pointer(anchor_event)
            self._begin_edge_hold(transition, uses_local_clip=True)
            return MoveProcessingResult(None, True)

        anchor_event = self.display_state.build_edge_anchor_event(
            frame.current_node,
            frame.current_display_id,
            transition.direction,
            transition.cross_ratio,
            frame.bounds,
            source_event=transition.event,
            blocked=True,
        )
        self._log_action_once(
            frame.now,
            (
                "target-logical-gap"
                if route.reason == "target-logical-gap"
                else "target-remote-disabled"
                if route.reason == "remote-switch-disabled" and route.destination is not None
                else "target-block",
                frame.current_node_id,
                frame.current_display_id,
                transition.direction,
                route.logical_neighbor_display_id,
                None if route.destination is None else route.destination.node_id,
                None if route.destination is None else route.destination.display_id,
            ),
            (
                "[AUTO SWITCH] target logical gap blocked on %s:%s via %s edge (logical=%s)"
                if route.reason == "target-logical-gap"
                else "[AUTO SWITCH] remote switch disabled on %s:%s via %s edge (target=%s:%s)"
                if route.reason == "remote-switch-disabled" and route.destination is not None
                else "[AUTO SWITCH] target edge blocked on %s:%s via %s edge"
            ),
            frame.current_node_id,
            frame.current_display_id,
            transition.direction,
            *(
                (route.logical_neighbor_display_id or "?",)
                if route.reason == "target-logical-gap"
                else (route.destination.node_id, route.destination.display_id)
                if route.reason == "remote-switch-disabled" and route.destination is not None
                else ()
            ),
        )
        self._begin_edge_hold(transition, uses_local_clip=False)
        return MoveProcessingResult(anchor_event, True)

    def _apply_internal_warp(
        self,
        *,
        transition: EdgeTransition,
        destination,
    ):
        frame = transition.frame
        anchor_event = self.display_state.build_edge_anchor_event(
            frame.current_node,
            destination.display_id,
            transition.direction,
            transition.cross_ratio,
            frame.bounds,
        )
        self.display_state.remember(frame.current_node_id, destination.display_id)

        self._warp_pointer(anchor_event)
        if frame.current_node_id == self.ctx.self_node.node_id:
            self._record_anchor_guard(
                anchor_event,
                frame.now,
                frame.layout.auto_switch.return_guard_ms,
            )
            log_detail(
                "[AUTO SWITCH] self internal display %s -> %s via %s edge",
                frame.current_display_id,
                destination.display_id,
                transition.direction,
            )
            return MoveProcessingResult(None, True)

        log_detail(
            "[AUTO SWITCH] target internal display %s:%s -> %s via %s edge",
            frame.current_node_id,
            frame.current_display_id,
            destination.display_id,
            transition.direction,
        )
        return MoveProcessingResult(anchor_event, True)

    def _record_switch(
        self,
        anchor_event: dict,
        now: float,
        return_guard_ms: int,
        source_event: dict | None = None,
    ) -> None:
        self._last_switch_at = now
        self._guard_until = now + max(int(return_guard_ms), 0) / 1000.0
        self._anchor_norm = (
            anchor_event.get("x_norm"),
            anchor_event.get("y_norm"),
        )
        self._mark_reposition_window(source_event)
        self._set_anchor_pixel(anchor_event)

    def _record_anchor_guard(
        self,
        anchor_event: dict,
        now: float,
        guard_ms: int,
    ) -> None:
        self._guard_until = now + max(int(guard_ms), 0) / 1000.0
        self._anchor_norm = (
            anchor_event.get("x_norm"),
            anchor_event.get("y_norm"),
        )
        self._set_anchor_pixel(anchor_event)

    def _set_anchor_pixel(self, anchor_event: dict) -> None:
        if "x" in anchor_event and "y" in anchor_event:
            self._anchor_pixel = (int(anchor_event["x"]), int(anchor_event["y"]))
            return
        self._anchor_pixel = None

    def _clear_anchor_guard(self) -> None:
        self._guard_until = 0.0
        self._anchor_norm = None
        self._anchor_pixel = None

    def _mark_reposition_window(self, source_event: dict | None) -> None:
        if source_event is None:
            return
        event_ts = _safe_event_ts(source_event)
        if event_ts is None:
            return
        self._drop_moves_until_ts = max(
            self._drop_moves_until_ts,
            event_ts + self.REPOSITION_STALE_MOVE_WINDOW_SEC,
        )

    def _warp_pointer(self, anchor_event: dict) -> None:
        if self.pointer_mover is None:
            return
        if "x" in anchor_event and "y" in anchor_event:
            self.pointer_mover(int(anchor_event["x"]), int(anchor_event["y"]))

    def _begin_edge_hold(self, transition: EdgeTransition, *, uses_local_clip: bool) -> None:
        rect = self.display_state.build_edge_hold_rect(
            transition.frame.current_node,
            transition.frame.current_display_id,
            transition.direction,
            transition.frame.bounds,
        )
        hold_key = (
            transition.frame.current_node_id,
            transition.frame.current_display_id,
            transition.direction,
        )
        hold = self._edge_hold
        if hold is not None:
            current_key = (hold.node_id, hold.display_id, hold.direction)
            if current_key == hold_key and hold.uses_local_clip == uses_local_clip and transition.frame.now < hold.until:
                return
            self.release_expired_edge_hold(transition.frame.now, force=True)
        if uses_local_clip:
            if self.pointer_clipper is None:
                return
            if not self.pointer_clipper.clip_to_rect(*rect):
                return
        self._edge_hold = _EdgeHold(
            node_id=transition.frame.current_node_id,
            display_id=transition.frame.current_display_id,
            direction=transition.direction,
            rect=rect,
            until=transition.frame.now + self.BLOCK_EDGE_HOLD_SEC,
            uses_local_clip=uses_local_clip,
        )

    def _edge_hold_state(self, event: dict, frame):
        hold = self._edge_hold
        if hold is None:
            return None
        if hold.node_id != frame.current_node_id or hold.display_id != frame.current_display_id:
            self.release_expired_edge_hold(frame.now, force=True)
            return None
        if event.get("x") is None or event.get("y") is None:
            return None
        x = int(event["x"])
        y = int(event["y"])
        left, top, right, bottom = hold.rect
        moved_inward = (
            (hold.direction == "left" and x > left)
            or (hold.direction == "right" and x < right)
            or (hold.direction == "up" and y > top)
            or (hold.direction == "down" and y < bottom)
        )
        pressing_blocked_edge = (
            (hold.direction == "left" and x <= left)
            or (hold.direction == "right" and x >= right)
            or (hold.direction == "up" and y <= top)
            or (hold.direction == "down" and y >= bottom)
        )
        return {
            "hold": hold,
            "moved_inward": moved_inward,
            "pressing_blocked_edge": pressing_blocked_edge,
        }

    def _pin_edge_hold_event(self, event: dict, frame, hold: _EdgeHold) -> dict:
        pinned = dict(event)
        left, top, right, bottom = hold.rect
        if hold.direction == "left":
            pinned["x"] = left
        elif hold.direction == "right":
            pinned["x"] = right
        elif hold.direction == "up":
            pinned["y"] = top
        elif hold.direction == "down":
            pinned["y"] = bottom
        bounds_arg = (
            frame.bounds.left,
            frame.bounds.top,
            frame.bounds.width,
            frame.bounds.height,
        ) if hasattr(frame.bounds, "left") else frame.bounds
        norm_x, norm_y = normalize_position(
            int(pinned["x"]),
            int(pinned["y"]),
            bounds_arg,
        )
        pinned["x_norm"] = norm_x
        pinned["y_norm"] = norm_y
        return pinned

    @staticmethod
    def _hold_axis_delta(event: dict, source_event: dict, direction: str) -> int | None:
        axis = "x" if direction in {"left", "right"} else "y"
        if event.get(axis) is None or source_event.get(axis) is None:
            return None
        return abs(int(event[axis]) - int(source_event[axis]))

    def _log_action_once(self, now: float, key, message: str, *args) -> None:
        if key == self._last_action_log_key and (now - self._last_action_log_at) < self.ACTION_LOG_DEDUP_WINDOW_SEC:
            return
        self._last_action_log_key = key
        self._last_action_log_at = now
        log_detail(message, *args)


def _safe_event_ts(event: dict) -> float | None:
    try:
        value = event.get("ts")
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
