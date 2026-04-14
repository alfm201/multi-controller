"""Edge action execution for auto-switch routing."""

from __future__ import annotations

import logging

from capture.input_capture import MoveProcessingResult
from routing.edge_runtime import EdgeTransition


class EdgeActionExecutor:
    """Execute resolved edge routes while keeping switch guard state."""

    REPOSITION_STALE_MOVE_WINDOW_SEC = 0.05
    ACTION_LOG_DEDUP_WINDOW_SEC = 0.25
    BLOCK_EDGE_HOLD_SEC = 0.015

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
        self._edge_hold_until = 0.0
        self._edge_hold_rect = None
        self._edge_hold_key = None

    def should_drop_stale_move(self, event: dict) -> bool:
        event_ts = _safe_event_ts(event)
        if event_ts is None or event_ts > self._drop_moves_until_ts:
            return False
        return True

    def release_expired_edge_hold(self, now: float, *, force: bool = False) -> None:
        if self._edge_hold_rect is None:
            return
        if not force and now < self._edge_hold_until:
            return
        if self.pointer_clipper is not None:
            self.pointer_clipper.clear_clip()
        self._edge_hold_rect = None
        self._edge_hold_key = None
        self._edge_hold_until = 0.0
        return True

    def maybe_release_edge_hold(self, event: dict, frame) -> bool:
        if self._edge_hold_rect is None or self._edge_hold_key is None:
            return False
        node_id, display_id, direction = self._edge_hold_key
        if node_id != frame.current_node_id or display_id != frame.current_display_id:
            self.release_expired_edge_hold(frame.now, force=True)
            return True
        if event.get("x") is None or event.get("y") is None:
            return False
        x = int(event["x"])
        y = int(event["y"])
        left, top, right, bottom = self._edge_hold_rect
        moved_inward = (
            (direction == "left" and x > left)
            or (direction == "right" and x < right)
            or (direction == "up" and y > top)
            or (direction == "down" and y < bottom)
        )
        if moved_inward:
            self.release_expired_edge_hold(frame.now, force=True)
            return True
        return False

    def is_inside_anchor_guard(self, event: dict, now: float) -> bool:
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
            logging.info(
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
            logging.info(
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
            else:
                self._log_action_once(
                    frame.now,
                    ("self-block", frame.current_display_id, transition.direction),
                    "[AUTO SWITCH] self dead edge blocked on %s via %s edge",
                    frame.current_display_id,
                    transition.direction,
                )
            self._warp_pointer(anchor_event)
            self._hold_self_block_edge(transition)
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
            ("target-block", frame.current_node_id, frame.current_display_id, transition.direction),
            "[AUTO SWITCH] target edge blocked on %s:%s via %s edge",
            frame.current_node_id,
            frame.current_display_id,
            transition.direction,
        )
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
            logging.info(
                "[AUTO SWITCH] self internal display %s -> %s via %s edge",
                frame.current_display_id,
                destination.display_id,
                transition.direction,
            )
            return MoveProcessingResult(None, True)

        logging.info(
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
        if "x" in anchor_event and "y" in anchor_event:
            self._anchor_pixel = (int(anchor_event["x"]), int(anchor_event["y"]))
            return
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

    def _hold_self_block_edge(self, transition: EdgeTransition) -> None:
        if self.pointer_clipper is None:
            return
        hold_key = (
            transition.frame.current_node_id,
            transition.frame.current_display_id,
            transition.direction,
        )
        if self._edge_hold_key == hold_key and transition.frame.now < self._edge_hold_until:
            return
        rect = self.display_state.build_edge_hold_rect(
            transition.frame.current_node,
            transition.frame.current_display_id,
            transition.direction,
            transition.frame.bounds,
        )
        if self.pointer_clipper.clip_to_rect(*rect):
            self._edge_hold_rect = rect
            self._edge_hold_key = hold_key
            self._edge_hold_until = transition.frame.now + self.BLOCK_EDGE_HOLD_SEC

    def _log_action_once(self, now: float, key, message: str, *args) -> None:
        if key == self._last_action_log_key and (now - self._last_action_log_at) < self.ACTION_LOG_DEDUP_WINDOW_SEC:
            return
        self._last_action_log_key = key
        self._last_action_log_at = now
        logging.info(message, *args)


def _safe_event_ts(event: dict) -> float | None:
    try:
        value = event.get("ts")
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
