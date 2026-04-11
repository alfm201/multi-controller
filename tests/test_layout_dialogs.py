"""Tests for runtime/layout_dialogs.py."""

import pytest

from runtime.layout_dialogs import (
    _cell_from_relative_position,
    append_monitor_grid_col,
    append_monitor_grid_row,
    build_monitor_preset,
    format_monitor_grid_text,
    monitor_grid_from_rows,
    parse_auto_switch_form,
    parse_monitor_grid_text,
    place_display_on_grid,
    remove_last_monitor_grid_col,
    remove_last_monitor_grid_row,
    set_monitor_grid_cell,
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
    assert "논리 배치는 끊기지 않고 이어져야 합니다" in validation.errors


def test_build_monitor_preset_creates_grid_with_matching_ids():
    preset = build_monitor_preset(3, 2)
    validation = validate_monitor_grids(preset, preset)

    assert validation.is_valid is True
    assert validation.display_ids == ("1", "2", "3", "4", "5", "6")


def test_place_display_on_grid_expands_when_dropped_on_edge():
    grid = monitor_grid_from_rows([["1", None]], min_rows=1, min_cols=2)

    updated = place_display_on_grid(grid, "2", 0, 2)

    assert updated.cols == 3
    assert updated.cells[0][2] == "2"


def test_place_display_on_grid_rejects_top_left_expansion():
    grid = monitor_grid_from_rows([["1"]], min_rows=1, min_cols=1)

    with pytest.raises(ValueError, match="위쪽이나 왼쪽"):
        place_display_on_grid(grid, "2", -1, -1)


def test_set_monitor_grid_cell_swaps_with_existing_display():
    grid = monitor_grid_from_rows([["1", "2"]], min_rows=1, min_cols=2)

    updated = set_monitor_grid_cell(grid, 0, 1, "1")

    assert updated.cells[0] == ("2", "1")


def test_append_and_remove_last_row_and_col():
    grid = monitor_grid_from_rows([["1", None]], min_rows=1, min_cols=2)

    grid = append_monitor_grid_row(grid)
    grid = append_monitor_grid_col(grid)

    assert grid.rows == 2
    assert grid.cols == 3

    trimmed = remove_last_monitor_grid_col(grid)
    trimmed = remove_last_monitor_grid_row(trimmed)

    assert trimmed.rows == 1
    assert trimmed.cols == 2


def test_remove_last_row_and_col_require_empty_edges():
    grid = monitor_grid_from_rows([["1"], ["2"]], min_rows=2, min_cols=1)

    with pytest.raises(ValueError, match="마지막 행"):
        remove_last_monitor_grid_row(grid)

    grid = monitor_grid_from_rows([["1", "2"]], min_rows=1, min_cols=2)
    with pytest.raises(ValueError, match="마지막 열"):
        remove_last_monitor_grid_col(grid)


def test_cell_from_relative_position_maps_pointer_to_stable_cell():
    assert _cell_from_relative_position(x=0, y=0, width=300, height=200, rows=2, cols=3) == (0, 0)
    assert _cell_from_relative_position(x=299, y=199, width=300, height=200, rows=2, cols=3) == (1, 2)
    assert _cell_from_relative_position(x=150, y=50, width=300, height=200, rows=2, cols=3) == (0, 1)
