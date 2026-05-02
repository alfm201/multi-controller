"""Tests for platform/injection/key_parser.py — wire-string → pynput object.

pynput 이 없는 환경에서는 importorskip 으로 전체가 스킵된다.
"""

import pytest

pytest.importorskip("pynput")

from pynput import keyboard, mouse  # noqa: E402

from msp_platform.injection.key_parser import parse_button, parse_key  # noqa: E402


# --------------------------------------------------------------------------- #
# parse_key
# --------------------------------------------------------------------------- #

def test_parse_key_printable_char_passthrough():
    assert parse_key("a") == "a"


def test_parse_key_printable_uppercase_passthrough():
    assert parse_key("Z") == "Z"


def test_parse_key_digit_passthrough():
    assert parse_key("5") == "5"


def test_parse_key_space_passthrough():
    assert parse_key(" ") == " "


def test_parse_key_esc():
    assert parse_key("Key.esc") is keyboard.Key.esc


def test_parse_key_ctrl_l():
    assert parse_key("Key.ctrl_l") is keyboard.Key.ctrl


def test_parse_key_ctrl_r():
    assert parse_key("Key.ctrl_r") is keyboard.Key.ctrl


def test_parse_key_cmd_l():
    assert parse_key("Key.cmd_l") is keyboard.Key.cmd


def test_parse_key_cmd_r():
    assert parse_key("Key.cmd_r") is keyboard.Key.cmd


def test_parse_key_shift():
    assert parse_key("Key.shift") is keyboard.Key.shift


def test_parse_key_enter():
    assert parse_key("Key.enter") is keyboard.Key.enter


def test_parse_key_unknown_special_returns_none():
    assert parse_key("Key.definitely_not_a_key") is None


def test_parse_key_empty_returns_none():
    assert parse_key("") is None


def test_parse_key_none_returns_none():
    assert parse_key(None) is None


# --------------------------------------------------------------------------- #
# parse_button
# --------------------------------------------------------------------------- #

def test_parse_button_left():
    assert parse_button("Button.left") is mouse.Button.left


def test_parse_button_right():
    assert parse_button("Button.right") is mouse.Button.right


def test_parse_button_middle():
    assert parse_button("Button.middle") is mouse.Button.middle


def test_parse_button_unknown_returns_none():
    assert parse_button("Button.nope") is None


def test_parse_button_empty_returns_none():
    assert parse_button("") is None


def test_parse_button_none_returns_none():
    assert parse_button(None) is None


def test_parse_button_bare_name_also_works():
    """접두사 없는 'left' 도 한 번 더 시도해서 받아준다."""
    assert parse_button("left") is mouse.Button.left
