"""Windows title bar styling helpers for Qt windows."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtGui import QColor

from app.meta.identity import APP_ID
from app.ui.gui_style import PALETTE

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36


def apply_app_user_model_id(app_id: str = APP_ID) -> None:
    try:
        shell32 = ctypes.windll.shell32
    except Exception:
        return
    setter = getattr(shell32, "SetCurrentProcessExplicitAppUserModelID", None)
    if setter is None:
        return
    try:
        setter(str(app_id))
    except Exception:
        return


def apply_window_chrome(window) -> None:
    try:
        hwnd = int(window.winId())
    except Exception:
        return
    if not hwnd:
        return
    try:
        dwmapi = ctypes.windll.dwmapi
    except Exception:
        return

    _set_bool_attr(dwmapi, hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, False)
    _set_color_attr(dwmapi, hwnd, DWMWA_CAPTION_COLOR, QColor(PALETTE["window"]))
    _set_color_attr(dwmapi, hwnd, DWMWA_TEXT_COLOR, QColor(PALETTE["text"]))
    _set_color_attr(dwmapi, hwnd, DWMWA_BORDER_COLOR, QColor(PALETTE["border"]))


def _set_bool_attr(dwmapi, hwnd: int, attr: int, value: bool) -> None:
    data = ctypes.c_int(1 if value else 0)
    try:
        dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            wintypes.DWORD(attr),
            ctypes.byref(data),
            ctypes.sizeof(data),
        )
    except Exception:
        return


def _set_color_attr(dwmapi, hwnd: int, attr: int, color: QColor) -> None:
    colorref = ctypes.c_uint32(_to_colorref(color))
    try:
        dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            wintypes.DWORD(attr),
            ctypes.byref(colorref),
            ctypes.sizeof(colorref),
        )
    except Exception:
        return


def _to_colorref(color: QColor) -> int:
    return color.red() | (color.green() << 8) | (color.blue() << 16)
