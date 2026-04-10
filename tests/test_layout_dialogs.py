"""Tests for runtime/layout_dialogs.py."""

import pytest

from runtime.layout_dialogs import (
    build_monitor_preset,
    format_monitor_grid_text,
    monitor_grid_from_rows,
    parse_auto_switch_form,
    parse_monitor_grid_text,
    validate_monitor_grids,
)


def test_monitor_grid_text_round_trip():
    rows = [["1", "2", None], ["3", ".", "4"]]
    text = format_monitor_grid_text(rows)
    assert text == "1 2 .\n3 . 4"
    assert parse_monitor_grid_text(text) == [["1", "2", None], ["3", None, "4"]]


def test_parse_auto_switch_form_validates_and_converts_values():
    parsed = parse_auto_switch_form(
        {
            "edge_threshold": "0.03",
            "warp_margin": "0.05",
            "cooldown_ms": "320",
            "return_guard_ms": "410",
            "anchor_dead_zone": "0.09",
        }
    )

    assert parsed == {
        "edge_threshold": 0.03,
        "warp_margin": 0.05,
        "cooldown_ms": 320,
        "return_guard_ms": 410,
        "anchor_dead_zone": 0.09,
    }

    with pytest.raises(ValueError, match="edge_threshold"):
        parse_auto_switch_form(
            {
                "edge_threshold": "0.4",
                "warp_margin": "0.05",
                "cooldown_ms": "320",
                "return_guard_ms": "410",
                "anchor_dead_zone": "0.09",
            }
        )


def test_validate_monitor_grids_rejects_disconnected_rows():
    logical = monitor_grid_from_rows([["1", None, "2"]], min_rows=1, min_cols=3)
    physical = monitor_grid_from_rows([["1", "2"]], min_rows=1, min_cols=2)

    validation = validate_monitor_grids(logical, physical)

    assert validation.is_valid is False
    assert "논리 배치는 끊기지 않아야 합니다." in validation.errors


def test_build_monitor_preset_creates_grid_with_matching_ids():
    preset = build_monitor_preset(3, 2)
    validation = validate_monitor_grids(preset, preset)

    assert validation.is_valid is True
    assert validation.display_ids == ("1", "2", "3", "4", "5", "6")
