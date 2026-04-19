from platform.windows.windows_global_hotkeys import WindowsGlobalHotkeyManager


class DummyUser32:
    def __init__(self, fail_ids=None):
        self.fail_ids = set(fail_ids or [])
        self.register_calls = []
        self.unregister_calls = []
        self.messages = []

    def RegisterHotKey(self, _hwnd, hotkey_id, modifiers, vk_code):
        self.register_calls.append((hotkey_id, modifiers, vk_code))
        return hotkey_id not in self.fail_ids

    def UnregisterHotKey(self, _hwnd, hotkey_id):
        self.unregister_calls.append(hotkey_id)
        return True

    def GetMessageW(self, msg_ptr, _hwnd, _min_filter, _max_filter):
        if not self.messages:
            return 0
        next_message = self.messages.pop(0)
        msg = msg_ptr._obj
        msg.message = next_message["message"]
        msg.wParam = next_message.get("wParam", 0)
        msg.lParam = next_message.get("lParam", 0)
        return 1

    def TranslateMessage(self, _msg_ptr):
        return True

    def DispatchMessageW(self, _msg_ptr):
        return 0

    def PostThreadMessageW(self, *_args):
        return True


class DummyKernel32:
    def GetCurrentThreadId(self):
        return 1234


def test_windows_global_hotkey_manager_registers_and_dispatches_callbacks():
    called = []
    user32 = DummyUser32()
    user32.messages = [
        {"message": 0x0312, "wParam": 1},
        {"message": 0x0312, "wParam": 2},
    ]
    manager = WindowsGlobalHotkeyManager(
        [
            {"name": "prev", "modifiers": 0x0003, "vk": ord("Q"), "callback": lambda: called.append("prev")},
            {"name": "next", "modifiers": 0x0003, "vk": ord("E"), "callback": lambda: called.append("next")},
        ],
        user32=user32,
        kernel32=DummyKernel32(),
    )

    manager.start()
    manager.join(timeout=1.0)

    assert called == ["prev", "next"]
    assert user32.register_calls == [
        (1, 0x0003 | 0x4000, ord("Q")),
        (2, 0x0003 | 0x4000, ord("E")),
    ]
    assert user32.unregister_calls == [1, 2]


def test_windows_global_hotkey_manager_skips_failed_registrations():
    user32 = DummyUser32(fail_ids={2})
    manager = WindowsGlobalHotkeyManager(
        [
            {"name": "prev", "modifiers": 0x0003, "vk": ord("Q"), "callback": lambda: None},
            {"name": "next", "modifiers": 0x0003, "vk": ord("E"), "callback": lambda: None},
        ],
        user32=user32,
        kernel32=DummyKernel32(),
    )

    manager.start()
    manager.join(timeout=1.0)

    assert user32.register_calls == [
        (1, 0x0003 | 0x4000, ord("Q")),
        (2, 0x0003 | 0x4000, ord("E")),
    ]
    assert user32.unregister_calls == [1]
