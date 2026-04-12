"""Boundary-based automatic target switching logic."""

import logging
import math
import time

from runtime.display import denormalize_position, get_virtual_screen_bounds, normalize_position
from runtime.layouts import (
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

    REPOSITION_STALE_MOVE_WINDOW_SEC = 0.05
    REPOSITION_SETTLE_WINDOW_SEC = 0.15
    REPOSITION_RETRY_INTERVAL_SEC = 0.01
    DEAD_EDGE_RELEASE_MARGIN_PX = 24
    DESTINATION_ENTRY_HOLD_MARGIN_PX = 32
    ENTRY_STRIP_THICKNESS_PX = 1

    def __init__(
        self,
        ctx,
        router,
        request_target,
        clear_target,
        is_target_online=None,
        pointer_mover=None,
        actual_pointer_provider=None,
        pointer_clipper=None,
        screen_bounds_provider=None,
        now_fn=None,
    ):
        self.ctx = ctx
        self.router = router
        self.request_target = request_target
        self.clear_target = clear_target
        self.is_target_online = is_target_online
        self.pointer_mover = pointer_mover
        self.actual_pointer_provider = actual_pointer_provider
        self.pointer_clipper = pointer_clipper
        self.screen_bounds_provider = screen_bounds_provider or get_virtual_screen_bounds
        self._now = now_fn or time.monotonic
        self._last_switch_at = 0.0
        self._guard_until = 0.0
        self._anchor_norm = None
        self._anchor_pixel = None
        self._drop_moves_until_ts = 0.0
        self._settle_until = 0.0
        self._settle_anchor_event = None
        self._settle_source_display_id = None
        self._settle_dest_display_id = None
        self._settle_direction = None
        self._settle_blocked = False
        self._last_settle_retry_at = 0.0
        self._last_actual_self_pointer = None
        self._pending_entry_release_display_id = None

    def process(self, event):
        """Look at mouse_move events and switch target when needed."""
        try:
            return self._process_mouse_move(event)
        except Exception as exc:
            logging.warning("[AUTO SWITCH] failed to process event: %s", exc)
            return event

    def refresh_self_clip(self) -> None:
        layout = self.ctx.layout
        if layout is None or not layout.auto_switch.enabled:
            self._clear_self_clip()
            return
        current_node_id = self.router.get_selected_target() or self.ctx.self_node.node_id
        if current_node_id != self.ctx.self_node.node_id:
            self._clear_self_clip()
            return
        current_node = layout.get_node(current_node_id)
        if current_node is None:
            self._clear_self_clip()
            return
        current_pos = self._get_actual_self_pointer(current_node)
        if current_pos is None:
            return
        current_display = self._resolve_actual_self_display(current_node, int(current_pos[0]), int(current_pos[1]))
        if current_display is None:
            return
        self._last_actual_self_pointer = current_pos
        self._clip_self_to_display(current_node, current_display.display_id, self.screen_bounds_provider())

    def _process_mouse_move(self, event):
        if event.get("kind") != "mouse_move":
            return event
        event_ts = _safe_event_ts(event)
        if event_ts is not None and event_ts <= self._drop_moves_until_ts:
            logging.debug(
                "[AUTO SWITCH DEBUG] drop stale move ts=%.6f until=%.6f pos=(%s,%s)",
                event_ts,
                self._drop_moves_until_ts,
                event.get("x"),
                event.get("y"),
            )
            return None
        layout = self.ctx.layout
        if layout is None or not layout.auto_switch.enabled:
            self._clear_self_clip()
            return event

        now = self._now()
        bounds = self.screen_bounds_provider()
        if self._inside_anchor_guard(event, now):
            return event

        current_node_id = self.router.get_selected_target() or self.ctx.self_node.node_id
        current_node = layout.get_node(current_node_id)
        if current_node is None:
            self._clear_self_clip()
            return event
        if current_node_id != self.ctx.self_node.node_id:
            self._clear_self_clip()
        if current_node_id == self.ctx.self_node.node_id:
            if self._release_pending_entry_clip(current_node, bounds):
                return None
            settled = self._process_settle_window(current_node, event, bounds, now)
            if settled is not event:
                return settled

        current_display, direction, cross_ratio = self._detect_display_edge(
            current_node,
            event,
            bounds,
        )
        if current_node_id == self.ctx.self_node.node_id and current_display is not None:
            self._clip_self_to_display(current_node, current_display.display_id, bounds)
        if current_display is not None and direction is not None:
            logging.debug(
                "[AUTO SWITCH DEBUG] current=%s:%s event=(%s,%s) dir=%s cross=%.4f",
                current_node_id,
                current_display.display_id,
                event.get("x"),
                event.get("y"),
                direction,
                cross_ratio,
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
                logical_neighbor = self._find_actual_logical_neighbor(
                    current_node,
                    current_display.display_id,
                    direction,
                    event,
                )
                if logical_neighbor is None:
                    logical_neighbor = find_adjacent_display_in_node(
                        current_node,
                        current_display.display_id,
                        direction,
                        cross_ratio,
                        logical=True,
                    )
                if logical_neighbor is not None:
                    self._clip_self_to_display(current_node, current_display.display_id, bounds)
                    logging.info(
                        "[AUTO SWITCH] self dead edge blocked on %s via %s edge",
                        current_display.display_id,
                        direction,
                    )
                    return None
            return event

        if next_display.node_id != current_node_id:
            cooldown_sec = max(layout.auto_switch.cooldown_ms, 0) / 1000.0
            if now - self._last_switch_at < cooldown_sec:
                return event

        if (
            next_display.node_id == current_node_id
            and current_node_id == self.ctx.self_node.node_id
            and next_display.display_id != current_display.display_id
        ):
            anchor_event = self._build_edge_anchor_event(
                current_node,
                next_display.display_id,
                direction,
                cross_ratio,
                bounds,
                source_event=event,
            )
            logging.debug(
                "[AUTO SWITCH DEBUG] self-warp %s -> %s dir=%s source=(%s,%s) anchor=(%s,%s)",
                current_display.display_id,
                next_display.display_id,
                direction,
                event.get("x"),
                event.get("y"),
                anchor_event.get("x"),
                anchor_event.get("y"),
            )
            self._clear_self_clip()
            self._warp_pointer(anchor_event)
            self._sync_last_actual_self_pointer(anchor_event)
            self._clip_self_to_entry_strip(current_node, next_display.display_id, direction, bounds)
            self._pending_entry_release_display_id = next_display.display_id
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
            if current_node_id == self.ctx.self_node.node_id:
                self._clip_self_to_display(current_node, current_display.display_id, bounds)
                logging.info(
                    "[AUTO SWITCH] self offline edge blocked on %s via %s edge (target=%s)",
                    current_display.display_id,
                    direction,
                    next_node.node_id,
                )
                return None
            logging.debug(
                "[AUTO SWITCH] skip offline adjacent target=%s from %s:%s via %s edge",
                next_node.node_id,
                current_node_id,
                current_display.display_id,
                direction,
            )
            return event

        anchor_event = self._build_edge_anchor_event(
            next_node,
            next_display.display_id,
            direction,
            cross_ratio,
            bounds,
            source_event=event,
        )
        logging.debug(
            "[AUTO SWITCH DEBUG] target-warp %s:%s -> %s:%s dir=%s source=(%s,%s) anchor=(%s,%s)",
            current_node_id,
            current_display.display_id,
            next_node.node_id,
            next_display.display_id,
            direction,
            event.get("x"),
            event.get("y"),
            anchor_event.get("x"),
            anchor_event.get("y"),
        )

        if hasattr(self.router, "prepare_pointer_handoff"):
            self.router.prepare_pointer_handoff(anchor_event)

        if next_node.node_id == self.ctx.self_node.node_id:
            if self.router.get_selected_target() is None:
                return event
            self.clear_target()
            self._record_switch(
                anchor_event,
                now,
                layout.auto_switch.return_guard_ms,
                bounds,
                source_event_ts=event_ts,
            )
            self._begin_settle(
                anchor_event,
                now,
                source_display_id=current_display.display_id,
                dest_display_id=next_display.display_id,
                direction=direction,
                blocked=False,
            )
            self._clear_self_clip()
            self._warp_pointer(anchor_event)
            self._clip_self_to_entry_strip(next_node, next_display.display_id, direction, bounds)
            self._pending_entry_release_display_id = next_display.display_id
            logging.info(
                "[AUTO SWITCH] %s:%s -> self:%s via %s edge",
                current_node_id,
                current_display.display_id,
                next_display.display_id,
                direction,
            )
            return None

        target = self.ctx.get_node(next_node.node_id)
        if target is None:
            return event

        self.request_target(next_node.node_id)
        self._record_switch(
            anchor_event,
            now,
            layout.auto_switch.return_guard_ms,
            bounds,
            source_event_ts=event_ts,
        )
        self._clear_settle()
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

    def _record_switch(
        self,
        anchor_event,
        now: float,
        return_guard_ms: int,
        bounds,
        source_event_ts: float | None = None,
    ):
        self._last_switch_at = now
        self._guard_until = now + max(int(return_guard_ms), 0) / 1000.0
        self._anchor_norm = (
            anchor_event.get("x_norm"),
            anchor_event.get("y_norm"),
        )
        self._mark_reposition_window(source_event_ts)
        if "x" in anchor_event and "y" in anchor_event:
            self._anchor_pixel = (int(anchor_event["x"]), int(anchor_event["y"]))
            return
        if hasattr(bounds, "left") and hasattr(bounds, "top") and hasattr(bounds, "width") and hasattr(bounds, "height"):
            bounds_arg = (bounds.left, bounds.top, bounds.width, bounds.height)
        else:
            bounds_arg = bounds
        self._anchor_pixel = denormalize_position(anchor_event["x_norm"], anchor_event["y_norm"], bounds_arg)

    def _mark_reposition_window(self, event_ts: float | None) -> None:
        if event_ts is None:
            return
        self._drop_moves_until_ts = max(
            self._drop_moves_until_ts,
            event_ts + self.REPOSITION_STALE_MOVE_WINDOW_SEC,
        )

    def _begin_settle(
        self,
        anchor_event: dict,
        now: float,
        *,
        source_display_id: str | None,
        dest_display_id: str | None,
        direction: str | None,
        blocked: bool,
    ) -> None:
        self._settle_until = now + self.REPOSITION_SETTLE_WINDOW_SEC
        self._settle_anchor_event = dict(anchor_event)
        self._settle_source_display_id = source_display_id
        self._settle_dest_display_id = dest_display_id
        self._settle_direction = direction
        self._settle_blocked = blocked
        self._last_settle_retry_at = 0.0

    def _clear_settle(self) -> None:
        self._settle_until = 0.0
        self._settle_anchor_event = None
        self._settle_source_display_id = None
        self._settle_dest_display_id = None
        self._settle_direction = None
        self._settle_blocked = False
        self._last_settle_retry_at = 0.0

    def _process_settle_window(self, node, event: dict, bounds, now: float):
        if self._settle_anchor_event is None or now >= self._settle_until:
            if self._settle_anchor_event is not None:
                self._clear_settle()
            return event
        if event.get("x") is None or event.get("y") is None:
            return event

        current_pos = self._get_actual_self_pointer(node)
        if current_pos is None:
            current_pos = (int(event["x"]), int(event["y"]))
        current_display = self._resolve_actual_self_display(node, int(current_pos[0]), int(current_pos[1]))
        current_display_id = None if current_display is None else current_display.display_id

        if self._settle_blocked:
            if current_display_id != self._settle_source_display_id:
                self._clear_settle()
                return event
            if not self._event_is_still_pressing_dead_edge(
                node,
                current_display_id,
                event,
                bounds,
                current_pos,
            ):
                self._clear_settle()
                return event
            self._retry_settle_anchor(now)
            return None

        if current_display_id != self._settle_dest_display_id:
            self._retry_settle_anchor(now)
            return None

        if self._is_outside_destination_entry_hold(
            node,
            current_display_id,
            bounds,
            current_pos,
        ):
            self._retry_settle_anchor(now)
            return None

        return None

    def _retry_settle_anchor(self, now: float) -> None:
        if self._settle_anchor_event is None:
            return
        if now - self._last_settle_retry_at < self.REPOSITION_RETRY_INTERVAL_SEC:
            return
        self._last_settle_retry_at = now
        logging.debug(
            "[AUTO SWITCH DEBUG] settle retry anchor=(%s,%s) blocked=%s",
            self._settle_anchor_event.get("x"),
            self._settle_anchor_event.get("y"),
            self._settle_blocked,
        )
        self._warp_pointer(self._settle_anchor_event)

    def _event_is_still_pressing_dead_edge(
        self,
        node,
        display_id: str | None,
        event: dict,
        bounds,
        current_pos: tuple[int, int],
    ) -> bool:
        if display_id is None or self._settle_direction is None:
            return False
        left, top, right, bottom = self._display_pixel_rect(node, display_id, bounds)
        actual_x, actual_y = current_pos
        raw_x = int(event["x"])
        raw_y = int(event["y"])
        if self._settle_direction == "left":
            return actual_x <= left + 1 and raw_x <= actual_x and top <= actual_y <= bottom
        if self._settle_direction == "right":
            return actual_x >= right - 1 and raw_x >= actual_x and top <= actual_y <= bottom
        if self._settle_direction == "up":
            return actual_y <= top + 1 and raw_y <= actual_y and left <= actual_x <= right
        if self._settle_direction == "down":
            return actual_y >= bottom - 1 and raw_y >= actual_y and left <= actual_x <= right
        return False

    def _is_outside_destination_entry_hold(
        self,
        node,
        display_id: str | None,
        bounds,
        current_pos: tuple[int, int],
    ) -> bool:
        if display_id is None or self._settle_direction is None:
            return False
        left, top, right, bottom = self._display_pixel_rect(node, display_id, bounds)
        actual_x, actual_y = current_pos
        margin = self.DESTINATION_ENTRY_HOLD_MARGIN_PX
        if self._settle_direction == "right":
            return actual_x > left + margin
        if self._settle_direction == "left":
            return actual_x < right - margin
        if self._settle_direction == "down":
            return actual_y > top + margin
        if self._settle_direction == "up":
            return actual_y < bottom - margin
        return False

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
        actual_pointer = self._get_actual_self_pointer(node)
        if actual_pointer is not None:
            actual_x, actual_y = actual_pointer
            display = self._resolve_actual_self_display(node, actual_x, actual_y)
            if display is not None:
                had_previous_actual = self._last_actual_self_pointer is not None
                left, top, right, bottom = self._display_pixel_rect(node, display.display_id, bounds)
                direction = self._actual_edge_direction(actual_pointer, (left, top, right, bottom))
                self._last_actual_self_pointer = actual_pointer
                if direction is not None:
                    cross_ratio = (
                        _axis_ratio(actual_y, top, bottom)
                        if direction in {"left", "right"}
                        else _axis_ratio(actual_x, left, right)
                    )
                    logging.debug(
                        "[AUTO SWITCH DEBUG] actual-pointer display=%s actual=(%s,%s) dir=%s",
                        display.display_id,
                        actual_x,
                        actual_y,
                        direction,
                    )
                    return display, direction, cross_ratio
                if had_previous_actual:
                    return display, None, None

        display = None
        if event.get("x") is not None and event.get("y") is not None:
            display = self._resolve_actual_self_display(node, int(event["x"]), int(event["y"]))
        if display is None:
            display = resolve_display_for_normalized_point(node, event.get("x_norm"), event.get("y_norm"))
        if display is None or event.get("x") is None or event.get("y") is None:
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
        actual_rect = self._actual_self_display_rect(node, display_id)
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

    def _build_edge_anchor_event(
        self,
        node,
        display_id: str,
        direction: str,
        cross_axis_ratio: float,
        bounds,
        source_event: dict | None = None,
        *,
        blocked: bool = False,
    ):
        left, top, right, bottom = self._display_pixel_rect(node, display_id, bounds)
        ratio = min(max(float(cross_axis_ratio), 0.0), 1.0)
        if direction == "left":
            x = left if blocked else right
            y = top + round(ratio * max(bottom - top, 0))
        elif direction == "right":
            x = right if blocked else left
            y = top + round(ratio * max(bottom - top, 0))
        elif direction == "up":
            x = left + round(ratio * max(right - left, 0))
            y = top if blocked else bottom
        elif direction == "down":
            x = left + round(ratio * max(right - left, 0))
            y = bottom if blocked else top
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

    def _warp_pointer(self, anchor_event: dict):
        if self.pointer_mover is None:
            return
        if "x" in anchor_event and "y" in anchor_event:
            logging.debug(
                "[AUTO SWITCH DEBUG] pointer-move direct=(%s,%s)",
                anchor_event["x"],
                anchor_event["y"],
            )
            self.pointer_mover(int(anchor_event["x"]), int(anchor_event["y"]))
            return
        bounds = self.screen_bounds_provider()
        if hasattr(bounds, "left") and hasattr(bounds, "top") and hasattr(bounds, "width") and hasattr(bounds, "height"):
            bounds_arg = (bounds.left, bounds.top, bounds.width, bounds.height)
        else:
            bounds_arg = bounds
        x, y = denormalize_position(anchor_event["x_norm"], anchor_event["y_norm"], bounds_arg)
        logging.debug("[AUTO SWITCH DEBUG] pointer-move denorm=(%s,%s)", x, y)
        self.pointer_mover(x, y)

    def _actual_self_display_rect(self, node, display_id: str):
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

    def _resolve_actual_self_display(self, node, x: int, y: int):
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
        chosen = None
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

    def _find_actual_logical_neighbor(
        self,
        node,
        current_display_id: str,
        direction: str,
        event: dict,
    ) -> str | None:
        if node.node_id != self.ctx.self_node.node_id:
            return None
        snapshot = self.ctx.get_monitor_inventory(node.node_id)
        if snapshot is None or not snapshot.monitors:
            return None
        current = next(
            (item for item in snapshot.monitors if item.monitor_id == current_display_id),
            None,
        )
        if current is None:
            return None
        ratio_axis = int(event["y"]) if direction in {"left", "right"} else int(event["x"])
        current_left = int(current.bounds.left)
        current_top = int(current.bounds.top)
        current_right_exclusive = current_left + max(int(current.bounds.width), 1)
        current_bottom_exclusive = current_top + max(int(current.bounds.height), 1)
        candidates = []
        for item in snapshot.monitors:
            if item.monitor_id == current_display_id:
                continue
            left = int(item.bounds.left)
            top = int(item.bounds.top)
            right_exclusive = left + max(int(item.bounds.width), 1)
            bottom_exclusive = top + max(int(item.bounds.height), 1)
            if direction == "left":
                if right_exclusive != current_left:
                    continue
                if not (max(top, current_top) <= ratio_axis < min(bottom_exclusive, current_bottom_exclusive)):
                    continue
            elif direction == "right":
                if left != current_right_exclusive:
                    continue
                if not (max(top, current_top) <= ratio_axis < min(bottom_exclusive, current_bottom_exclusive)):
                    continue
            elif direction == "up":
                if bottom_exclusive != current_top:
                    continue
                if not (max(left, current_left) <= ratio_axis < min(right_exclusive, current_right_exclusive)):
                    continue
            elif direction == "down":
                if top != current_bottom_exclusive:
                    continue
                if not (max(left, current_left) <= ratio_axis < min(right_exclusive, current_right_exclusive)):
                    continue
            else:
                raise ValueError(f"unknown direction: {direction}")
            candidates.append(item)
        if not candidates:
            return None
        if direction in {"left", "right"}:
            return min(
                candidates,
                key=lambda item: abs(
                    (
                        int(item.bounds.top)
                        + int(item.bounds.top)
                        + max(int(item.bounds.height), 1)
                        - 1
                    )
                    / 2
                    - ratio_axis
                ),
            ).monitor_id
        return min(
            candidates,
            key=lambda item: abs(
                (
                    int(item.bounds.left)
                    + int(item.bounds.left)
                    + max(int(item.bounds.width), 1)
                    - 1
                )
                / 2
                - ratio_axis
            ),
        ).monitor_id

    def _get_actual_self_pointer(self, node) -> tuple[int, int] | None:
        if node.node_id != self.ctx.self_node.node_id or not callable(self.actual_pointer_provider):
            return None
        try:
            return self.actual_pointer_provider()
        except Exception as exc:
            logging.debug("[AUTO SWITCH DEBUG] actual pointer lookup failed: %s", exc)
            return None

    def _actual_edge_direction(
        self,
        current_pos: tuple[int, int],
        rect: tuple[int, int, int, int],
    ) -> str | None:
        if self._last_actual_self_pointer is None:
            return None
        current_x, current_y = current_pos
        prev_x, prev_y = self._last_actual_self_pointer
        delta_x = current_x - prev_x
        delta_y = current_y - prev_y
        left, top, right, bottom = rect
        directions = []
        if current_x <= left and delta_x < 0:
            directions.append(("left", abs(delta_x)))
        if current_x >= right and delta_x > 0:
            directions.append(("right", abs(delta_x)))
        if current_y <= top and delta_y < 0:
            directions.append(("up", abs(delta_y)))
        if current_y >= bottom and delta_y > 0:
            directions.append(("down", abs(delta_y)))
        if not directions:
            return None
        return max(directions, key=lambda entry: entry[1])[0]

    def _clip_self_to_display(self, node, display_id: str, bounds) -> None:
        if node.node_id != self.ctx.self_node.node_id or self.pointer_clipper is None:
            return
        left, top, right, bottom = self._display_pixel_rect(node, display_id, bounds)
        self.pointer_clipper.clip_to_rect(left, top, right, bottom)

    def _clear_self_clip(self) -> None:
        self._pending_entry_release_display_id = None
        if self.pointer_clipper is not None:
            self.pointer_clipper.clear_clip()

    def _sync_last_actual_self_pointer(self, anchor_event: dict) -> None:
        if callable(self.actual_pointer_provider):
            actual = self.actual_pointer_provider()
            if actual is not None:
                self._last_actual_self_pointer = actual
                return
        if "x" in anchor_event and "y" in anchor_event:
            self._last_actual_self_pointer = (int(anchor_event["x"]), int(anchor_event["y"]))

    def _clip_self_to_entry_strip(self, node, display_id: str, direction: str, bounds) -> None:
        if node.node_id != self.ctx.self_node.node_id or self.pointer_clipper is None:
            return
        left, top, right, bottom = self._display_pixel_rect(node, display_id, bounds)
        thickness = max(int(self.ENTRY_STRIP_THICKNESS_PX), 0)
        if direction == "right":
            clip_left = left
            clip_right = min(left + thickness, right)
            clip_top = top
            clip_bottom = bottom
        elif direction == "left":
            clip_left = max(right - thickness, left)
            clip_right = right
            clip_top = top
            clip_bottom = bottom
        elif direction == "down":
            clip_left = left
            clip_right = right
            clip_top = top
            clip_bottom = min(top + thickness, bottom)
        elif direction == "up":
            clip_left = left
            clip_right = right
            clip_top = max(bottom - thickness, top)
            clip_bottom = bottom
        else:
            raise ValueError(f"unknown direction: {direction}")
        self.pointer_clipper.clip_to_rect(clip_left, clip_top, clip_right, clip_bottom)

    def _release_pending_entry_clip(self, node, bounds) -> bool:
        display_id = self._pending_entry_release_display_id
        if display_id is None:
            return False
        self._pending_entry_release_display_id = None
        self._clip_self_to_display(node, display_id, bounds)
        logging.debug(
            "[AUTO SWITCH DEBUG] released entry strip for %s",
            display_id,
        )
        return True


def _axis_ratio(value: float, start: float, end: float) -> float:
    span = max(float(end) - float(start), 1.0)
    return min(max((float(value) - float(start)) / span, 0.0), 1.0)


def _distance_to_rect(x: int, y: int, left: int, top: int, right: int, bottom: int) -> float:
    dx = 0 if left <= x <= right else min(abs(x - left), abs(x - right))
    dy = 0 if top <= y <= bottom else min(abs(y - top), abs(y - bottom))
    return math.hypot(dx, dy)


def _safe_event_ts(event: dict) -> float | None:
    try:
        value = event.get("ts")
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
