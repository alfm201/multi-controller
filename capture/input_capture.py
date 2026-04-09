from pynput import keyboard, mouse

from core.events import (
    make_key_down_event,
    make_key_up_event,
    make_mouse_button_event,
    make_mouse_move_event,
    make_mouse_wheel_event,
    make_system_event,
    now_ts,
)


def _key_to_str(key):
    try:
        return key.char
    except AttributeError:
        return str(key)


class InputCapture:
    def __init__(self, event_queue, hotkey_matchers=None):
        self.event_queue = event_queue
        self.hotkey_matchers = list(hotkey_matchers or [])
        self.keyboard_listener = None
        self.mouse_listener = None
        self.running = False
        self._pending_modifier_presses = []
        self._pending_modifier_keys = set()
        self._suppressed_modifier_releases = set()

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

        if key == keyboard.Key.esc:
            self.put_event(make_system_event("ESC input detected, stopping capture"))
            self.stop()
            return False

    def on_move(self, x, y):
        if not self.running:
            return
        self._flush_pending_modifiers()
        self.put_event(make_mouse_move_event(x, y))

    def on_click(self, x, y, button, pressed):
        if not self.running:
            return
        self._flush_pending_modifiers()
        self.put_event(make_mouse_button_event(x, y, button, pressed))

    def on_scroll(self, x, y, dx, dy):
        if not self.running:
            return
        self._flush_pending_modifiers()
        self.put_event(make_mouse_wheel_event(x, y, dx, dy))

    def start(self):
        if self.running:
            return

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
