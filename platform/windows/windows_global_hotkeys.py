"""Windows WM_HOTKEY registration for app-wide global shortcuts."""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes

if not hasattr(wintypes, "LRESULT"):
    wintypes.LRESULT = ctypes.c_ssize_t

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_NOREPEAT = 0x4000


class WindowsGlobalHotkeyManager:
    def __init__(self, bindings, *, user32=None, kernel32=None):
        self._bindings = list(bindings)
        self._user32 = user32 or ctypes.windll.user32
        self._kernel32 = kernel32 or ctypes.windll.kernel32
        self._thread = None
        self._thread_id = None
        self._running = threading.Event()
        self._started = threading.Event()
        self._start_error = None
        self._registered_ids = set()
        self._active_binding_names = set()

    @property
    def active_binding_names(self) -> set[str]:
        return set(self._active_binding_names)

    def start(self):
        if self._thread is not None:
            return
        self._running.set()
        self._thread = threading.Thread(
            target=self._thread_main,
            daemon=True,
            name="windows-global-hotkeys",
        )
        self._thread.start()
        self._started.wait(timeout=2.0)
        if self._start_error is not None:
            raise RuntimeError("failed to start Windows global hotkey manager") from self._start_error

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
            for hotkey_id, binding in enumerate(self._bindings, start=1):
                modifiers = int(binding["modifiers"]) | MOD_NOREPEAT
                vk_code = int(binding["vk"])
                if self._user32.RegisterHotKey(None, hotkey_id, modifiers, vk_code):
                    self._registered_ids.add(hotkey_id)
                    self._active_binding_names.add(str(binding["name"]))
                    continue
                logging.warning(
                    "[HOTKEY] failed to register Windows global hotkey %s",
                    binding["name"],
                )
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
                    raise ctypes.WinError()
                if int(msg.message) == WM_HOTKEY:
                    hotkey_id = int(msg.wParam)
                    if 1 <= hotkey_id <= len(self._bindings):
                        callback = self._bindings[hotkey_id - 1]["callback"]
                        try:
                            callback()
                        except Exception as exc:
                            logging.warning("[HOTKEY] global callback failed: %s", exc)
                    continue
                self._user32.TranslateMessage(ctypes.byref(msg))
                self._user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            logging.warning("[HOTKEY] Windows global hotkey loop failed: %s", exc)
        finally:
            for hotkey_id in list(self._registered_ids):
                try:
                    self._user32.UnregisterHotKey(None, hotkey_id)
                except Exception:
                    pass
            self._registered_ids.clear()
            self._active_binding_names.clear()
