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
        self.cursor = (0, 0)
        self.clip_calls = []

    def SetCursorPos(self, x, y):
        self.calls.append((x, y))
        self.cursor = (x, y)
        return 1

    def GetCursorPos(self, point):
        point._obj.x = self.cursor[0]
        point._obj.y = self.cursor[1]
        return 1

    def ClipCursor(self, rect):
        if rect is None:
            self.clip_calls.append(None)
            return 1
        self.clip_calls.append((rect._obj.left, rect._obj.top, rect._obj.right, rect._obj.bottom))
        return 1


def test_local_cursor_controller_moves_cursor_and_records_guard():
    guard = FakeGuard()
    user32 = FakeUser32()
    controller = LocalCursorController(synthetic_guard=guard, user32=user32)

    assert controller.move(100, 200) is True
    assert guard.moves == [(100, 200)]
    assert user32.calls == [(100, 200)]


def test_local_cursor_controller_can_read_current_cursor_position():
    controller = LocalCursorController(user32=FakeUser32())

    controller.move(123, 456)

    assert controller.position() == (123, 456)


def test_local_cursor_controller_can_clip_and_clear():
    user32 = FakeUser32()
    controller = LocalCursorController(user32=user32)

    assert controller.clip_to_rect(-1920, 0, -1, 1079) is True
    assert controller.clear_clip() is True

    assert user32.clip_calls == [(-1920, 0, 0, 1080), None]
