from __future__ import annotations

from capture.windows_keyboard_hook import (
    KBDLLHOOKSTRUCT,
    VK_CONTROL,
    VK_ESCAPE,
    VK_MENU,
    VK_SHIFT,
    WM_KEYDOWN,
    WM_KEYUP,
    WindowsLowLevelKeyboardHook,
    vk_to_key_token,
)
from capture.windows_mouse_hook import (
    MSLLHOOKSTRUCT,
    WM_LBUTTONDOWN,
    WM_MOUSEMOVE,
    WM_MOUSEWHEEL,
    WindowsLowLevelMouseHook,
)


class DummyReceiver:
    def __init__(self):
        self.events = []
        self.drop_move = False
        self.drop_button = False
        self.drop_wheel = False

    def should_drop_mouse_move(self, x, y):
        return self.drop_move

    def should_drop_mouse_button(self, button, x, y, pressed):
        return self.drop_button

    def should_drop_mouse_wheel(self, x, y, dx, dy):
        return self.drop_wheel

    def on_move(self, x, y, *, synthetic_checked=False):
        self.events.append(("move", x, y, synthetic_checked))

    def on_click(self, x, y, button, pressed, *, synthetic_checked=False):
        self.events.append(("click", x, y, button, pressed, synthetic_checked))

    def on_scroll(self, x, y, dx, dy, *, synthetic_checked=False):
        self.events.append(("scroll", x, y, dx, dy, synthetic_checked))

    def on_key_press(self, key):
        self.events.append(("key_down", key))

    def on_key_release(self, key):
        self.events.append(("key_up", key))


class DummyWinApi:
    def CallNextHookEx(self, *_args, **_kwargs):
        return 0


def test_mouse_hook_dispatches_move_and_blocks_when_requested():
    receiver = DummyReceiver()
    hook = WindowsLowLevelMouseHook(
        receiver,
        should_block=lambda kind, event: kind == "mouse_move",
        user32=DummyWinApi(),
        kernel32=DummyWinApi(),
    )
    info = MSLLHOOKSTRUCT()
    info.pt.x = 120
    info.pt.y = 340

    assert hook._handle_message(WM_MOUSEMOVE, info) is True
    assert receiver.events == [("move", 120, 340, True)]


def test_mouse_hook_skips_synthetic_drop_without_blocking():
    receiver = DummyReceiver()
    receiver.drop_move = True
    hook = WindowsLowLevelMouseHook(
        receiver,
        should_block=lambda kind, event: True,
        user32=DummyWinApi(),
        kernel32=DummyWinApi(),
    )
    info = MSLLHOOKSTRUCT()
    info.pt.x = 5
    info.pt.y = 6

    assert hook._handle_message(WM_MOUSEMOVE, info) is False
    assert receiver.events == []


def test_mouse_hook_dispatches_button_and_wheel():
    receiver = DummyReceiver()
    hook = WindowsLowLevelMouseHook(
        receiver,
        should_block=lambda kind, event: False,
        user32=DummyWinApi(),
        kernel32=DummyWinApi(),
    )

    click_info = MSLLHOOKSTRUCT()
    click_info.pt.x = 10
    click_info.pt.y = 20
    assert hook._handle_message(WM_LBUTTONDOWN, click_info) is False

    wheel_info = MSLLHOOKSTRUCT()
    wheel_info.pt.x = 11
    wheel_info.pt.y = 21
    wheel_info.mouseData = 120 << 16
    assert hook._handle_message(WM_MOUSEWHEEL, wheel_info) is False

    assert receiver.events == [
        ("click", 10, 20, "Button.left", True, True),
        ("scroll", 11, 21, 0, 1, True),
    ]


def test_keyboard_vk_mapping_covers_hotkey_modifiers_and_escape():
    assert vk_to_key_token(VK_CONTROL, 0) == "Key.ctrl_l"
    assert vk_to_key_token(VK_MENU, 0) == "Key.alt_l"
    assert vk_to_key_token(VK_SHIFT, 0) == "Key.shift"
    assert vk_to_key_token(VK_ESCAPE, 0) == "Key.esc"
    assert vk_to_key_token(0x51, 0) == "q"


def test_keyboard_hook_dispatches_without_blocking_by_default():
    receiver = DummyReceiver()
    hook = WindowsLowLevelKeyboardHook(
        receiver,
        should_block=lambda kind, event: False,
        user32=DummyWinApi(),
        kernel32=DummyWinApi(),
    )
    info = KBDLLHOOKSTRUCT()
    info.vkCode = 0x51

    assert hook._handle_message(WM_KEYDOWN, info) is False
    assert hook._handle_message(WM_KEYUP, info) is False
    assert receiver.events == [("key_down", "q"), ("key_up", "q")]


def test_keyboard_hook_can_block_when_requested():
    receiver = DummyReceiver()
    hook = WindowsLowLevelKeyboardHook(
        receiver,
        should_block=lambda kind, event: kind == "key_down",
        user32=DummyWinApi(),
        kernel32=DummyWinApi(),
    )
    info = KBDLLHOOKSTRUCT()
    info.vkCode = 0x51

    assert hook._handle_message(WM_KEYDOWN, info) is True
    assert hook._handle_message(WM_KEYUP, info) is False
    assert receiver.events == [("key_down", "q"), ("key_up", "q")]
