"""로컬 키보드/마우스 입력을 캡처해 이벤트 큐로 넘긴다."""

import logging

from core.events import (
    make_key_down_event,
    make_key_up_event,
    make_mouse_button_event,
    make_mouse_move_event,
    make_mouse_wheel_event,
    now_ts,
)
from runtime.display import enrich_pointer_event, get_virtual_screen_bounds


def _key_to_str(key):
    vk = getattr(key, "vk", None)
    if isinstance(vk, int):
        if 0x41 <= vk <= 0x5A:
            return chr(vk).lower()
        if 0x30 <= vk <= 0x39:
            return chr(vk)
        if 0x70 <= vk <= 0x87:
            return f"Key.f{vk - 0x6F}"
    try:
        return key.char
    except AttributeError:
        return str(key)


class InputCapture:
    def __init__(
        self,
        event_queue,
        hotkey_matchers=None,
        synthetic_guard=None,
        screen_bounds_provider=None,
    ):
        self.event_queue = event_queue
        self.hotkey_matchers = list(hotkey_matchers or [])
        self.synthetic_guard = synthetic_guard
        self.keyboard_listener = None
        self.mouse_listener = None
        self.running = False
        self._pending_modifier_presses = []
        self._pending_modifier_keys = set()
        self._suppressed_modifier_releases = set()
        self._screen_bounds_provider = screen_bounds_provider or get_virtual_screen_bounds

    def put_event(self, event):
        self.event_queue.put(event)

    def _is_hotkey_modifier(self, key_str):
        return any(matcher.is_modifier_key(key_str) for matcher in self.hotkey_matchers)

    def _flush_pending_modifiers(self):
        for key_str in self._pending_modifier_presses:
            self.put_event({"kind": "key_down", "ts": now_ts(), "key": key_str})
        self._pending_modifier_presses.clear()
        self._pending_modifier_keys.clear()

    def _buffer_modifier_press(self, key_str):
        if key_str in self._pending_modifier_keys:
            return
        self._pending_modifier_keys.add(key_str)
        self._pending_modifier_presses.append(key_str)

    def on_key_press(self, key):
        if not self.running:
            return
        key_str = _key_to_str(key)

        if self.synthetic_guard is not None and self.synthetic_guard.should_suppress_key(
            key_str,
            down=True,
        ):
            logging.debug("[CAPTURE DROP ] synthetic key_down key=%s", key_str)
            return

        consumed = False
        for matcher in self.hotkey_matchers:
            if matcher.on_press(key_str):
                consumed = True

        if consumed:
            self._suppressed_modifier_releases.update(self._pending_modifier_keys)
            self._pending_modifier_presses.clear()
            self._pending_modifier_keys.clear()
            return

        if self._is_hotkey_modifier(key_str):
            self._buffer_modifier_press(key_str)
            return

        self._flush_pending_modifiers()
        self.put_event(make_key_down_event(key))

    def on_key_release(self, key):
        if not self.running:
            return

        key_str = _key_to_str(key)

        if self.synthetic_guard is not None and self.synthetic_guard.should_suppress_key(
            key_str,
            down=False,
        ):
            logging.debug("[CAPTURE DROP ] synthetic key_up key=%s", key_str)
            return

        consumed = False
        for matcher in self.hotkey_matchers:
            if matcher.on_release(key_str):
                consumed = True

        if consumed:
            return

        if key_str in self._suppressed_modifier_releases:
            self._suppressed_modifier_releases.discard(key_str)
            return

        if key_str in self._pending_modifier_keys:
            self._flush_pending_modifiers()

        self.put_event(make_key_up_event(key))

    def on_move(self, x, y):
        if not self.running:
            return
        if self.synthetic_guard is not None and self.synthetic_guard.should_suppress_mouse_move(x, y):
            logging.debug("[CAPTURE DROP ] synthetic mouse_move x=%s y=%s", x, y)
            return
        self._flush_pending_modifiers()
        self.put_event(
            enrich_pointer_event(
                make_mouse_move_event(x, y),
                self._screen_bounds_provider(),
            )
        )

    def on_click(self, x, y, button, pressed):
        if not self.running:
            return
        if self.synthetic_guard is not None and self.synthetic_guard.should_suppress_mouse_button(
            str(button),
            x,
            y,
            pressed,
        ):
            logging.debug(
                "[CAPTURE DROP ] synthetic mouse_button button=%s pressed=%s x=%s y=%s",
                button,
                pressed,
                x,
                y,
            )
            return
        self._flush_pending_modifiers()
        self.put_event(
            enrich_pointer_event(
                make_mouse_button_event(x, y, button, pressed),
                self._screen_bounds_provider(),
            )
        )

    def on_scroll(self, x, y, dx, dy):
        if not self.running:
            return
        if self.synthetic_guard is not None and self.synthetic_guard.should_suppress_mouse_wheel(
            x,
            y,
            dx,
            dy,
        ):
            logging.debug(
                "[CAPTURE DROP ] synthetic mouse_wheel x=%s y=%s dx=%s dy=%s",
                x,
                y,
                dx,
                dy,
            )
            return
        self._flush_pending_modifiers()
        self.put_event(
            enrich_pointer_event(
                make_mouse_wheel_event(x, y, dx, dy),
                self._screen_bounds_provider(),
            )
        )

    def start(self):
        if self.running:
            return

        from pynput import keyboard, mouse

        self.running = True
        self.keyboard_listener = keyboard.Listener(
            on_press=self.on_key_press,
            on_release=self.on_key_release,
        )
        self.mouse_listener = mouse.Listener(
            on_move=self.on_move,
            on_click=self.on_click,
            on_scroll=self.on_scroll,
        )
        self.keyboard_listener.start()
        self.mouse_listener.start()

    def stop(self):
        if not self.running:
            return

        self.running = False

        if self.keyboard_listener is not None:
            self.keyboard_listener.stop()

        if self.mouse_listener is not None:
            self.mouse_listener.stop()

    def join(self):
        if self.keyboard_listener is not None:
            self.keyboard_listener.join()

        if self.mouse_listener is not None:
            self.mouse_listener.join()
