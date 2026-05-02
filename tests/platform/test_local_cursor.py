"""Tests for platform/windows/local_cursor.py."""

import msp_platform.windows.local_cursor as local_cursor_module
from msp_platform.windows.local_cursor import LocalCursorController


class FakeGuard:
    def __init__(self):
        self.moves = []

    def record_mouse_move(self, x, y, *, tolerance_px=None):
        self.moves.append((x, y, tolerance_px))


class FakeUser32:
    def __init__(self, *, initial_display_count=0):
        self.calls = []
        self.cursor = (0, 0)
        self.clip_calls = []
        self.show_calls = []
        self.display_count = int(initial_display_count)
        self.set_system_cursor_calls = []
        self.created_cursors = []
        self.restore_calls = 0

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

    def GetCursorInfo(self, info_ptr):
        info_ptr._obj.flags = 0x00000001 if self.display_count >= 0 else 0
        return 1

    def ShowCursor(self, show):
        show = bool(show)
        self.show_calls.append(show)
        self.display_count += 1 if show else -1
        return self.display_count

    def CreateCursor(self, _instance, _hot_x, _hot_y, width, height, _and_mask, _xor_mask):
        handle = len(self.created_cursors) + 1
        self.created_cursors.append((width, height))
        return handle

    def SetSystemCursor(self, handle, cursor_id):
        self.set_system_cursor_calls.append((handle, cursor_id))
        return 1

    def SystemParametersInfoW(self, action, _param, _pv_param, _flags):
        self.restore_calls += 1
        return 1


class FakeShowCursorOnlyUser32(FakeUser32):
    CreateCursor = None
    SetSystemCursor = None
    SystemParametersInfoW = None


def test_local_cursor_controller_moves_cursor_and_records_guard():
    guard = FakeGuard()
    user32 = FakeUser32()
    controller = LocalCursorController(synthetic_guard=guard, user32=user32)

    assert controller.move(100, 200) is True
    assert guard.moves == [(100, 200, 1)]
    assert user32.calls == [(100, 200)]


def test_local_cursor_controller_can_read_current_cursor_position():
    controller = LocalCursorController(user32=FakeUser32())

    controller.move(123, 456)

    assert controller.position() == (123, 456)


def test_local_cursor_controller_can_clip_and_clear():
    guard = FakeGuard()
    user32 = FakeUser32()
    user32.cursor = (50, 60)
    controller = LocalCursorController(user32=user32, synthetic_guard=guard)

    assert controller.clip_to_rect(-1920, 0, -1, 1079) is True
    assert controller.clear_clip() is True

    assert user32.clip_calls == [(-1920, 0, 0, 1080), None]
    assert guard.moves == [(50, 60, 1), (50, 60, 1)]


def test_local_cursor_controller_enables_dpi_awareness_before_clip(monkeypatch):
    calls = []

    def _fake_enable_best_effort_dpi_awareness(*, user32=None, shcore=None):
        calls.append(user32)
        return True

    monkeypatch.setattr(
        local_cursor_module,
        "enable_best_effort_dpi_awareness",
        _fake_enable_best_effort_dpi_awareness,
    )
    user32 = FakeUser32()
    controller = LocalCursorController(user32=user32)

    assert controller.clip_to_rect(0, 0, 99, 99) is True
    assert calls[-1] is user32


def test_local_cursor_controller_can_hide_and_show_cursor():
    user32 = FakeUser32()
    controller = LocalCursorController(user32=user32)

    assert controller.hide_cursor() is True
    assert len(user32.set_system_cursor_calls) == len(local_cursor_module.STANDARD_CURSOR_IDS)
    assert controller.show_cursor() is True
    assert user32.restore_calls == 1


def test_hide_cursor_falls_back_to_showcursor_when_system_cursor_api_unavailable():
    user32 = FakeShowCursorOnlyUser32(initial_display_count=3)
    controller = LocalCursorController(user32=user32)

    assert controller.hide_cursor() is True
    assert user32.show_calls != []
    assert user32.display_count < 0


def test_show_cursor_uses_best_effort_visibility_after_restore():
    user32 = FakeUser32(initial_display_count=-3)
    controller = LocalCursorController(user32=user32)
    controller._transparent_cursors_active = True

    assert controller.show_cursor() is True
    assert user32.restore_calls == 1
    assert user32.display_count >= 0


def test_best_effort_hide_cursor_can_converge_from_large_positive_display_count():
    user32 = FakeShowCursorOnlyUser32(initial_display_count=90)
    controller = LocalCursorController(user32=user32)

    assert controller.hide_cursor() is True
    assert user32.display_count < 0


def test_best_effort_show_cursor_can_converge_from_large_negative_display_count():
    user32 = FakeShowCursorOnlyUser32(initial_display_count=-90)
    controller = LocalCursorController(user32=user32)

    assert controller.show_cursor() is True
    assert user32.display_count >= 0
