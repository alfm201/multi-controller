"""Target-side input sink with lease-aware authorization."""

import logging
import threading
import time
from collections import defaultdict

from injection.os_injector import LoggingOSInjector, OSInjector
from runtime.display import get_virtual_screen_bounds, resolve_pointer_position


class InputSink:
    def __init__(
        self,
        injector: OSInjector | None = None,
        require_authorization: bool = False,
        screen_size_provider=None,
    ):
        self._injector: OSInjector = injector or LoggingOSInjector()
        self._pressed = defaultdict(set)  # peer_id -> set of entry strings
        self._authorized_controller_id = None
        self._require_authorization = require_authorization
        self._lock = threading.Lock()
        self._screen_size_provider = screen_size_provider or get_virtual_screen_bounds
        self._last_remote_input_at = 0.0
        self._remote_input_grace_sec = 0.25

    def handle(self, peer_id, event):
        if not self._is_authorized(peer_id):
            logging.debug(
                "[SINK DROP     ] from=%s unauthorized holder=%s kind=%s",
                peer_id,
                self._authorized_controller_id,
                event.get("kind"),
            )
            return

        kind = event.get("kind")
        with self._lock:
            self._last_remote_input_at = time.monotonic()
        self._track_pressed(peer_id, kind, event)

        if kind == "key_down":
            key = event.get("key")
            logging.info("[SINK KEY DOWN ] from=%s key=%s", peer_id, key)
            if key is not None:
                self._injector.inject_key(str(key), down=True)

        elif kind == "key_up":
            key = event.get("key")
            logging.info("[SINK KEY UP   ] from=%s key=%s", peer_id, key)
            if key is not None:
                self._injector.inject_key(str(key), down=False)

        elif kind == "mouse_move":
            if event.get("relative"):
                dx = int(event.get("dx") or 0)
                dy = int(event.get("dy") or 0)
                logging.debug("[SINK MOVE     ] from=%s dx=%s dy=%s relative=1", peer_id, dx, dy)
                self._injector.inject_mouse_move_relative(dx, dy)
            else:
                x, y = self._resolve_pointer_position(event)
                logging.debug("[SINK MOVE     ] from=%s x=%s y=%s", peer_id, x, y)
                self._injector.inject_mouse_move(int(x), int(y))

        elif kind == "mouse_button":
            pressed = bool(event.get("pressed"))
            state = "DOWN" if pressed else "UP"
            button = event.get("button")
            position = self._resolve_pointer_position_or_none(event)
            x, y = (None, None) if position is None else position
            logging.info(
                "[SINK CLICK    ] from=%s %s %s x=%s y=%s",
                peer_id,
                button,
                state,
                x,
                y,
            )
            if button is not None:
                self._injector.inject_mouse_button(
                    str(button),
                    None if x is None else int(x),
                    None if y is None else int(y),
                    down=pressed,
                )

        elif kind == "mouse_wheel":
            position = self._resolve_pointer_position_or_none(event)
            x, y = (None, None) if position is None else position
            dx = event.get("dx") or 0
            dy = event.get("dy") or 0
            logging.debug(
                "[SINK WHEEL    ] from=%s x=%s y=%s dx=%s dy=%s",
                peer_id,
                x,
                y,
                dx,
                dy,
            )
            self._injector.inject_mouse_wheel(
                None if x is None else int(x),
                None if y is None else int(y),
                int(dx),
                int(dy),
            )

        else:
            logging.debug("[SINK UNKNOWN  ] from=%s event=%s", peer_id, event)

    def set_authorized_controller(self, controller_id):
        with self._lock:
            previous = self._authorized_controller_id
            if previous == controller_id:
                return
            self._authorized_controller_id = controller_id
            if controller_id is None:
                self._last_remote_input_at = 0.0

            if controller_id is None:
                release_map = dict(self._pressed)
                self._pressed.clear()
            else:
                release_map = {
                    peer_id: set(entries)
                    for peer_id, entries in self._pressed.items()
                    if peer_id != controller_id
                }
                for peer_id in list(release_map):
                    self._pressed.pop(peer_id, None)

        logging.info("[SINK LEASE    ] %s -> %s", previous, controller_id)
        if controller_id is not None:
            prepare_remote = getattr(self._injector, "prepare_remote_control", None)
            if callable(prepare_remote):
                try:
                    prepare_remote()
                except Exception as exc:
                    logging.debug("[SINK LEASE    ] prepare_remote_control failed: %s", exc)
        else:
            end_remote = getattr(self._injector, "end_remote_control", None)
            if callable(end_remote):
                try:
                    end_remote()
                except Exception as exc:
                    logging.debug("[SINK LEASE    ] end_remote_control failed: %s", exc)
        self._release_entries_map(release_map)

    def release_peer(self, peer_id):
        with self._lock:
            entries = list(self._pressed.pop(peer_id, ()))

        if not entries:
            return

        logging.info(
            "[SINK RELEASE  ] peer=%s releasing %s stuck input(s)",
            peer_id,
            len(entries),
        )
        self._release_entries(peer_id, entries)

    def get_authorized_controller(self):
        with self._lock:
            return self._authorized_controller_id

    def remote_input_recent(self, within_sec: float | None = None) -> bool:
        grace = self._remote_input_grace_sec if within_sec is None else max(float(within_sec), 0.0)
        with self._lock:
            last = self._last_remote_input_at
        if last <= 0:
            return False
        return (time.monotonic() - last) <= grace

    def _is_authorized(self, peer_id):
        with self._lock:
            if not self._require_authorization:
                return True
            return peer_id == self._authorized_controller_id

    def _release_entries_map(self, release_map):
        for peer_id, entries in release_map.items():
            if entries:
                logging.info(
                    "[SINK RELEASE  ] peer=%s releasing %s stuck input(s)",
                    peer_id,
                    len(entries),
                )
                self._release_entries(peer_id, entries)

    def _release_entries(self, peer_id, entries):
        for entry in entries:
            if entry.startswith("mouse:"):
                button = entry[len("mouse:"):]
                logging.info(
                    "[SINK RELEASE  ] peer=%s mouse_button button=%s released",
                    peer_id,
                    button,
                )
                self._injector.inject_mouse_button(button, 0, 0, down=False)
            else:
                logging.info("[SINK RELEASE  ] peer=%s key_up key=%s", peer_id, entry)
                self._injector.inject_key(entry, down=False)

    def _track_pressed(self, peer_id, kind, event):
        with self._lock:
            if kind == "key_down":
                self._pressed[peer_id].add(event["key"])
            elif kind == "key_up":
                self._pressed[peer_id].discard(event["key"])
            elif kind == "mouse_button":
                entry = f"mouse:{event['button']}"
                if event.get("pressed"):
                    self._pressed[peer_id].add(entry)
                else:
                    self._pressed[peer_id].discard(entry)

    def _resolve_pointer_position(self, event):
        return resolve_pointer_position(event, self._screen_size_provider())

    def _resolve_pointer_position_or_none(self, event):
        if "x_norm" in event and "y_norm" in event:
            return resolve_pointer_position(event, self._screen_size_provider())
        if event.get("x") is None or event.get("y") is None:
            return None
        return int(event["x"]), int(event["y"])


class NullInputSink:
    def handle(self, peer_id, event):
        pass

    def release_peer(self, peer_id):
        pass
