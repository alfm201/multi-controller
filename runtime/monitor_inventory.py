"""Read-only monitor inventory helpers for automatic logical detection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import ctypes
from ctypes import wintypes
import sys


@dataclass(frozen=True)
class MonitorBounds:
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class MonitorInventoryItem:
    monitor_id: str
    display_name: str
    bounds: MonitorBounds
    is_primary: bool = False
    dpi_scale: float = 1.0
    logical_order: int = 0


@dataclass(frozen=True)
class MonitorInventorySnapshot:
    node_id: str
    monitors: tuple[MonitorInventoryItem, ...]
    captured_at: str | None = None

    def ordered(self) -> tuple[MonitorInventoryItem, ...]:
        return tuple(
            sorted(
                self.monitors,
                key=lambda item: (item.logical_order, item.bounds.top, item.bounds.left, item.monitor_id),
            )
        )

    def monitor_ids(self) -> tuple[str, ...]:
        return tuple(item.monitor_id for item in self.ordered())


def detect_monitor_inventory(node_id: str) -> MonitorInventorySnapshot:
    """Detect the current Windows monitor inventory once."""
    if sys.platform != "win32":
        return MonitorInventorySnapshot(node_id=node_id, monitors=(), captured_at=_captured_now())

    user32 = ctypes.windll.user32
    try:
        shcore = ctypes.windll.shcore
    except Exception:
        shcore = None

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    class DISPLAY_DEVICEW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("DeviceName", wintypes.WCHAR * 32),
            ("DeviceString", wintypes.WCHAR * 128),
            ("StateFlags", wintypes.DWORD),
            ("DeviceID", wintypes.WCHAR * 128),
            ("DeviceKey", wintypes.WCHAR * 128),
        ]

    items: list[MonitorInventoryItem] = []
    monitor_handles: list[tuple[int, str, MonitorBounds, bool]] = []
    monitor_info_getter = user32.GetMonitorInfoW
    enum_display_devices = user32.EnumDisplayDevicesW

    MONITORINFOF_PRIMARY = 1
    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_int,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(RECT),
        wintypes.LPARAM,
    )

    def _display_name(device_name: str) -> str:
        device = DISPLAY_DEVICEW()
        device.cb = ctypes.sizeof(DISPLAY_DEVICEW)
        if not enum_display_devices(device_name, 0, ctypes.byref(device), 0):
            return device_name
        return device.DeviceString or device_name

    def _dpi_scale(handle) -> float:
        if shcore is None:
            return 1.0
        dpi_x = wintypes.UINT()
        dpi_y = wintypes.UINT()
        try:
            result = shcore.GetDpiForMonitor(handle, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
        except Exception:
            return 1.0
        if result != 0 or dpi_x.value <= 0:
            return 1.0
        return round(dpi_x.value / 96.0, 3)

    def _collect(handle, _hdc, _rect, _param):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if not monitor_info_getter(handle, ctypes.byref(info)):
            return 1
        bounds = MonitorBounds(
            left=int(info.rcMonitor.left),
            top=int(info.rcMonitor.top),
            width=max(int(info.rcMonitor.right - info.rcMonitor.left), 1),
            height=max(int(info.rcMonitor.bottom - info.rcMonitor.top), 1),
        )
        device_name = str(info.szDevice)
        is_primary = bool(info.dwFlags & MONITORINFOF_PRIMARY)
        monitor_handles.append((handle, device_name, bounds, is_primary))
        return 1

    user32.EnumDisplayMonitors(0, 0, MONITORENUMPROC(_collect), 0)

    ordered = sorted(
        monitor_handles,
        key=lambda item: (item[2].top, item[2].left, item[1]),
    )
    for logical_order, (handle, device_name, bounds, is_primary) in enumerate(ordered):
        items.append(
            MonitorInventoryItem(
                monitor_id=device_name,
                display_name=_display_name(device_name),
                bounds=bounds,
                is_primary=is_primary,
                dpi_scale=_dpi_scale(handle),
                logical_order=logical_order,
            )
        )

    return MonitorInventorySnapshot(
        node_id=node_id,
        monitors=tuple(items),
        captured_at=_captured_now(),
    )


def snapshot_to_logical_rows(snapshot: MonitorInventorySnapshot) -> list[list[str | None]]:
    """Convert detected monitor bounds into the grid rows used by the monitor editor."""
    ordered = snapshot.ordered()
    if not ordered:
        return []
    x_positions = {item.bounds.left for item in ordered}
    y_positions = {item.bounds.top for item in ordered}
    cols = {position: index for index, position in enumerate(sorted(x_positions))}
    rows = {position: index for index, position in enumerate(sorted(y_positions))}
    matrix = [
        [None for _ in range(max(len(cols), 1))]
        for _ in range(max(len(rows), 1))
    ]
    for item in ordered:
        matrix[rows[item.bounds.top]][cols[item.bounds.left]] = item.monitor_id
    return matrix


def serialize_monitor_inventory_snapshot(snapshot: MonitorInventorySnapshot) -> dict:
    return {
        "node_id": snapshot.node_id,
        "captured_at": snapshot.captured_at,
        "monitors": [
            {
                "monitor_id": item.monitor_id,
                "display_name": item.display_name,
                "bounds": {
                    "left": item.bounds.left,
                    "top": item.bounds.top,
                    "width": item.bounds.width,
                    "height": item.bounds.height,
                },
                "is_primary": item.is_primary,
                "dpi_scale": item.dpi_scale,
                "logical_order": item.logical_order,
            }
            for item in snapshot.ordered()
        ],
    }


def deserialize_monitor_inventory_snapshot(payload: dict) -> MonitorInventorySnapshot:
    items = []
    for raw in payload.get("monitors") or []:
        bounds = raw.get("bounds") or {}
        items.append(
            MonitorInventoryItem(
                monitor_id=str(raw.get("monitor_id") or ""),
                display_name=str(raw.get("display_name") or raw.get("monitor_id") or ""),
                bounds=MonitorBounds(
                    left=int(bounds.get("left", 0)),
                    top=int(bounds.get("top", 0)),
                    width=max(int(bounds.get("width", 1)), 1),
                    height=max(int(bounds.get("height", 1)), 1),
                ),
                is_primary=bool(raw.get("is_primary", False)),
                dpi_scale=float(raw.get("dpi_scale", 1.0)),
                logical_order=int(raw.get("logical_order", 0)),
            )
        )
    return MonitorInventorySnapshot(
        node_id=str(payload.get("node_id") or ""),
        monitors=tuple(items),
        captured_at=payload.get("captured_at"),
    )


def merge_detected_and_physical_override(
    detected: MonitorInventorySnapshot,
    physical_rows: tuple[tuple[str | None, ...], ...] | None,
) -> dict:
    """Prepare a future merge payload without changing runtime behavior yet."""
    return {
        "node_id": detected.node_id,
        "logical_monitors": [
            {
                "monitor_id": item.monitor_id,
                "display_name": item.display_name,
                "bounds": {
                    "left": item.bounds.left,
                    "top": item.bounds.top,
                    "width": item.bounds.width,
                    "height": item.bounds.height,
                },
                "is_primary": item.is_primary,
                "dpi_scale": item.dpi_scale,
                "logical_order": item.logical_order,
            }
            for item in detected.ordered()
        ],
        "physical_override": [] if physical_rows is None else [list(row) for row in physical_rows],
    }


def _captured_now() -> str:
    return datetime.now().strftime("%H:%M:%S")
