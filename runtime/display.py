"""화면 좌표와 DPI awareness 관련 유틸리티."""

import ctypes
from dataclasses import dataclass


SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

PROCESS_PER_MONITOR_DPI_AWARE = 2
PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


@dataclass(frozen=True)
class ScreenBounds:
    """화면 또는 virtual desktop 좌표 영역."""

    left: int
    top: int
    width: int
    height: int


_DPI_AWARENESS_READY = False
_DPI_AWARENESS_MODE = "unknown"


def enable_best_effort_dpi_awareness(user32=None, shcore=None):
    """가능한 가장 높은 수준의 DPI awareness를 적용한다."""
    global _DPI_AWARENESS_MODE, _DPI_AWARENESS_READY

    if user32 is None:
        try:
            user32 = ctypes.windll.user32
        except Exception:
            return False

    if _DPI_AWARENESS_READY and shcore is None:
        return True

    if shcore is None:
        try:
            shcore = ctypes.windll.shcore
        except Exception:
            shcore = None

    try:
        set_context = getattr(user32, "SetProcessDpiAwarenessContext", None)
        if set_context is not None and set_context(PER_MONITOR_AWARE_V2):
            _DPI_AWARENESS_READY = True
            _DPI_AWARENESS_MODE = "per-monitor-v2"
            return True
    except Exception:
        pass

    try:
        set_awareness = getattr(shcore, "SetProcessDpiAwareness", None)
        if set_awareness is not None:
            result = set_awareness(PROCESS_PER_MONITOR_DPI_AWARE)
            if result in (0, None):
                _DPI_AWARENESS_READY = True
                _DPI_AWARENESS_MODE = "per-monitor"
                return True
    except Exception:
        pass

    try:
        legacy = getattr(user32, "SetProcessDPIAware", None)
        if legacy is not None:
            result = legacy()
            if result in (0, 1, True, None):
                _DPI_AWARENESS_READY = True
                _DPI_AWARENESS_MODE = "system"
                return True
    except Exception:
        pass

    return False


def get_dpi_awareness_mode():
    """현재 프로세스에 적용된 DPI awareness 모드를 반환한다."""
    if not _DPI_AWARENESS_READY:
        enable_best_effort_dpi_awareness()
    return _DPI_AWARENESS_MODE


def _get_user32():
    user32 = ctypes.windll.user32
    enable_best_effort_dpi_awareness(user32=user32)
    return user32


def _coerce_bounds(bounds_or_width, height=None):
    if isinstance(bounds_or_width, ScreenBounds):
        return bounds_or_width
    if height is None:
        if isinstance(bounds_or_width, tuple) and len(bounds_or_width) == 2:
            width, raw_height = bounds_or_width
            return ScreenBounds(0, 0, int(width), int(raw_height))
        if isinstance(bounds_or_width, tuple) and len(bounds_or_width) == 4:
            left, top, width, raw_height = bounds_or_width
            return ScreenBounds(int(left), int(top), int(width), int(raw_height))
        raise ValueError("screen bounds must be ScreenBounds or tuple of 2/4 ints")
    return ScreenBounds(0, 0, int(bounds_or_width), int(height))


def get_primary_screen_bounds():
    """현재 시스템의 주 모니터 영역을 반환한다."""
    user32 = _get_user32()
    width = int(user32.GetSystemMetrics(SM_CXSCREEN))
    height = int(user32.GetSystemMetrics(SM_CYSCREEN))
    return ScreenBounds(0, 0, max(width, 1), max(height, 1))


def get_virtual_screen_bounds():
    """현재 virtual desktop 전체 영역을 반환한다."""
    user32 = _get_user32()
    left = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
    top = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
    width = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
    height = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
    if width <= 0 or height <= 0:
        return get_primary_screen_bounds()
    return ScreenBounds(left, top, width, height)


def get_primary_screen_size():
    """현재 시스템의 주 모니터 크기를 반환한다."""
    bounds = get_primary_screen_bounds()
    return bounds.width, bounds.height


def normalize_position(x, y, width, height=None, left=0, top=0):
    """절대 좌표를 0.0~1.0 범위의 정규화 좌표로 바꾼다."""
    bounds = _coerce_bounds(width, height)
    if height is not None:
        bounds = ScreenBounds(int(left), int(top), bounds.width, bounds.height)
    max_x = max(int(bounds.width) - 1, 1)
    max_y = max(int(bounds.height) - 1, 1)
    rel_x = float(x) - float(bounds.left)
    rel_y = float(y) - float(bounds.top)
    norm_x = min(max(rel_x / max_x, 0.0), 1.0)
    norm_y = min(max(rel_y / max_y, 0.0), 1.0)
    return norm_x, norm_y


def denormalize_position(norm_x, norm_y, width, height=None, left=0, top=0):
    """정규화 좌표를 현재 화면 크기에 맞는 절대 좌표로 복원한다."""
    bounds = _coerce_bounds(width, height)
    if height is not None:
        bounds = ScreenBounds(int(left), int(top), bounds.width, bounds.height)
    max_x = max(int(bounds.width) - 1, 0)
    max_y = max(int(bounds.height) - 1, 0)
    x = int(bounds.left) + round(min(max(float(norm_x), 0.0), 1.0) * max_x)
    y = int(bounds.top) + round(min(max(float(norm_y), 0.0), 1.0) * max_y)
    return x, y


def enrich_pointer_event(event, bounds_or_width, height=None):
    """마우스 이벤트에 정규화 좌표를 추가한다."""
    x = event.get("x")
    y = event.get("y")
    if x is None or y is None:
        return event

    bounds = _coerce_bounds(bounds_or_width, height)
    norm_x, norm_y = normalize_position(x, y, bounds)
    enriched = dict(event)
    enriched["x_norm"] = norm_x
    enriched["y_norm"] = norm_y
    return enriched


def resolve_pointer_position(event, bounds_or_width, height=None):
    """이벤트에서 사용할 좌표를 정규화 좌표 우선으로 결정한다."""
    bounds = _coerce_bounds(bounds_or_width, height)
    if "x_norm" in event and "y_norm" in event:
        return denormalize_position(event["x_norm"], event["y_norm"], bounds)
    return int(event.get("x") or 0), int(event.get("y") or 0)
