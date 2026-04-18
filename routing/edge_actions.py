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
    clip_rect: tuple[int, int, int, int] | None
    axis_tolerance_px: int
    uses_local_clip: bool
    entered_at: float
    last_seen_at: float
    release_distance_px: int
    release_consecutive_samples: int
    max_rebound_drift_px: int
    current_inward_samples: int = 0
    state: str = "latched"
    guard_reason: str | None = None


@dataclass(frozen=True)
class _LocalHoldObservation:
    inward_distance_px: int
    pressing_blocked_edge: bool
    rebound: bool
    clip_matches: bool | None
    leaked_outward: bool
    source_pressing_blocked_edge: bool

    @property
    def stable(self) -> bool:
        return (
            self.pressing_blocked_edge
            and self.clip_matches is True
            and not self.leaked_outward
        )

    @property
    def disturbed(self) -> bool:
        return self.clip_matches is not True or self.leaked_outward or self.ambiguous

    @property
    def ambiguous(self) -> bool:
        return self.rebound and not self.stable


class EdgeActionExecutor:
    """Execute resolved edge routes while keeping switch guard state."""

    REPOSITION_STALE_MOVE_WINDOW_SEC = 0.05
    ACTION_LOG_DEDUP_WINDOW_SEC = 0.25
    LOCAL_EDGE_HOLD_AXIS_TOLERANCE_PX = 1
    LOCAL_EDGE_HOLD_RELEASE_DISTANCE_PX = 3
    LOCAL_EDGE_HOLD_RELEASE_CONSECUTIVE_SAMPLES = 2
    LOCAL_EDGE_HOLD_MAX_REBOUND_DRIFT_PX = 3
    REMOTE_EDGE_HOLD_RELEASE_DISTANCE_PX = 1
    REMOTE_EDGE_HOLD_RELEASE_CONSECUTIVE_SAMPLES = 1
    REMOTE_EDGE_HOLD_MAX_REBOUND_DRIFT_PX = 0

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
        self._clear_self_gate_after_repair = False

    def should_drop_stale_move(self, event: dict) -> bool:
        event_ts = _safe_event_ts(event)
        if event_ts is None or event_ts > self._drop_moves_until_ts:
            return False
        hold = self._edge_hold
        if hold is not None and hold.uses_local_clip and self._is_inward_stale_move(event, hold):
            return False
        return True

    def release_edge_hold(self) -> bool:
        hold = self._edge_hold
        if hold is None:
            return False
        if hold.uses_local_clip and self.pointer_clipper is not None:
            self.pointer_clipper.clear_clip()
        self._edge_hold = None
        return True

    def release_expired_edge_hold(self, now: float, *, force: bool = False) -> None:
        if not force:
            return False
        return self.release_edge_hold()

    def sync_edge_hold(self, now: float, *, current_node_id: str | None = None) -> None:
        hold = self._edge_hold
        if hold is None:
            return
        if current_node_id is not None and hold.node_id != current_node_id:
            self.release_edge_hold()

    def edge_hold_context(self, *, current_node_id: str | None = None) -> _EdgeHold | None:
        hold = self._edge_hold
        if hold is None:
            return None
        if current_node_id is not None and hold.node_id != current_node_id:
            return None
        return hold

    def mark_local_hold_risk(self, *, reason: str = "external") -> bool:
        hold = self._edge_hold
        if hold is None or not hold.uses_local_clip:
            return False
        hold.state = "guarded"
        hold.guard_reason = str(reason)
        return True

    def refresh_local_hold_clip(self) -> bool:
        hold = self._edge_hold
        return self._refresh_local_hold_clip(hold)

    def consume_self_gate_clear_request(self) -> bool:
        should_clear = self._clear_self_gate_after_repair
        self._clear_self_gate_after_repair = False
        return should_clear

    def continue_edge_hold(self, event: dict, frame, *, source_event: dict | None = None):
        state = self._edge_hold_state(event, frame)
        if state is None:
            return None
        hold = state["hold"]
        if hold.uses_local_clip:
            return self._continue_local_edge_hold(
                event,
                frame,
                hold,
                state,
                source_event=source_event,
            )
        return self._continue_remote_edge_hold(
            event,
            frame,
            hold,
            state,
            source_event=source_event,
        )

    def _continue_local_edge_hold(
        self,
        event: dict,
        frame,
        hold: _EdgeHold,
        state: dict,
        *,
        source_event: dict | None = None,
    ):
        observation = self._observe_local_hold(
            event,
            frame,
            hold,
            state,
            source_event=source_event,
        )
        self._mark_hold_seen(hold, frame.now)

        if observation.inward_distance_px > 0:
            hold.current_inward_samples += 1
            hold.state = "guarded"
            if self._should_release_edge_hold(hold, observation.inward_distance_px):
                self.release_edge_hold()
                return event
            return MoveProcessingResult(None, True)

        hold.current_inward_samples = 0
        if hold.state == "latched" and (hold.guard_reason is not None or observation.disturbed):
            hold.state = "guarded"

        if hold.state != "guarded":
            return event

        repaired_clip = False
        if observation.clip_matches is False:
            repaired_clip = self._refresh_local_hold_clip(hold)
            observation = self._replace_local_hold_clip_matches(
                observation,
                self._local_hold_clip_matches(hold),
            )

        if observation.leaked_outward:
            return self._repair_local_hold_leak(
                event,
                frame,
                hold,
                source_event=source_event,
            )

        if observation.clip_matches is False and repaired_clip:
            return MoveProcessingResult(None, True)

        if observation.stable:
            hold.state = "latched"
            hold.guard_reason = None
            return event

        return MoveProcessingResult(None, True)

    def _continue_remote_edge_hold(
        self,
        event: dict,
        frame,
        hold: _EdgeHold,
        state: dict,
        *,
        source_event: dict | None = None,
    ):
        source_state = None
        if source_event is not None:
            source_state = self._edge_hold_state(source_event, frame)

        rebound = bool(event.get("__self_event_rebound__"))
        source_axis_delta = None
        if source_event is not None:
            source_axis_delta = self._hold_axis_delta(event, source_event, hold.direction)

        source_pressing_blocked_edge = False
        if source_state is not None:
            source_pressing_blocked_edge = source_state["pressing_blocked_edge"]
            if (
                rebound
                and source_axis_delta is not None
                and source_axis_delta > hold.max_rebound_drift_px
            ):
                source_pressing_blocked_edge = False

        inward_distance = state["inward_distance_px"]
        pressing_blocked_edge = state["pressing_blocked_edge"] or source_pressing_blocked_edge
        self._mark_hold_seen(hold, frame.now)

        if inward_distance <= 0:
            hold.current_inward_samples = 0
            return self._continue_held_event(
                event,
                frame,
                hold,
                pressing_blocked_edge=pressing_blocked_edge,
            )

        hold.current_inward_samples += 1
        if (
            inward_distance >= hold.release_distance_px
            or hold.current_inward_samples >= hold.release_consecutive_samples
        ):
            self.release_edge_hold()
            return event
        return self._continue_held_event(
            event,
            frame,
            hold,
            pressing_blocked_edge=pressing_blocked_edge,
        )

    def apply_edge_hold_routing_hint(self, event: dict, *, current_node_id: str) -> dict:
        hold = self._edge_hold
        if hold is None or not hold.uses_local_clip:
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
            self.release_edge_hold()
            return transition.event

        if route.kind == "block":
            return self._apply_block(
                transition=transition,
                route=route,
            )

        destination = route.destination
        if destination is None:
            self.release_edge_hold()
            return transition.event

        if route.kind == "self-warp":
            self.release_edge_hold()
            return self._apply_internal_warp(
                transition=transition,
                destination=destination,
            )

        if route.kind != "target-switch":
            self.release_edge_hold()
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
        self.release_edge_hold()

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
            hold_started = self._begin_edge_hold(transition, uses_local_clip=True)
            if not hold_started:
                self._warp_pointer(anchor_event)
                self._mark_reposition_window(transition.event)
                return MoveProcessingResult(None, True)
            hold = self._edge_hold
            if hold is not None:
                repaired = self._repair_initial_local_block_leak(
                    transition,
                    hold,
                )
                if repaired is not None:
                    return repaired
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

        if frame.current_node_id == self.ctx.self_node.node_id:
            self._clear_local_clip_for_self_warp()
            anchor_event = self._adjust_self_anchor_inside_display(
                anchor_event,
                node=frame.current_node,
                display_id=destination.display_id,
                bounds=frame.bounds,
                direction=transition.direction,
            )
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

    def _clear_local_clip_for_self_warp(self) -> bool:
        if self.pointer_clipper is None:
            return False
        return bool(self.pointer_clipper.clear_clip())

    def _adjust_self_anchor_inside_display(
        self,
        anchor_event: dict,
        *,
        node,
        display_id: str,
        bounds,
        direction: str,
    ) -> dict:
        adjusted = dict(anchor_event)
        if hasattr(self.display_state, "display_pixel_rect"):
            left, top, right, bottom = self.display_state.display_pixel_rect(
                node,
                display_id,
                bounds,
            )
            if int(adjusted["x"]) <= left:
                adjusted["x"] = min(left + 1, right)
            elif int(adjusted["x"]) >= right:
                adjusted["x"] = max(right - 1, left)
            if int(adjusted["y"]) <= top:
                adjusted["y"] = min(top + 1, bottom)
            elif int(adjusted["y"]) >= bottom:
                adjusted["y"] = max(bottom - 1, top)
            return adjusted

        if direction == "left":
            adjusted["x"] = int(adjusted["x"]) - 1
        elif direction == "right":
            adjusted["x"] = int(adjusted["x"]) + 1
        elif direction == "up":
            adjusted["y"] = int(adjusted["y"]) - 1
        elif direction == "down":
            adjusted["y"] = int(adjusted["y"]) + 1
        return adjusted

    def _begin_edge_hold(self, transition: EdgeTransition, *, uses_local_clip: bool) -> bool:
        rect = self.display_state.build_edge_hold_rect(
            transition.frame.current_node,
            transition.frame.current_display_id,
            transition.direction,
            transition.frame.bounds,
        )
        clip_rect = rect
        hold_key = (
            transition.frame.current_node_id,
            transition.frame.current_display_id,
            transition.direction,
        )
        hold = self._edge_hold
        if hold is not None:
            current_key = (hold.node_id, hold.display_id, hold.direction)
            if current_key == hold_key and hold.uses_local_clip == uses_local_clip:
                return True
            self.release_edge_hold()
        if uses_local_clip:
            if self.pointer_clipper is None:
                return False
            if hasattr(self.display_state, "build_local_edge_clip_rect"):
                clip_rect = self.display_state.build_local_edge_clip_rect(
                    transition.frame.current_node,
                    transition.frame.current_display_id,
                    transition.direction,
                    transition.frame.bounds,
                )
            if not self.pointer_clipper.clip_to_rect(*clip_rect):
                return False
        release_distance_px, release_consecutive_samples, max_rebound_drift_px = self._edge_hold_release_params(
            uses_local_clip
        )
        self._edge_hold = _EdgeHold(
            node_id=transition.frame.current_node_id,
            display_id=transition.frame.current_display_id,
            direction=transition.direction,
            rect=rect,
            clip_rect=clip_rect if uses_local_clip else None,
            axis_tolerance_px=self.LOCAL_EDGE_HOLD_AXIS_TOLERANCE_PX if uses_local_clip else 0,
            uses_local_clip=uses_local_clip,
            entered_at=transition.frame.now,
            last_seen_at=transition.frame.now,
            release_distance_px=release_distance_px,
            release_consecutive_samples=release_consecutive_samples,
            max_rebound_drift_px=max_rebound_drift_px,
        )
        return True

    def _edge_hold_state(self, event: dict, frame):
        hold = self._edge_hold
        if hold is None:
            return None
        if hold.node_id != frame.current_node_id or hold.display_id != frame.current_display_id:
            self.release_edge_hold()
            return None
        if event.get("x") is None or event.get("y") is None:
            return None
        x = int(event["x"])
        y = int(event["y"])
        left, top, right, bottom = hold.rect
        tolerance = max(int(hold.axis_tolerance_px), 0)
        inward_distance_px = 0
        pressing_blocked_edge = False
        if hold.direction == "left":
            blocked_limit = left + tolerance
            inward_distance_px = max(x - blocked_limit, 0)
            pressing_blocked_edge = x <= blocked_limit
        elif hold.direction == "right":
            blocked_limit = right - tolerance
            inward_distance_px = max(blocked_limit - x, 0)
            pressing_blocked_edge = x >= blocked_limit
        elif hold.direction == "up":
            blocked_limit = top + tolerance
            inward_distance_px = max(y - blocked_limit, 0)
            pressing_blocked_edge = y <= blocked_limit
        elif hold.direction == "down":
            blocked_limit = bottom - tolerance
            inward_distance_px = max(blocked_limit - y, 0)
            pressing_blocked_edge = y >= blocked_limit
        return {
            "hold": hold,
            "moved_inward": inward_distance_px > 0,
            "inward_distance_px": inward_distance_px,
            "pressing_blocked_edge": pressing_blocked_edge,
        }

    def _continue_held_event(self, event: dict, frame, hold: _EdgeHold, *, pressing_blocked_edge: bool):
        if hold.uses_local_clip:
            return event
        if not pressing_blocked_edge:
            return None
        return self._pin_edge_hold_event(event, frame, hold)

    def _observe_local_hold(
        self,
        event: dict,
        frame,
        hold: _EdgeHold,
        state: dict,
        *,
        source_event: dict | None = None,
    ) -> _LocalHoldObservation:
        rebound = bool(event.get("__self_event_rebound__"))
        clip_matches = self._local_hold_clip_matches(hold)
        leaked_outward = self._hold_outward_overflow_px(event, hold) > 0
        source_pressing_blocked_edge = self._source_pressing_blocked_edge(
            event,
            frame,
            hold,
            source_event=source_event,
            rebound=rebound,
        )
        return _LocalHoldObservation(
            inward_distance_px=state["inward_distance_px"],
            pressing_blocked_edge=state["pressing_blocked_edge"] or source_pressing_blocked_edge,
            rebound=rebound,
            clip_matches=clip_matches,
            leaked_outward=leaked_outward,
            source_pressing_blocked_edge=source_pressing_blocked_edge,
        )

    def _source_pressing_blocked_edge(
        self,
        event: dict,
        frame,
        hold: _EdgeHold,
        *,
        source_event: dict | None = None,
        rebound: bool = False,
    ) -> bool:
        if source_event is None:
            return False
        source_state = self._edge_hold_state(source_event, frame)
        if source_state is None:
            return False
        source_pressing_blocked_edge = source_state["pressing_blocked_edge"]
        if not rebound:
            return source_pressing_blocked_edge
        source_axis_delta = self._hold_axis_delta(event, source_event, hold.direction)
        if source_axis_delta is None or source_axis_delta <= hold.max_rebound_drift_px:
            return source_pressing_blocked_edge
        return False

    def _repair_local_hold_leak(
        self,
        event: dict,
        frame,
        hold: _EdgeHold,
        *,
        source_event: dict | None = None,
    ) -> MoveProcessingResult:
        clip_cleared_for_warp = False
        if hold.clip_rect is not None:
            self._refresh_local_hold_clip(hold)
            if self._local_hold_clip_matches(hold) is False:
                clip_cleared_for_warp = self._clear_local_clip_for_self_warp()
        anchor_event = self.display_state.build_edge_anchor_event(
            frame.current_node,
            hold.display_id,
            hold.direction,
            self._hold_cross_axis_ratio(event, hold),
            frame.bounds,
            source_event=source_event if source_event is not None else event,
            blocked=True,
        )
        if frame.current_node_id == self.ctx.self_node.node_id:
            anchor_event = self._adjust_self_anchor_inside_display(
                anchor_event,
                node=frame.current_node,
                display_id=hold.display_id,
                bounds=frame.bounds,
                direction=hold.direction,
            )
        self._warp_pointer(anchor_event)
        if frame.current_node_id == self.ctx.self_node.node_id:
            self._clear_self_gate_after_repair = True
        if clip_cleared_for_warp and hold.clip_rect is not None:
            self._refresh_local_hold_clip(hold)
        self._record_anchor_guard(
            anchor_event,
            frame.now,
            frame.layout.auto_switch.return_guard_ms,
        )
        self._mark_reposition_window(source_event if source_event is not None else event)
        self._mark_hold_seen(hold, frame.now)
        return MoveProcessingResult(None, True)

    def _repair_initial_local_block_leak(
        self,
        transition: EdgeTransition,
        hold: _EdgeHold,
    ) -> MoveProcessingResult | None:
        if self._hold_outward_overflow_px(transition.event, hold) <= 0:
            return None
        actual_event = self._current_local_hold_event(transition.frame, hold, fallback_event=transition.event)
        if actual_event is None:
            return None
        if self._hold_outward_overflow_px(actual_event, hold) <= 0:
            return None
        self._log_action_once(
            transition.frame.now,
            (
                "self-block-fallback",
                transition.frame.current_display_id,
                transition.direction,
            ),
            "[AUTO SWITCH] self block fallback warp on %s via %s edge after clip could not confine pointer",
            transition.frame.current_display_id,
            transition.direction,
        )
        return self._repair_local_hold_leak(
            actual_event,
            transition.frame,
            hold,
            source_event=transition.event,
        )

    def _local_hold_clip_matches(self, hold: _EdgeHold) -> bool | None:
        if not hold.uses_local_clip or hold.clip_rect is None:
            return True
        if self.pointer_clipper is None or not hasattr(self.pointer_clipper, "current_clip_rect"):
            return None
        current = self.pointer_clipper.current_clip_rect()
        if current is None:
            return False
        return tuple(int(value) for value in current) == hold.clip_rect

    @staticmethod
    def _replace_local_hold_clip_matches(
        observation: _LocalHoldObservation,
        clip_matches: bool | None,
    ) -> _LocalHoldObservation:
        return _LocalHoldObservation(
            inward_distance_px=observation.inward_distance_px,
            pressing_blocked_edge=observation.pressing_blocked_edge,
            rebound=observation.rebound,
            clip_matches=clip_matches,
            leaked_outward=observation.leaked_outward,
            source_pressing_blocked_edge=observation.source_pressing_blocked_edge,
        )

    @staticmethod
    def _mark_hold_seen(hold: _EdgeHold, now: float) -> None:
        hold.last_seen_at = now

    def _refresh_local_hold_clip(self, hold: _EdgeHold | None) -> bool:
        if hold is None or not hold.uses_local_clip or hold.clip_rect is None:
            return False
        if self.pointer_clipper is None:
            return False
        return bool(self.pointer_clipper.clip_to_rect(*hold.clip_rect))

    def _current_local_hold_event(self, frame, hold: _EdgeHold, *, fallback_event: dict | None = None) -> dict | None:
        if not hold.uses_local_clip:
            return None
        provider = getattr(self.display_state, "actual_pointer_position", None)
        if not callable(provider):
            return None
        try:
            actual_pos = provider(frame.current_node)
        except Exception:
            return None
        if actual_pos is None:
            return None
        try:
            x = int(actual_pos[0])
            y = int(actual_pos[1])
        except (TypeError, ValueError, IndexError):
            return None
        actual_event = {} if fallback_event is None else dict(fallback_event)
        actual_event["x"] = x
        actual_event["y"] = y
        return actual_event

    @staticmethod
    def _should_release_edge_hold(hold: _EdgeHold, inward_distance_px: int) -> bool:
        return (
            inward_distance_px >= hold.release_distance_px
            or hold.current_inward_samples >= hold.release_consecutive_samples
        )

    def _edge_hold_release_params(self, uses_local_clip: bool) -> tuple[int, int, int]:
        if uses_local_clip:
            return (
                self.LOCAL_EDGE_HOLD_RELEASE_DISTANCE_PX,
                self.LOCAL_EDGE_HOLD_RELEASE_CONSECUTIVE_SAMPLES,
                self.LOCAL_EDGE_HOLD_MAX_REBOUND_DRIFT_PX,
            )
        return (
            self.REMOTE_EDGE_HOLD_RELEASE_DISTANCE_PX,
            self.REMOTE_EDGE_HOLD_RELEASE_CONSECUTIVE_SAMPLES,
            self.REMOTE_EDGE_HOLD_MAX_REBOUND_DRIFT_PX,
        )

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

    @staticmethod
    def _is_inward_stale_move(event: dict, hold: _EdgeHold) -> bool:
        if event.get("x") is None or event.get("y") is None:
            return False
        x = int(event["x"])
        y = int(event["y"])
        left, top, right, bottom = hold.rect
        if hold.direction == "left":
            return x > left
        if hold.direction == "right":
            return x < right
        if hold.direction == "up":
            return y > top
        if hold.direction == "down":
            return y < bottom
        return False

    @staticmethod
    def _hold_outward_overflow_px(event: dict, hold: _EdgeHold) -> int:
        if event.get("x") is None or event.get("y") is None:
            return 0
        x = int(event["x"])
        y = int(event["y"])
        left, top, right, bottom = hold.rect
        if hold.direction == "left":
            return max(left - x, 0)
        if hold.direction == "right":
            return max(x - right, 0)
        if hold.direction == "up":
            return max(top - y, 0)
        if hold.direction == "down":
            return max(y - bottom, 0)
        return 0

    @staticmethod
    def _hold_cross_axis_ratio(event: dict, hold: _EdgeHold) -> float:
        left, top, right, bottom = hold.rect
        if hold.direction in {"left", "right"}:
            if event.get("y") is None:
                return 0.5
            span = max(bottom - top, 1)
            return min(max((int(event["y"]) - top) / float(span), 0.0), 1.0)
        if event.get("x") is None:
            return 0.5
        span = max(right - left, 1)
        return min(max((int(event["x"]) - left) / float(span), 0.0), 1.0)

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
