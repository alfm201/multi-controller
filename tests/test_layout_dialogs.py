"""Tests for runtime/layout_dialogs.py."""

import pytest

from runtime.layout_dialogs import (
    format_monitor_grid_text,
    parse_auto_switch_form,
    parse_monitor_grid_text,
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
