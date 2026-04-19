"""Tests for platform/windows/synthetic_input.py."""

from platform.windows.synthetic_input import SyntheticInputGuard


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, delta):
        self.value += delta


def test_key_event_is_suppressed_once():
    clock = FakeClock()
    guard = SyntheticInputGuard(now_fn=clock)

    guard.record_key("a", down=True)

    assert guard.should_suppress_key("a", down=True) is True
    assert guard.should_suppress_key("a", down=True) is False


def test_expired_key_event_is_not_suppressed():
    clock = FakeClock()
    guard = SyntheticInputGuard(now_fn=clock)

    guard.record_key("a", down=True)
    clock.advance(guard.KEY_TTL_SEC + 0.01)

    assert guard.should_suppress_key("a", down=True) is False


def test_mouse_move_requires_exact_match():
    clock = FakeClock()
    guard = SyntheticInputGuard(now_fn=clock)

    guard.record_mouse_move(100, 200)

    assert guard.should_suppress_mouse_move(100, 200) is True


def test_mouse_move_does_not_suppress_nearby_real_motion():
    clock = FakeClock()
    guard = SyntheticInputGuard(now_fn=clock)

    guard.record_mouse_move(100, 200)

    assert guard.should_suppress_mouse_move(101, 200) is False


def test_mouse_move_can_suppress_one_pixel_warp_echo_with_explicit_tolerance():
    clock = FakeClock()
    guard = SyntheticInputGuard(now_fn=clock)

    guard.record_mouse_move(100, 200, tolerance_px=1)

    assert guard.should_suppress_mouse_move(101, 200) is True


def test_mouse_move_explicit_tolerance_does_not_swallow_follow_up_real_motion():
    clock = FakeClock()
    guard = SyntheticInputGuard(now_fn=clock)

    guard.record_mouse_move(100, 200, tolerance_px=1)

    assert guard.should_suppress_mouse_move(101, 200) is True
    assert guard.should_suppress_mouse_move(102, 200) is False


def test_mouse_move_history_is_bounded():
    clock = FakeClock()
    guard = SyntheticInputGuard(now_fn=clock)

    for index in range(guard.MAX_MOVE_SAMPLES + 5):
        guard.record_mouse_move(index, index)

    assert len(guard._move_events) == guard.MAX_MOVE_SAMPLES


def test_mouse_button_matches_kind_and_position():
    clock = FakeClock()
    guard = SyntheticInputGuard(now_fn=clock)

    guard.record_mouse_button("Button.left", 400, 500, down=True)

    assert guard.should_suppress_mouse_button("Button.left", 401, 502, True) is True
    assert guard.should_suppress_mouse_button("Button.left", 401, 502, True) is False


def test_mouse_wheel_matches_scroll_vector():
    clock = FakeClock()
    guard = SyntheticInputGuard(now_fn=clock)

    guard.record_mouse_wheel(10, 20, 0, -1)

    assert guard.should_suppress_mouse_wheel(10, 21, 0, -1) is True
    assert guard.should_suppress_mouse_wheel(10, 21, 0, -1) is False
