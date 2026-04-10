"""Tests for runtime/local_cursor.py."""

from runtime.local_cursor import LocalCursorController


class FakeGuard:
    def __init__(self):
        self.moves = []

    def record_mouse_move(self, x, y):
        self.moves.append((x, y))


class FakeUser32:
    def __init__(self):
        self.calls = []

    def SetCursorPos(self, x, y):
        self.calls.append((x, y))
        return 1


def test_local_cursor_controller_moves_cursor_and_records_guard():
    guard = FakeGuard()
    user32 = FakeUser32()
    controller = LocalCursorController(synthetic_guard=guard, user32=user32)

    assert controller.move(100, 200) is True
    assert guard.moves == [(100, 200)]
    assert user32.calls == [(100, 200)]
