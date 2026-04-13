"""Controller-side virtual remote pointer for active target mode."""

from __future__ import annotations

import time

from core.events import make_mouse_move_event
from runtime.display import ScreenBounds
from runtime.display import enrich_pointer_event


class ActiveRemotePointer:
    """Track a virtual pointer while self mouse input is locally blocked."""

    def __init__(self, *, pointer_mover=None, now_fn=None):
        self.pointer_mover = pointer_mover
        self._now = now_fn or time.time
        self.reset()

    def reset(self) -> None:
        self._active_node_id: str | None = None
        self._active_display_id: str | None = None
        self._anchor_local: tuple[int, int] | None = None
        self._current_event: dict | None = None

    def begin(
        self,
        *,
        node_id: str,
        display_id: str | None,
        anchor_local: tuple[int, int] | None,
        initial_event: dict | None,
    ) -> None:
        self._active_node_id = node_id
        self._active_display_id = display_id
        self._anchor_local = None if anchor_local is None else (int(anchor_local[0]), int(anchor_local[1]))
        self._current_event = None if initial_event is None else dict(initial_event)

    def is_active_for(self, node_id: str | None) -> bool:
        return bool(node_id and node_id == self._active_node_id)

    def current_event(self) -> dict | None:
        return None if self._current_event is None else dict(self._current_event)

    def current_display_id(self) -> str | None:
        return self._active_display_id

    def ensure_anchor(self, local_event: dict) -> None:
        if self._anchor_local is not None:
            return
        if local_event.get("x") is None or local_event.get("y") is None:
            return
        self._anchor_local = (int(local_event["x"]), int(local_event["y"]))

    def sync_from_remote_event(self, *, node_id: str, display_id: str | None, event: dict | None) -> None:
        if not self.is_active_for(node_id):
            return
        if display_id is not None:
            self._active_display_id = display_id
        if event is not None:
            self._current_event = dict(event)

    def translate_local_move(
        self,
        *,
        node_id: str,
        display_id: str,
        node,
        bounds,
        local_event: dict,
        display_state,
    ) -> dict | None:
        if not self.is_active_for(node_id):
            return None

        self.ensure_anchor(local_event)
        if self._anchor_local is None:
            return None

        current_event = self._current_event
        if current_event is None:
            current_event = display_state.build_display_center_event(node, display_id, bounds)
            self._current_event = dict(current_event)
        self._active_display_id = display_id

        local_x = local_event.get("x")
        local_y = local_event.get("y")
        if local_x is None or local_y is None:
            self.recenter_local_pointer()
            return None

        dx = int(local_x) - self._anchor_local[0]
        dy = int(local_y) - self._anchor_local[1]
        if dx == 0 and dy == 0:
            self.recenter_local_pointer()
            return None

        left, top, right, bottom = display_state.display_pixel_rect(node, display_id, bounds)
        next_x = min(max(int(current_event.get("x", left)) + dx, left), right)
        next_y = min(max(int(current_event.get("y", top)) + dy, top), bottom)
        translated = enrich_pointer_event(
            make_mouse_move_event(next_x, next_y),
            self._coerce_bounds(bounds),
        )
        translated["ts"] = local_event.get("ts", self._now())
        self._current_event = dict(translated)
        self.recenter_local_pointer()
        return translated

    def recenter_local_pointer(self) -> None:
        if self.pointer_mover is None or self._anchor_local is None:
            return
        self.pointer_mover(self._anchor_local[0], self._anchor_local[1])

    @staticmethod
    def _coerce_bounds(bounds):
        if isinstance(bounds, ScreenBounds):
            return bounds
        if hasattr(bounds, "left") and hasattr(bounds, "top") and hasattr(bounds, "width") and hasattr(bounds, "height"):
            return ScreenBounds(int(bounds.left), int(bounds.top), int(bounds.width), int(bounds.height))
        return bounds
