"""Shared Win32 API setup for low-level keyboard/mouse hooks."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

if not hasattr(wintypes, "ULONG_PTR"):
    wintypes.ULONG_PTR = ctypes.c_size_t

if not hasattr(wintypes, "LRESULT"):
    wintypes.LRESULT = ctypes.c_ssize_t

if not hasattr(wintypes, "HHOOK"):
    wintypes.HHOOK = wintypes.HANDLE

if not hasattr(wintypes, "HINSTANCE"):
    wintypes.HINSTANCE = wintypes.HANDLE

if not hasattr(wintypes, "HMODULE"):
    wintypes.HMODULE = wintypes.HINSTANCE


def load_user32():
    return ctypes.WinDLL("user32", use_last_error=True)


def load_kernel32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def last_winerror():
    code = ctypes.get_last_error()
    return ctypes.WinError(code or None)


def _set_signature(api, name, argtypes, restype):
    func = getattr(api, name, None)
    if func is None:
        return
    try:
        func.argtypes = argtypes
        func.restype = restype
    except Exception:
        return


def configure_low_level_hook_api(user32, kernel32, hookproc_type):
    _set_signature(
        user32,
        "SetWindowsHookExW",
        [ctypes.c_int, hookproc_type, wintypes.HINSTANCE, wintypes.DWORD],
        wintypes.HHOOK,
    )
    _set_signature(
        user32,
        "UnhookWindowsHookEx",
        [wintypes.HHOOK],
        wintypes.BOOL,
    )
    _set_signature(
        user32,
        "CallNextHookEx",
        [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM],
        wintypes.LRESULT,
    )
    _set_signature(
        user32,
        "GetMessageW",
        [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT],
        ctypes.c_int,
    )
    _set_signature(
        user32,
        "TranslateMessage",
        [ctypes.POINTER(wintypes.MSG)],
        wintypes.BOOL,
    )
    _set_signature(
        user32,
        "DispatchMessageW",
        [ctypes.POINTER(wintypes.MSG)],
        wintypes.LRESULT,
    )
    _set_signature(
        user32,
        "PostThreadMessageW",
        [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM],
        wintypes.BOOL,
    )
    _set_signature(
        kernel32,
        "GetCurrentThreadId",
        [],
        wintypes.DWORD,
    )
    _set_signature(
        kernel32,
        "GetModuleHandleW",
        [wintypes.LPCWSTR],
        wintypes.HMODULE,
    )
