from pynput import keyboard, mouse

from core.events import (
    make_key_down_event,
    make_key_up_event,
    make_mouse_move_event,
    make_mouse_button_event,
    make_mouse_wheel_event,
    make_system_event,
)


class InputCapture:
    def __init__(self, event_queue):
        self.event_queue = event_queue
        self.keyboard_listener = None
        self.mouse_listener = None
        self.running = False

    def put_event(self, event):
        self.event_queue.put(event)

    def on_key_press(self, key):
        if not self.running:
            return
        self.put_event(make_key_down_event(key))

    def on_key_release(self, key):
        if not self.running:
            return

        self.put_event(make_key_up_event(key))

        if key == keyboard.Key.esc:
            self.put_event(make_system_event("ESC 입력 감지, capture 종료"))
            self.stop()
            return False

    def on_move(self, x, y):
        if not self.running:
            return
        self.put_event(make_mouse_move_event(x, y))

    def on_click(self, x, y, button, pressed):
        if not self.running:
            return
        self.put_event(make_mouse_button_event(x, y, button, pressed))

    def on_scroll(self, x, y, dx, dy):
        if not self.running:
            return
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
