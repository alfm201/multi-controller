"""Windows low-level mouse hook that can capture and optionally suppress local mouse input."""

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


WH_MOUSE_LL = 14
HC_ACTION = 0

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_MOUSEHWHEEL = 0x020E
WM_QUIT = 0x0012

XBUTTON1 = 0x0001
XBUTTON2 = 0x0002
WHEEL_DELTA = 120


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
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


class WindowsLowLevelMouseHook:
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
            name="low-level-mouse-hook",
        )
        self._thread.start()
        self._started.wait(timeout=2.0)
        if self._start_error is not None:
            raise RuntimeError("failed to start low-level mouse hook") from self._start_error
        if self._hook_handle is None:
            raise RuntimeError("low-level mouse hook did not initialize")

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
                WH_MOUSE_LL,
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
            logging.warning("[CAPTURE] low-level mouse hook loop failed: %s", exc)
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
            info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            if self._handle_message(int(w_param), info):
                return 1
        except Exception as exc:
            logging.debug("[CAPTURE] low-level mouse callback failed: %s", exc)
        return self._call_next(n_code, w_param, l_param)

    def _call_next(self, n_code, w_param, l_param):
        return self._user32.CallNextHookEx(self._hook_handle, n_code, w_param, l_param)

    def _handle_message(self, message, info) -> bool:
        x = int(info.pt.x)
        y = int(info.pt.y)

        if message == WM_MOUSEMOVE:
            if self._receiver.should_drop_mouse_move(x, y):
                return False
            handled = bool(self._receiver.on_move(x, y, synthetic_checked=True))
            blocked = self._block("mouse_move", {"x": x, "y": y})
            return handled or blocked

        if message in {WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN, WM_RBUTTONUP, WM_MBUTTONDOWN, WM_MBUTTONUP}:
            button = {
                WM_LBUTTONDOWN: "Button.left",
                WM_LBUTTONUP: "Button.left",
                WM_RBUTTONDOWN: "Button.right",
                WM_RBUTTONUP: "Button.right",
                WM_MBUTTONDOWN: "Button.middle",
                WM_MBUTTONUP: "Button.middle",
            }[message]
            pressed = message in {WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN}
            if self._receiver.should_drop_mouse_button(button, x, y, pressed):
                return False
            handled = bool(self._receiver.on_click(x, y, button, pressed, synthetic_checked=True))
            return handled or self._block(
                "mouse_button",
                {"button": button, "pressed": pressed, "x": x, "y": y},
            )

        if message in {WM_XBUTTONDOWN, WM_XBUTTONUP}:
            xbutton = (int(info.mouseData) >> 16) & 0xFFFF
            button = "Button.x1" if xbutton == XBUTTON1 else "Button.x2"
            pressed = message == WM_XBUTTONDOWN
            if self._receiver.should_drop_mouse_button(button, x, y, pressed):
                return False
            handled = bool(self._receiver.on_click(x, y, button, pressed, synthetic_checked=True))
            return handled or self._block(
                "mouse_button",
                {"button": button, "pressed": pressed, "x": x, "y": y},
            )

        if message in {WM_MOUSEWHEEL, WM_MOUSEHWHEEL}:
            delta = ctypes.c_short((int(info.mouseData) >> 16) & 0xFFFF).value
            dx = int(delta / WHEEL_DELTA) if message == WM_MOUSEHWHEEL else 0
            dy = int(delta / WHEEL_DELTA) if message == WM_MOUSEWHEEL else 0
            if self._receiver.should_drop_mouse_wheel(x, y, dx, dy):
                return False
            handled = bool(self._receiver.on_scroll(x, y, dx, dy, synthetic_checked=True))
            return handled or self._block(
                "mouse_wheel",
                {"x": x, "y": y, "dx": dx, "dy": dy},
            )

        return False

    def _block(self, kind, event) -> bool:
        try:
            return bool(self._should_block(kind, event))
        except Exception as exc:
            logging.debug("[CAPTURE] mouse block predicate failed kind=%s: %s", kind, exc)
            return False
