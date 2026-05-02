"""Windows low-level keyboard hook that can capture and optionally suppress local keyboard input."""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes

from msp_platform.capture.windows_hook_api import (
    configure_low_level_hook_api,
    last_winerror,
    load_kernel32,
    load_user32,
)

if not hasattr(wintypes, "ULONG_PTR"):
    wintypes.ULONG_PTR = ctypes.c_size_t

if not hasattr(wintypes, "LRESULT"):
    wintypes.LRESULT = ctypes.c_ssize_t


WH_KEYBOARD_LL = 13
HC_ACTION = 0

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012

LLKHF_EXTENDED = 0x01

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_ESCAPE = 0x1B
VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SPACE = 0x20
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_CAPITAL = 0x14
VK_INSERT = 0x2D
VK_DELETE = 0x2E
VK_HOME = 0x24
VK_END = 0x23
VK_PRIOR = 0x21
VK_NEXT = 0x22
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.ULONG_PTR),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(
    wintypes.LRESULT,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


SPECIAL_KEYS = {
    VK_ESCAPE: "Key.esc",
    VK_BACK: "Key.backspace",
    VK_TAB: "Key.tab",
    VK_RETURN: "Key.enter",
    VK_SPACE: " ",
    VK_CAPITAL: "Key.caps_lock",
    VK_INSERT: "Key.insert",
    VK_DELETE: "Key.delete",
    VK_HOME: "Key.home",
    VK_END: "Key.end",
    VK_PRIOR: "Key.page_up",
    VK_NEXT: "Key.page_down",
    VK_LEFT: "Key.left",
    VK_UP: "Key.up",
    VK_RIGHT: "Key.right",
    VK_DOWN: "Key.down",
}


def vk_to_key_token(vk_code: int, flags: int = 0) -> str | None:
    vk = int(vk_code)
    if 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    if 0x70 <= vk <= 0x87:
        return f"Key.f{vk - 0x6F}"
    if vk in SPECIAL_KEYS:
        return SPECIAL_KEYS[vk]
    if vk == VK_CONTROL:
        return "Key.ctrl_r" if (flags & LLKHF_EXTENDED) else "Key.ctrl_l"
    if vk == VK_LCONTROL:
        return "Key.ctrl_l"
    if vk == VK_RCONTROL:
        return "Key.ctrl_r"
    if vk == VK_MENU:
        return "Key.alt_r" if (flags & LLKHF_EXTENDED) else "Key.alt_l"
    if vk == VK_LMENU:
        return "Key.alt_l"
    if vk == VK_RMENU:
        return "Key.alt_r"
    if vk == VK_SHIFT:
        return "Key.shift"
    if vk == VK_LSHIFT:
        return "Key.shift_l"
    if vk == VK_RSHIFT:
        return "Key.shift_r"
    if vk == VK_LWIN:
        return "Key.cmd_l"
    if vk == VK_RWIN:
        return "Key.cmd_r"
    return None


class WindowsLowLevelKeyboardHook:
    def __init__(
        self,
        receiver,
        *,
        should_block=None,
        user32=None,
        kernel32=None,
    ):
        self._receiver = receiver
        self._should_block = should_block or (lambda _kind, _event: False)
        self._user32 = user32 or load_user32()
        self._kernel32 = kernel32 or load_kernel32()
        configure_low_level_hook_api(self._user32, self._kernel32, HOOKPROC)
        self._thread = None
        self._thread_id = None
        self._hook_handle = None
        self._hook_proc = None
        self._running = threading.Event()
        self._started = threading.Event()
        self._start_error = None

    def start(self):
        if self._thread is not None:
            return
        self._running.set()
        self._thread = threading.Thread(
            target=self._thread_main,
            daemon=True,
            name="low-level-keyboard-hook",
        )
        self._thread.start()
        self._started.wait(timeout=2.0)
        if self._start_error is not None:
            raise RuntimeError("failed to start low-level keyboard hook") from self._start_error
        if self._hook_handle is None:
            raise RuntimeError("low-level keyboard hook did not initialize")

    def stop(self):
        self._running.clear()
        if self._thread_id:
            try:
                self._user32.PostThreadMessageW(int(self._thread_id), WM_QUIT, 0, 0)
            except Exception:
                pass

    def join(self, timeout=None):
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _thread_main(self):
        try:
            self._thread_id = int(self._kernel32.GetCurrentThreadId())
            self._hook_proc = HOOKPROC(self._hook_callback)
            module_handle = self._kernel32.GetModuleHandleW(None)
            if not module_handle:
                raise last_winerror()
            self._hook_handle = self._user32.SetWindowsHookExW(
                WH_KEYBOARD_LL,
                self._hook_proc,
                module_handle,
                0,
            )
            if not self._hook_handle:
                raise last_winerror()
        except Exception as exc:
            self._start_error = exc
            self._started.set()
            return

        self._started.set()
        msg = wintypes.MSG()
        try:
            while self._running.is_set():
                result = self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    raise last_winerror()
                self._user32.TranslateMessage(ctypes.byref(msg))
                self._user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            logging.warning("[CAPTURE] low-level keyboard hook loop failed: %s", exc)
        finally:
            if self._hook_handle:
                try:
                    self._user32.UnhookWindowsHookEx(self._hook_handle)
                except Exception:
                    pass
            self._hook_handle = None

    def _hook_callback(self, n_code, w_param, l_param):
        if n_code != HC_ACTION:
            return self._call_next(n_code, w_param, l_param)
        try:
            info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if self._handle_message(int(w_param), info):
                return 1
        except Exception as exc:
            logging.debug("[CAPTURE] low-level keyboard callback failed: %s", exc)
        return self._call_next(n_code, w_param, l_param)

    def _call_next(self, n_code, w_param, l_param):
        return self._user32.CallNextHookEx(self._hook_handle, n_code, w_param, l_param)

    def _handle_message(self, message, info) -> bool:
        key_token = vk_to_key_token(info.vkCode, info.flags)
        if not key_token:
            return False

        event = {"key": key_token, "vk": int(info.vkCode)}
        if message in {WM_KEYDOWN, WM_SYSKEYDOWN}:
            receiver_block = bool(self._receiver.on_key_press(key_token))
            return receiver_block or self._block("key_down", event)
        if message in {WM_KEYUP, WM_SYSKEYUP}:
            receiver_block = bool(self._receiver.on_key_release(key_token))
            return receiver_block or self._block("key_up", event)
        return False

    def _block(self, kind, event) -> bool:
        try:
            return bool(self._should_block(kind, event))
        except Exception as exc:
            logging.debug("[CAPTURE] keyboard block predicate failed kind=%s: %s", kind, exc)
            return False
