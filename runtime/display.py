"""화면 좌표를 정규화하거나 복원하는 유틸리티."""

import ctypes


def get_primary_screen_size():
    """현재 시스템의 주 모니터 크기를 반환한다."""
    user32 = ctypes.windll.user32
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass
    width = int(user32.GetSystemMetrics(0))
    height = int(user32.GetSystemMetrics(1))
    return max(width, 1), max(height, 1)


def normalize_position(x, y, width, height):
    """절대 좌표를 0.0~1.0 범위의 정규화 좌표로 바꾼다."""
    max_x = max(int(width) - 1, 1)
    max_y = max(int(height) - 1, 1)
    norm_x = min(max(float(x) / max_x, 0.0), 1.0)
    norm_y = min(max(float(y) / max_y, 0.0), 1.0)
    return norm_x, norm_y


def denormalize_position(norm_x, norm_y, width, height):
    """정규화 좌표를 현재 화면 크기에 맞는 절대 좌표로 복원한다."""
    max_x = max(int(width) - 1, 0)
    max_y = max(int(height) - 1, 0)
    x = round(min(max(float(norm_x), 0.0), 1.0) * max_x)
    y = round(min(max(float(norm_y), 0.0), 1.0) * max_y)
    return x, y


def enrich_pointer_event(event, width, height):
    """마우스 이벤트에 정규화 좌표를 추가한다."""
    x = event.get("x")
    y = event.get("y")
    if x is None or y is None:
        return event

    norm_x, norm_y = normalize_position(x, y, width, height)
    enriched = dict(event)
    enriched["x_norm"] = norm_x
    enriched["y_norm"] = norm_y
    return enriched


def resolve_pointer_position(event, width, height):
    """이벤트에서 사용할 좌표를 정규화 좌표 우선으로 결정한다."""
    if "x_norm" in event and "y_norm" in event:
        return denormalize_position(event["x_norm"], event["y_norm"], width, height)
    return int(event.get("x") or 0), int(event.get("y") or 0)
