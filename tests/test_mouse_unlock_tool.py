"""Tests for the manual mouse unlock tool."""

from __future__ import annotations

import io

from scripts import mouse_unlock_tool


def test_notify_user_writes_to_stdout_when_available(monkeypatch):
    output = io.StringIO()
    shown = []

    monkeypatch.setattr(mouse_unlock_tool.sys, "stdout", output)
    monkeypatch.setattr(mouse_unlock_tool, "_show_message_box", lambda *args, **kwargs: shown.append(True))

    mouse_unlock_tool._notify_user("released", error=False)

    assert output.getvalue() == "released\n"
    assert shown == []


def test_notify_user_uses_message_box_when_stream_is_missing(monkeypatch):
    shown = []

    monkeypatch.setattr(mouse_unlock_tool.sys, "stdout", None)
    monkeypatch.setattr(mouse_unlock_tool, "_show_message_box", lambda message, *, error: shown.append((message, error)))

    mouse_unlock_tool._notify_user("released", error=False)

    assert shown == [("released", False)]
