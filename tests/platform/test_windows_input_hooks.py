from __future__ import annotations

import ctypes

from msp_platform.capture.windows_keyboard_hook import (
    KBDLLHOOKSTRUCT,
    VK_CONTROL,
    VK_ESCAPE,
    VK_LCONTROL,
    VK_LMENU,
    VK_LSHIFT,
    VK_LWIN,
    VK_MENU,
    VK_RCONTROL,
    VK_RMENU,
    VK_RSHIFT,
    VK_RWIN,
    VK_SHIFT,
    WM_KEYDOWN,
    WM_KEYUP,
    WindowsLowLevelKeyboardHook,
    vk_to_key_token,
)
from msp_platform.capture.windows_hook_api import configure_low_level_hook_api
from msp_platform.capture.windows_mouse_hook import (
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
        self.block_move = False

    def should_drop_mouse_move(self, x, y):
        return self.drop_move

    def should_drop_mouse_button(self, button, x, y, pressed):
        return self.drop_button

    def should_drop_mouse_wheel(self, x, y, dx, dy):
        return self.drop_wheel

    def on_move(self, x, y, *, synthetic_checked=False):
        self.events.append(("move", x, y, synthetic_checked))
        return self.block_move

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


class SignatureFunction:
    def __init__(self):
        self.argtypes = None
        self.restype = None

    def __call__(self, *_args, **_kwargs):
        return 0


class SignatureUser32(DummyWinApi):
    def __init__(self):
        self.SetWindowsHookExW = SignatureFunction()
        self.UnhookWindowsHookEx = SignatureFunction()
        self.CallNextHookEx = SignatureFunction()
        self.GetMessageW = SignatureFunction()
        self.TranslateMessage = SignatureFunction()
        self.DispatchMessageW = SignatureFunction()
        self.PostThreadMessageW = SignatureFunction()


class SignatureKernel32(DummyWinApi):
    def __init__(self):
        self.GetCurrentThreadId = SignatureFunction()
        self.GetModuleHandleW = SignatureFunction()


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


def test_mouse_hook_blocks_when_receiver_requests_local_block():
    receiver = DummyReceiver()
    receiver.block_move = True
    hook = WindowsLowLevelMouseHook(
        receiver,
        should_block=lambda kind, event: False,
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
    assert vk_to_key_token(VK_LCONTROL, 0) == "Key.ctrl_l"
    assert vk_to_key_token(VK_RCONTROL, 0) == "Key.ctrl_r"
    assert vk_to_key_token(VK_MENU, 0) == "Key.alt_l"
    assert vk_to_key_token(VK_LMENU, 0) == "Key.alt_l"
    assert vk_to_key_token(VK_RMENU, 0) == "Key.alt_r"
    assert vk_to_key_token(VK_SHIFT, 0) == "Key.shift"
    assert vk_to_key_token(VK_LSHIFT, 0) == "Key.shift_l"
    assert vk_to_key_token(VK_RSHIFT, 0) == "Key.shift_r"
    assert vk_to_key_token(VK_LWIN, 0) == "Key.cmd_l"
    assert vk_to_key_token(VK_RWIN, 0) == "Key.cmd_r"
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


def test_keyboard_hook_blocks_consumed_hotkey_even_without_block_predicate():
    from msp_platform.capture.hotkey import HotkeyMatcher
    from msp_platform.capture.input_capture import InputCapture

    fired = []
    capture = InputCapture(
        None,
        hotkey_matchers=[
            HotkeyMatcher(
                modifier_groups=[
                    ("Key.ctrl", "Key.ctrl_l", "Key.ctrl_r"),
                    ("Key.alt", "Key.alt_l", "Key.alt_r"),
                ],
                trigger="e",
                callback=lambda: fired.append("next"),
                name="cycle-target-next",
            )
        ],
    )
    capture.running = True
    hook = WindowsLowLevelKeyboardHook(
        capture,
        should_block=lambda kind, event: False,
        user32=DummyWinApi(),
        kernel32=DummyWinApi(),
    )

    ctrl = KBDLLHOOKSTRUCT()
    ctrl.vkCode = VK_CONTROL
    alt = KBDLLHOOKSTRUCT()
    alt.vkCode = VK_MENU
    trigger = KBDLLHOOKSTRUCT()
    trigger.vkCode = 0x45

    assert hook._handle_message(WM_KEYDOWN, ctrl) is False
    assert hook._handle_message(WM_KEYDOWN, alt) is False
    assert hook._handle_message(WM_KEYDOWN, trigger) is True
    assert fired == ["next"]


def test_configure_low_level_hook_api_sets_pointer_sized_signatures():
    user32 = SignatureUser32()
    kernel32 = SignatureKernel32()
    hookproc = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)

    configure_low_level_hook_api(user32, kernel32, hookproc)

    assert user32.SetWindowsHookExW.argtypes is not None
    assert kernel32.GetModuleHandleW.restype is not None
    assert kernel32.GetCurrentThreadId.restype is not None
