"""Tests for runtime/diagnostics.py."""

import json

from runtime.diagnostics import build_runtime_diagnostics, format_runtime_diagnostics
from runtime.display import ScreenBounds
from runtime.windows_interaction import WindowsInteractionDiagnostics


def test_build_runtime_diagnostics_collects_expected_fields():
    diagnostics = build_runtime_diagnostics(
        windows_diag_provider=lambda: WindowsInteractionDiagnostics(
            is_windows=True,
            is_elevated=False,
        ),
        dpi_mode_provider=lambda: "per-monitor-v2",
        primary_bounds_provider=lambda: ScreenBounds(0, 0, 1920, 1080),
        virtual_bounds_provider=lambda: ScreenBounds(-1280, 0, 3200, 1080),
    )

    assert diagnostics["platform"] == "windows"
    assert diagnostics["is_elevated"] is False
    assert diagnostics["dpi_awareness_mode"] == "per-monitor-v2"
    assert diagnostics["primary_screen"]["width"] == 1920
    assert diagnostics["virtual_screen"]["left"] == -1280
    assert diagnostics["notes"]


def test_format_runtime_diagnostics_returns_json_string():
    text = format_runtime_diagnostics({"platform": "windows", "is_elevated": True})
    data = json.loads(text)
    assert data["platform"] == "windows"
    assert data["is_elevated"] is True
