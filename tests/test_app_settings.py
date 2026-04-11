"""Tests for runtime/app_settings.py."""

import pytest

from runtime.app_settings import (
    AppHotkeySettings,
    hotkey_to_matcher_parts,
    load_app_settings,
    normalize_hotkey_string,
    validate_hotkey_settings,
)


def test_load_app_settings_uses_defaults():
    settings = load_app_settings({})

    assert settings.hotkeys.previous_target == "Ctrl+Alt+Q"
    assert settings.hotkeys.next_target == "Ctrl+Alt+E"
    assert settings.hotkeys.toggle_auto_switch == "Ctrl+Alt+Z"
    assert settings.hotkeys.quit_app == "Ctrl+Alt+Esc"


def test_load_app_settings_accepts_legacy_stop_capture_key_as_quit_app():
    settings = load_app_settings({"settings": {"hotkeys": {"stop_capture": "Ctrl+Alt+X"}}})

    assert settings.hotkeys.quit_app == "Ctrl+Alt+X"


def test_normalize_hotkey_string_canonicalizes_common_forms():
    assert normalize_hotkey_string("alt + ctrl + q") == "Ctrl+Alt+Q"
    assert normalize_hotkey_string("ctrl-alt-esc") == "Ctrl+Alt+Esc"
    assert normalize_hotkey_string("ctrl+alt+z") == "Ctrl+Alt+Z"


def test_hotkey_to_matcher_parts_builds_pynput_key_strings():
    modifiers, trigger = hotkey_to_matcher_parts("Ctrl+Alt+Esc")

    assert modifiers == (
        ("Key.ctrl", "Key.ctrl_l", "Key.ctrl_r"),
        ("Key.alt", "Key.alt_l", "Key.alt_r"),
    )
    assert trigger == "Key.esc"


def test_validate_hotkey_settings_rejects_duplicates():
    with pytest.raises(ValueError, match="서로 다른 조합"):
        validate_hotkey_settings(
            AppHotkeySettings(
                previous_target="Ctrl+Alt+Q",
                next_target="Ctrl+Alt+Q",
                toggle_auto_switch="Ctrl+Alt+Z",
                quit_app="Ctrl+Alt+Esc",
            )
        )
