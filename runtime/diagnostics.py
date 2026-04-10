"""실환경 검증을 돕는 런타임 진단 유틸리티."""

import json

from runtime.display import (
    get_dpi_awareness_mode,
    get_primary_screen_bounds,
    get_virtual_screen_bounds,
)
from runtime.windows_interaction import detect_windows_interaction_diagnostics


def build_runtime_diagnostics(
    windows_diag_provider=None,
    dpi_mode_provider=None,
    primary_bounds_provider=None,
    virtual_bounds_provider=None,
):
    """현재 실행 환경의 핵심 진단 정보를 수집한다."""
    windows_diag_provider = windows_diag_provider or detect_windows_interaction_diagnostics
    dpi_mode_provider = dpi_mode_provider or get_dpi_awareness_mode
    primary_bounds_provider = primary_bounds_provider or get_primary_screen_bounds
    virtual_bounds_provider = virtual_bounds_provider or get_virtual_screen_bounds

    privilege = windows_diag_provider()
    primary = primary_bounds_provider()
    virtual = virtual_bounds_provider()

    return {
        "platform": "windows" if privilege.is_windows else "non-windows",
        "is_elevated": privilege.is_elevated,
        "dpi_awareness_mode": dpi_mode_provider(),
        "primary_screen": {
            "left": primary.left,
            "top": primary.top,
            "width": primary.width,
            "height": primary.height,
        },
        "virtual_screen": {
            "left": virtual.left,
            "top": virtual.top,
            "width": virtual.width,
            "height": virtual.height,
        },
        "notes": [
            "administrator apps may require the same elevation level due to Windows UIPI",
            "virtual_screen bounds are the normalization basis for pointer events",
        ],
    }


def format_runtime_diagnostics(diagnostics):
    """진단 정보를 사람이 읽기 쉬운 JSON 문자열로 만든다."""
    return json.dumps(diagnostics, ensure_ascii=False, indent=2)
