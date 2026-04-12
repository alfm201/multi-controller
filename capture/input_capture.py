"""Local keyboard and mouse capture that normalizes input into router events."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

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


def _describe_start_error(exc):
    cause = getattr(exc, "__cause__", None)
    if cause is None:
        return str(exc)
    return f"{exc} ({cause})"


@dataclass(frozen=True)
class MoveProcessingResult:
    event: dict | None
    block_local: bool = False


class InputCapture:
    def __init__(
        self,
        event_queue,
        hotkey_matchers=None,
        synthetic_guard=None,
        screen_bounds_provider=None,
        move_processor=None,
        pointer_state_refresher=None,
        local_activity_callback=None,
        mouse_block_predicate=None,
        keyboard_block_predicate=None,
        mouse_hook_factory=None,
        keyboard_hook_factory=None,
    ):
        self.event_queue = event_queue
        self.hotkey_matchers = list(hotkey_matchers or [])
        self.synthetic_guard = synthetic_guard
        self.keyboard_listener = None
        self.mouse_listener = None
        self.keyboard_hook = None
        self.mouse_hook = None
        self.running = False
        self._pending_modifier_presses = []
        self._pending_modifier_keys = set()
        self._suppressed_modifier_releases = set()
        self._screen_bounds_provider = screen_bounds_provider or get_virtual_screen_bounds
        self.move_processor = move_processor
        self.pointer_state_refresher = pointer_state_refresher
        self.local_activity_callback = local_activity_callback
        self.mouse_block_predicate = mouse_block_predicate
        self.keyboard_block_predicate = keyboard_block_predicate
        self._mouse_hook_factory = mouse_hook_factory
        self._keyboard_hook_factory = keyboard_hook_factory

    def put_event(self, event):
        if self.event_queue is None:
            return
        self.event_queue.put(event)

    def _notify_local_activity(self):
        if callable(self.local_activity_callback):
            self.local_activity_callback()

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

    def should_drop_mouse_move(self, x, y) -> bool:
        if self.synthetic_guard is not None and self.synthetic_guard.should_suppress_mouse_move(x, y):
            logging.debug("[CAPTURE DROP ] synthetic mouse_move x=%s y=%s", x, y)
            return True
        return False

    def should_drop_mouse_button(self, button, x, y, pressed) -> bool:
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
            return True
        return False

    def should_drop_mouse_wheel(self, x, y, dx, dy) -> bool:
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
            return True
        return False

    def on_key_press(self, key):
        if not self.running:
            return False
        key_str = _key_to_str(key)

        if self.synthetic_guard is not None and self.synthetic_guard.should_suppress_key(
            key_str,
            down=True,
        ):
            logging.debug("[CAPTURE DROP ] synthetic key_down key=%s", key_str)
            return False

        consumed = False
        for matcher in self.hotkey_matchers:
            if matcher.on_press(key_str):
                consumed = True

        if consumed:
            self._notify_local_activity()
            self._suppressed_modifier_releases.update(self._pending_modifier_keys)
            self._pending_modifier_presses.clear()
            self._pending_modifier_keys.clear()
            return True

        if self._is_hotkey_modifier(key_str):
            self._notify_local_activity()
            self._buffer_modifier_press(key_str)
            return True

        self._notify_local_activity()
        self._flush_pending_modifiers()
        self.put_event(make_key_down_event(key))
        return True

    def on_key_release(self, key):
        if not self.running:
            return False

        key_str = _key_to_str(key)

        if self.synthetic_guard is not None and self.synthetic_guard.should_suppress_key(
            key_str,
            down=False,
        ):
            logging.debug("[CAPTURE DROP ] synthetic key_up key=%s", key_str)
            return False

        consumed = False
        for matcher in self.hotkey_matchers:
            if matcher.on_release(key_str):
                consumed = True

        if consumed:
            self._notify_local_activity()
            return True

        if key_str in self._suppressed_modifier_releases:
            self._suppressed_modifier_releases.discard(key_str)
            return True

        if key_str in self._pending_modifier_keys:
            self._notify_local_activity()
            self._flush_pending_modifiers()

        self._notify_local_activity()
        self.put_event(make_key_up_event(key))
        return True

    # Pynput listeners stop when callbacks return False. Keep listener wrappers
    # separate from the low-level hook entrypoints so fallback mode keeps running.
    def _listener_on_key_press(self, key):
        self.on_key_press(key)
        return None

    def _listener_on_key_release(self, key):
        self.on_key_release(key)
        return None

    def on_move(self, x, y, *, synthetic_checked=False):
        if not self.running:
            return False
        if not synthetic_checked and self.should_drop_mouse_move(x, y):
            return False
        self._notify_local_activity()
        self._flush_pending_modifiers()
        event = enrich_pointer_event(
            make_mouse_move_event(x, y),
            self._screen_bounds_provider(),
        )
        block_local = False
        if callable(self.move_processor):
            processed = self.move_processor(event)
            if isinstance(processed, MoveProcessingResult):
                event = processed.event
                block_local = bool(processed.block_local)
            else:
                event = processed
            if event is None:
                return block_local
        self.put_event(event)
        return block_local

    def _listener_on_move(self, x, y):
        self.on_move(x, y)
        return None

    def on_click(self, x, y, button, pressed, *, synthetic_checked=False):
        if not self.running:
            return False
        if not synthetic_checked and self.should_drop_mouse_button(button, x, y, pressed):
            return False
        if callable(self.pointer_state_refresher):
            self.pointer_state_refresher()
        self._notify_local_activity()
        self._flush_pending_modifiers()
        self.put_event(
            enrich_pointer_event(
                make_mouse_button_event(x, y, button, pressed),
                self._screen_bounds_provider(),
            )
        )
        return False

    def _listener_on_click(self, x, y, button, pressed):
        self.on_click(x, y, button, pressed)
        return None

    def on_scroll(self, x, y, dx, dy, *, synthetic_checked=False):
        if not self.running:
            return False
        if not synthetic_checked and self.should_drop_mouse_wheel(x, y, dx, dy):
            return False
        self._notify_local_activity()
        self._flush_pending_modifiers()
        self.put_event(
            enrich_pointer_event(
                make_mouse_wheel_event(x, y, dx, dy),
                self._screen_bounds_provider(),
            )
        )
        return False

    def _listener_on_scroll(self, x, y, dx, dy):
        self.on_scroll(x, y, dx, dy)
        return None

    def start(self):
        if self.running:
            return

        self.running = True

        if sys.platform.startswith("win"):
            keyboard_hook_factory = self._keyboard_hook_factory
            if keyboard_hook_factory is None:
                from capture.windows_keyboard_hook import WindowsLowLevelKeyboardHook

                keyboard_hook_factory = WindowsLowLevelKeyboardHook
            try:
                self.keyboard_hook = keyboard_hook_factory(
                    self,
                    should_block=self.keyboard_block_predicate,
                )
                self.keyboard_hook.start()
                logging.info("[CAPTURE] low-level keyboard hook active")
            except Exception as exc:
                logging.warning(
                    "[CAPTURE] low-level keyboard hook unavailable (%s); falling back to pynput keyboard listener",
                    _describe_start_error(exc),
                )
                self.keyboard_hook = None

            mouse_hook_factory = self._mouse_hook_factory
            if mouse_hook_factory is None:
                from capture.windows_mouse_hook import WindowsLowLevelMouseHook

                mouse_hook_factory = WindowsLowLevelMouseHook
            try:
                self.mouse_hook = mouse_hook_factory(
                    self,
                    should_block=self.mouse_block_predicate,
                )
                self.mouse_hook.start()
                logging.info("[CAPTURE] low-level mouse hook active")
            except Exception as exc:
                logging.warning(
                    "[CAPTURE] low-level mouse hook unavailable (%s); falling back to pynput mouse listener",
                    _describe_start_error(exc),
                )
                self.mouse_hook = None

        from pynput import keyboard, mouse

        if self.keyboard_hook is None:
            logging.info("[CAPTURE] using pynput keyboard listener fallback")
            self.keyboard_listener = keyboard.Listener(
                on_press=self._listener_on_key_press,
                on_release=self._listener_on_key_release,
            )
            self.keyboard_listener.start()

        if self.mouse_hook is None:
            logging.info("[CAPTURE] using pynput mouse listener fallback")
            self.mouse_listener = mouse.Listener(
                on_move=self._listener_on_move,
                on_click=self._listener_on_click,
                on_scroll=self._listener_on_scroll,
            )
            self.mouse_listener.start()

    def stop(self):
        if not self.running:
            return

        self.running = False

        if self.keyboard_listener is not None:
            self.keyboard_listener.stop()

        if self.mouse_listener is not None:
            self.mouse_listener.stop()

        if self.keyboard_hook is not None:
            self.keyboard_hook.stop()

        if self.mouse_hook is not None:
            self.mouse_hook.stop()

    def join(self):
        if self.keyboard_listener is not None:
            self.keyboard_listener.join()

        if self.mouse_listener is not None:
            self.mouse_listener.join()

        if self.keyboard_hook is not None:
            self.keyboard_hook.join()

        if self.mouse_hook is not None:
            self.mouse_hook.join()
