from pynput import keyboard, mouse

from core.events import (
    make_key_down_event,
    make_key_up_event,
    make_mouse_move_event,
    make_mouse_button_event,
    make_mouse_wheel_event,
    make_system_event,
)


def _key_to_str(key):
    """Normalize a pynput Key/char into the same wire-string core.events uses."""
    try:
        return key.char
    except AttributeError:
        return str(key)


class InputCapture:
    def __init__(self, event_queue, hotkey_matchers=None):
        self.event_queue = event_queue
        # HotkeyMatcher 리스트. capture 가 press/release 마다 순서대로 호출한다.
        # 하나라도 True 를 돌리면 그 키 이벤트는 큐에 넣지 않는다.
        self.hotkey_matchers = list(hotkey_matchers or [])
        self.keyboard_listener = None
        self.mouse_listener = None
        self.running = False

    def put_event(self, event):
        self.event_queue.put(event)

    def on_key_press(self, key):
        if not self.running:
            return
        key_str = _key_to_str(key)
        consumed = False
        for matcher in self.hotkey_matchers:
            if matcher.on_press(key_str):
                consumed = True
                # 첫 매치 이후에도 나머지 matcher 의 상태는 계속 유지해야
                # 하므로 break 하지 않는다. 다만 소비된 사실은 기록한다.
        if consumed:
            return
        self.put_event(make_key_down_event(key))

    def on_key_release(self, key):
        if not self.running:
            return

        key_str = _key_to_str(key)
        consumed = False
        for matcher in self.hotkey_matchers:
            if matcher.on_release(key_str):
                consumed = True

        if not consumed:
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
