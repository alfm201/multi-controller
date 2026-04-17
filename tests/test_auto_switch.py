"""Tests for routing/auto_switch.py."""

from __future__ import annotations

from capture.input_capture import MoveProcessingResult
from routing.auto_switch import AutoTargetSwitcher, detect_edge_direction
from runtime.context import NodeInfo, RuntimeContext
from runtime.layouts import AutoSwitchSettings, LayoutConfig, LayoutNode, replace_layout_monitors
from runtime.monitor_inventory import (
    MonitorBounds,
    MonitorInventoryItem,
    MonitorInventorySnapshot,
)


class FakeRouter:
    def __init__(self, selected_target=None, active_target=None, last_remote_anchor_event=None):
        self._selected_target = selected_target
        self._active_target = active_target
        self.handoffs = []
        self.local_returns = []
        self._last_remote_anchor_event = last_remote_anchor_event

    def get_selected_target(self):
        return self._selected_target

    def get_active_target(self):
        return self._active_target

    def prepare_pointer_handoff(self, anchor_event):
        self.handoffs.append(anchor_event)

    def prepare_local_return(self, anchor_event):
        self.local_returns.append(anchor_event)

    def get_last_remote_anchor_event(self):
        if self._last_remote_anchor_event is None:
            return None
        return dict(self._last_remote_anchor_event)


class FakeBounds:
    def __init__(self, left=0, top=0, width=1920, height=1080):
        self.left = left
        self.top = top
        self.width = width
        self.height = height


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, delta):
        self.value += delta


class FakeClipper:
    def __init__(self):
        self.clip_calls = []
        self.clear_calls = 0
        self._current_clip_rect = None

    def clip_to_rect(self, left, top, right, bottom):
        rect = (left, top, right, bottom)
        self.clip_calls.append(rect)
        self._current_clip_rect = rect
        return True

    def clear_clip(self):
        self.clear_calls += 1
        self._current_clip_rect = None
        return True

    def current_clip_rect(self):
        return self._current_clip_rect


def _ctx(layout):
    nodes = [
        NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000}),
        NodeInfo.from_dict({"name": "B", "ip": "127.0.0.1", "port": 5001}),
        NodeInfo.from_dict({"name": "C", "ip": "127.0.0.1", "port": 5002}),
    ]
    return RuntimeContext(self_node=nodes[0], nodes=nodes, layout=layout)


def _ctx_with_inventory(layout, *snapshots):
    ctx = _ctx(layout)
    for snapshot in snapshots:
        ctx.replace_monitor_inventory(snapshot)
    return ctx


def _layout(enabled=True):
    return LayoutConfig(
        nodes=(
            LayoutNode("A", 0, 0),
            LayoutNode("B", 1, 0),
            LayoutNode("C", 2, 0),
        ),
        auto_switch=AutoSwitchSettings(
            enabled=enabled,
            cooldown_ms=250,
            return_guard_ms=400,
        ),
    )


def test_detect_edge_direction_prefers_nearest_edge():
    assert detect_edge_direction({"x_norm": 0.99, "y_norm": 0.4}, 0.05) == ("right", 0.4)
    assert detect_edge_direction({"x_norm": 0.2, "y_norm": 0.01}, 0.05) == ("up", 0.2)
    assert detect_edge_direction({"x_norm": 0.5, "y_norm": 0.5}, 0.05) == (None, None)


def test_auto_switch_requests_adjacent_target_and_blocks_local_move():
    requests = []
    clears = []
    moves = []
    router = FakeRouter(selected_target=None)
    switcher = AutoTargetSwitcher(
        _ctx(_layout(enabled=True)),
        router,
        request_target=requests.append,
        clear_target=lambda: clears.append("clear"),
        pointer_mover=lambda x, y: moves.append((x, y)),
        screen_bounds_provider=lambda: FakeBounds(),
        now_fn=FakeClock(),
    )

    event = {"kind": "mouse_move", "x": 1919, "y": 600, "x_norm": 0.999, "y_norm": 0.55}
    result = switcher.process(event)

    assert result == MoveProcessingResult(None, True)
    assert requests == ["B"]
    assert clears == []
    assert router.handoffs[-1]["x"] == 0
    assert moves == []


def test_auto_switch_keeps_self_internal_routing_when_remote_switching_disabled():
    display1 = r"\\.\DISPLAY1"
    display2 = r"\\.\DISPLAY2"
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=False, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[[display2, display1]],
        physical_rows=[[display1, display2]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem(display2, display2, MonitorBounds(-1920, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem(display1, display1, MonitorBounds(0, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-11T00:00:00",
    )
    moves = []
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        actual_pointer_provider=lambda: (1919, 396),
        screen_bounds_provider=lambda: FakeBounds(left=-1920, width=3840),
        now_fn=FakeClock(),
    )
    switcher.refresh_self_clip()

    result = switcher.process(
        {"kind": "mouse_move", "x": 1919, "y": 396, "x_norm": 0.999, "y_norm": 0.36}
    )

    assert result == MoveProcessingResult(None, True)
    assert moves == [(-1920, 396)]
    assert switcher._display_state_by_node["A"] == display2


def test_auto_switch_skips_remote_target_switch_when_disabled():
    requests = []
    router = FakeRouter(selected_target=None)
    switcher = AutoTargetSwitcher(
        _ctx(_layout(enabled=False)),
        router,
        request_target=requests.append,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: None,
        screen_bounds_provider=lambda: FakeBounds(),
        now_fn=FakeClock(),
    )

    event = {"kind": "mouse_move", "x": 1919, "y": 600, "x_norm": 0.999, "y_norm": 0.55}
    result = switcher.process(event)

    assert result == MoveProcessingResult(None, True)
    assert requests == []


def test_auto_switch_can_return_to_self_and_clear_target():
    requests = []
    clears = []
    moves = []
    router = FakeRouter(selected_target="B", active_target="B")
    switcher = AutoTargetSwitcher(
        _ctx(_layout(enabled=True)),
        router,
        request_target=requests.append,
        clear_target=lambda: clears.append("clear"),
        pointer_mover=lambda x, y: moves.append((x, y)),
        screen_bounds_provider=lambda: FakeBounds(),
        now_fn=FakeClock(),
    )
    switcher._display_state_by_node["B"] = "1"

    event = {"kind": "mouse_move", "x": 0, "y": 450, "x_norm": 0.0, "y_norm": 0.4}
    result = switcher.process(event)

    assert result == MoveProcessingResult(None, True)
    assert requests == []
    assert clears == ["clear"]
    assert moves == [(0, 450)]
    assert router.local_returns[-1]["x"] == 1919
    assert router.local_returns[-1]["y"] == 450


def test_auto_switch_blocks_return_to_self_when_remote_switching_disabled():
    requests = []
    clears = []
    moves = []
    router = FakeRouter(selected_target="B", active_target="B")
    switcher = AutoTargetSwitcher(
        _ctx(_layout(enabled=False)),
        router,
        request_target=requests.append,
        clear_target=lambda: clears.append("clear"),
        pointer_mover=lambda x, y: moves.append((x, y)),
        screen_bounds_provider=lambda: FakeBounds(),
        now_fn=FakeClock(),
    )
    switcher._display_state_by_node["B"] = "1"

    event = {"kind": "mouse_move", "x": 0, "y": 450, "x_norm": 0.0, "y_norm": 0.4}
    result = switcher.process(event)

    assert result.block_local is True
    assert result.event is not None
    assert result.event["kind"] == "mouse_move"
    assert requests == []
    assert clears == []
    assert moves == [(0, 450)]
    assert router.local_returns == []


def test_auto_switch_self_internal_warp_updates_cached_display():
    display1 = r"\\.\DISPLAY1"
    display2 = r"\\.\DISPLAY2"
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[[display2, display1]],
        physical_rows=[[display1, display2]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem(display2, display2, MonitorBounds(-1920, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem(display1, display1, MonitorBounds(0, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-11T00:00:00",
    )
    moves = []
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        actual_pointer_provider=lambda: (1919, 396),
        screen_bounds_provider=lambda: FakeBounds(left=-1920, width=3840),
        now_fn=FakeClock(),
    )
    switcher.refresh_self_clip()

    result = switcher.process(
        {"kind": "mouse_move", "x": 1919, "y": 396, "x_norm": 0.999, "y_norm": 0.36}
    )

    assert result == MoveProcessingResult(None, True)
    assert moves == [(-1920, 396)]
    assert switcher._display_state_by_node["A"] == display2


def test_auto_switch_blocks_center_crossing_when_physical_neighbor_is_missing():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-11T00:00:00",
    )
    moves = []
    positions = iter(((1919, 540), (1920, 540)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    switcher.refresh_self_clip()
    result = switcher.process(
        {"kind": "mouse_move", "x": 1920, "y": 540, "x_norm": 0.50013, "y_norm": 0.5}
    )

    assert result == MoveProcessingResult(None, True)
    assert moves == [(1919, 540)]
    assert switcher._display_state_by_node["A"] == "1"


def test_auto_switch_keeps_blocked_self_display_during_rebound_after_center_block():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-17T00:00:00",
    )
    moves = []
    clipper = FakeClipper()
    positions = iter(((1919, 540), (1919, 540), (1920, 540)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        pointer_clipper=clipper,
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    switcher.refresh_self_clip()
    first = switcher.process(
        {"kind": "mouse_move", "x": 1919, "y": 540, "x_norm": 1919 / 3839, "y_norm": 540 / 1079}
    )
    second = switcher.process(
        {"kind": "mouse_move", "x": 1919, "y": 540, "x_norm": 1919 / 3839, "y_norm": 540 / 1079}
    )

    assert first == MoveProcessingResult(None, True)
    assert second == MoveProcessingResult(None, True)
    assert moves == [(1919, 540)]
    assert switcher._display_state_by_node["A"] == "1"
    assert clipper.clear_calls == 0


def test_auto_switch_build_frame_prefers_active_hold_display_during_rebound():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-17T00:00:00",
    )
    clock = FakeClock()
    clipper = FakeClipper()
    positions = iter(((1919, 540), (1919, 540), (1919, 540)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda _x, _y: None,
        pointer_clipper=clipper,
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=clock,
    )

    switcher.refresh_self_clip()
    blocked = switcher.process(
        {"kind": "mouse_move", "x": 1919, "y": 540, "x_norm": 1919 / 3839, "y_norm": 540 / 1079}
    )
    frame = switcher._build_frame(
        layout,
        {
            "kind": "mouse_move",
            "x": 1920,
            "y": 540,
            "x_norm": 1920 / 3839,
            "y_norm": 540 / 1079,
            "__actual_pointer_snapshot__": (1920, 540),
            "__self_event_rebound__": True,
        },
        clock(),
    )

    assert blocked == MoveProcessingResult(None, True)
    assert frame is not None
    assert frame.current_display_id == "1"
    assert clipper.clear_calls == 0


def test_auto_switch_build_frame_prefers_active_dead_edge_hold_display_during_rebound():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1"]],
        physical_rows=[["1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),),
        captured_at="2026-04-17T00:00:00",
    )
    clock = FakeClock()
    clipper = FakeClipper()
    positions = iter(((1919, 540), (1919, 540), (1919, 540)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda _x, _y: None,
        pointer_clipper=clipper,
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=1920),
        now_fn=clock,
    )

    switcher.refresh_self_clip()
    blocked = switcher.process(
        {"kind": "mouse_move", "x": 1919, "y": 540, "x_norm": 1.0, "y_norm": 540 / 1079}
    )
    frame = switcher._build_frame(
        layout,
        {
            "kind": "mouse_move",
            "x": 1922,
            "y": 540,
            "x_norm": 1922 / 1919,
            "y_norm": 540 / 1079,
            "__actual_pointer_snapshot__": (1922, 540),
            "__self_event_rebound__": True,
        },
        clock(),
    )

    assert blocked == MoveProcessingResult(None, True)
    assert frame is not None
    assert frame.current_display_id == "1"
    assert clipper.clear_calls == 0


def test_auto_switch_released_hold_allows_actual_display_reconciliation_again():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-17T00:00:00",
    )
    clock = FakeClock()
    clipper = FakeClipper()
    positions = iter(((1919, 540), (1915, 540)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda _x, _y: None,
        pointer_clipper=clipper,
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=clock,
    )

    blocked = switcher.process(
        {"kind": "mouse_move", "x": 1919, "y": 540, "x_norm": 1919 / 3839, "y_norm": 540 / 1079}
    )
    released = switcher.process(
        {"kind": "mouse_move", "x": 1915, "y": 540, "x_norm": 1915 / 3839, "y_norm": 540 / 1079}
    )
    frame = switcher._build_frame(
        layout,
        {
            "kind": "mouse_move",
            "x": 1923,
            "y": 540,
            "x_norm": 1923 / 3839,
            "y_norm": 540 / 1079,
            "__actual_pointer_snapshot__": (1923, 540),
            "__self_event_rebound__": True,
        },
        clock(),
    )

    assert blocked == MoveProcessingResult(None, True)
    assert released["kind"] == "mouse_move"
    assert switcher._executor._edge_hold is None
    assert frame is not None
    assert frame.current_display_id == "2"
    assert clipper.clear_calls == 1


def test_auto_switch_blocks_fast_self_logical_gap_crossing():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-17T00:00:00",
    )
    moves = []
    positions = iter(((1918, 540), (1925, 540)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    first = switcher.process(
        {"kind": "mouse_move", "x": 1918, "y": 540, "x_norm": 1918 / 3839, "y_norm": 540 / 1079}
    )
    second = switcher.process(
        {"kind": "mouse_move", "x": 1925, "y": 540, "x_norm": 1925 / 3839, "y_norm": 540 / 1079}
    )

    assert first["kind"] == "mouse_move"
    assert first["x"] == 1918
    assert first["y"] == 540
    assert second == MoveProcessingResult(None, True)
    assert moves == [(1919, 540)]
    assert switcher._display_state_by_node["A"] == "1"


def test_auto_switch_prefers_crossed_display_when_event_lands_on_neighbor_edge():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-17T00:00:00",
    )
    moves = []
    clipper = FakeClipper()
    positions = iter(((1918, 540), (1920, 540)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        pointer_clipper=clipper,
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    first = switcher.process(
        {"kind": "mouse_move", "x": 1918, "y": 540, "x_norm": 1918 / 3839, "y_norm": 540 / 1079}
    )
    second = switcher.process(
        {"kind": "mouse_move", "x": 1920, "y": 540, "x_norm": 1920 / 3839, "y_norm": 540 / 1079}
    )

    assert first["kind"] == "mouse_move"
    assert first["x"] == 1918
    assert first["y"] == 540
    assert second == MoveProcessingResult(None, True)
    assert moves == []
    assert switcher._display_state_by_node["A"] == "1"
    assert switcher._executor._edge_hold is not None
    assert switcher._executor._edge_hold.display_id == "1"
    assert switcher._executor._edge_hold.direction == "right"
    assert clipper.clip_calls == [(0, 0, 1919, 1079)]


def test_auto_switch_drops_stale_rebound_after_self_logical_gap_block():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-17T00:00:00",
    )
    moves = []
    consumed_positions = []
    clipper = FakeClipper()
    positions = iter(((1918, 540), (1920, 540), (1925, 540)))

    def actual_pointer():
        pos = next(positions)
        consumed_positions.append(pos)
        return pos

    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        pointer_clipper=clipper,
        actual_pointer_provider=actual_pointer,
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    first = switcher.process(
        {"kind": "mouse_move", "x": 1918, "y": 540, "x_norm": 1918 / 3839, "y_norm": 540 / 1079, "ts": 1.0}
    )
    blocked = switcher.process(
        {"kind": "mouse_move", "x": 1920, "y": 540, "x_norm": 1920 / 3839, "y_norm": 540 / 1079, "ts": 1.0}
    )
    stale = switcher.process(
        {"kind": "mouse_move", "x": 1925, "y": 540, "x_norm": 1925 / 3839, "y_norm": 540 / 1079, "ts": 1.01}
    )

    assert first["kind"] == "mouse_move"
    assert blocked == MoveProcessingResult(None, True)
    assert stale == MoveProcessingResult(None, True)
    assert moves == [(1919, 540)]
    assert consumed_positions == [(1918, 540), (1920, 540), (1925, 540)]
    assert switcher._display_state_by_node["A"] == "1"
    assert switcher._executor._edge_hold is not None
    assert clipper.clip_calls == [(0, 0, 1919, 1079), (0, 0, 1919, 1079)]


def test_auto_switch_blocks_fast_self_dead_edge_crossing():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1"]],
        physical_rows=[["1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),),
        captured_at="2026-04-17T00:00:00",
    )
    moves = []
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        screen_bounds_provider=lambda: FakeBounds(width=1920),
        now_fn=FakeClock(),
    )

    first = switcher.process(
        {"kind": "mouse_move", "x": 1918, "y": 540, "x_norm": 1918 / 1919, "y_norm": 540 / 1079}
    )
    second = switcher.process(
        {"kind": "mouse_move", "x": 1925, "y": 540, "x_norm": 1925 / 1919, "y_norm": 540 / 1079}
    )

    assert first["kind"] == "mouse_move"
    assert first["x"] == 1918
    assert first["y"] == 540
    assert second == MoveProcessingResult(None, True)
    assert moves == [(1919, 540)]
    assert switcher._display_state_by_node["A"] == "1"


def test_auto_switch_fast_target_switch_crossing_still_switches_target():
    requests = []
    router = FakeRouter(selected_target=None)
    switcher = AutoTargetSwitcher(
        _ctx(_layout(enabled=True)),
        router,
        request_target=requests.append,
        clear_target=lambda: None,
        pointer_mover=lambda _x, _y: None,
        screen_bounds_provider=lambda: FakeBounds(),
        now_fn=FakeClock(),
    )

    first = switcher.process(
        {"kind": "mouse_move", "x": 1918, "y": 540, "x_norm": 1918 / 1919, "y_norm": 540 / 1079}
    )
    second = switcher.process(
        {"kind": "mouse_move", "x": 1925, "y": 540, "x_norm": 1925 / 1919, "y_norm": 540 / 1079}
    )

    assert first["kind"] == "mouse_move"
    assert first["x"] == 1918
    assert first["y"] == 540
    assert second == MoveProcessingResult(None, True)
    assert requests == ["B"]
    assert router.handoffs[-1]["x"] == 0
    assert router.handoffs[-1]["y"] == 540


def test_auto_switch_fast_self_warp_uses_crossed_display_context():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["1", "2"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-17T00:00:00",
    )
    moves = []
    positions = iter(((1918, 540), (1925, 540)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    first = switcher.process(
        {"kind": "mouse_move", "x": 1918, "y": 540, "x_norm": 1918 / 3839, "y_norm": 540 / 1079}
    )
    second = switcher.process(
        {"kind": "mouse_move", "x": 1925, "y": 540, "x_norm": 1925 / 3839, "y_norm": 540 / 1079}
    )

    assert first["kind"] == "mouse_move"
    assert first["x"] == 1918
    assert first["y"] == 540
    assert second == MoveProcessingResult(None, True)
    assert moves == [(1920, 540)]
    assert switcher._display_state_by_node["A"] == "2"


def test_auto_switch_self_warp_clears_stale_local_clip_before_move():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["1", "2"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-18T00:00:00",
    )
    moves = []
    clipper = FakeClipper()
    clipper.clip_to_rect(0, 0, 1919, 1079)
    positions = iter(((1918, 540), (1925, 540)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        pointer_clipper=clipper,
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    switcher.process(
        {"kind": "mouse_move", "x": 1918, "y": 540, "x_norm": 1918 / 3839, "y_norm": 540 / 1079}
    )
    result = switcher.process(
        {"kind": "mouse_move", "x": 1925, "y": 540, "x_norm": 1925 / 3839, "y_norm": 540 / 1079}
    )

    assert result == MoveProcessingResult(None, True)
    assert moves == [(1920, 540)]
    assert clipper.clear_calls == 1
    assert clipper.current_clip_rect() is None


def test_auto_switch_outer_edge_warps_when_physical_neighbor_exists():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-11T00:00:00",
    )
    moves = []
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        actual_pointer_provider=lambda: (0, 540),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    switcher.refresh_self_clip()
    result = switcher.process(
        {"kind": "mouse_move", "x": 0, "y": 540, "x_norm": 0.0, "y_norm": 0.5}
    )

    assert result == MoveProcessingResult(None, True)
    assert moves == [(3839, 540)]
    assert switcher._display_state_by_node["A"] == "2"


def test_auto_switch_corrects_stale_self_display_before_outer_edge_processing():
    display1 = r"\\.\DISPLAY1"
    display2 = r"\\.\DISPLAY2"
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[[display2, display1]],
        physical_rows=[[display2, display1]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem(display2, display2, MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem(display1, display1, MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-15T00:00:00",
    )
    moves = []
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        actual_pointer_provider=lambda: (0, 540),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )
    switcher._display_state_by_node["A"] = display1

    result = switcher.process(
        {"kind": "mouse_move", "x": 0, "y": 540, "x_norm": 0.0, "y_norm": 0.5}
    )

    assert result == MoveProcessingResult(None, True)
    assert moves == [(0, 540)]
    assert switcher._display_state_by_node["A"] == display2


def test_auto_switch_routes_self_edge_using_actual_pointer_snapshot():
    display1 = r"\\.\DISPLAY1"
    display2 = r"\\.\DISPLAY2"
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[[display2, display1]],
        physical_rows=[[display2, display1]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem(display2, display2, MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem(display1, display1, MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-15T00:00:00",
    )
    moves = []
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        actual_pointer_provider=lambda: (0, 540),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )
    switcher._display_state_by_node["A"] = display1

    result = switcher.process(
        {"kind": "mouse_move", "x": 1920, "y": 540, "x_norm": 0.50013, "y_norm": 0.5}
    )

    assert result == MoveProcessingResult(None, True)
    assert moves == [(0, 540)]
    assert switcher._display_state_by_node["A"] == display2


def test_auto_switch_releases_self_block_hold_from_actual_inward_move():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1"]],
        physical_rows=[["1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(-1920, 0, 1920, 1080), logical_order=0),
        ),
        captured_at="2026-04-15T00:00:00",
    )
    moves = []
    clipper = FakeClipper()
    positions = iter(((-258, 0), (-258, 0), (-258, 5)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        pointer_clipper=clipper,
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(left=-1920, width=1920),
        now_fn=FakeClock(),
    )
    switcher.refresh_self_clip()

    blocked = switcher.process(
        {"kind": "mouse_move", "x": -258, "y": 0, "x_norm": 0.865, "y_norm": 0.0}
    )
    inward = {"kind": "mouse_move", "x": -258, "y": 5, "x_norm": 0.865, "y_norm": 5 / 1079, "ts": 1.0}
    released = switcher.process(inward)

    assert blocked == MoveProcessingResult(None, True)
    assert released["kind"] == "mouse_move"
    assert released["x"] == -258
    assert released["y"] == 5
    assert clipper.clear_calls == 1
    assert moves == []


def test_auto_switch_keeps_self_logical_gap_hold_on_repeated_outward_press():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-17T00:00:00",
    )
    moves = []
    clipper = FakeClipper()
    positions = iter(((1919, 540), (1919, 540), (1919, 540)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        pointer_clipper=clipper,
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    first = switcher.process(
        {"kind": "mouse_move", "x": 1920, "y": 540, "x_norm": 1920 / 3839, "y_norm": 540 / 1079}
    )
    second_event = {"kind": "mouse_move", "x": 1927, "y": 540, "x_norm": 1927 / 3839, "y_norm": 540 / 1079}
    second = switcher.process(second_event)

    assert first == MoveProcessingResult(None, True)
    assert second["kind"] == "mouse_move"
    assert second["x"] == 1919
    assert second["y"] == 540
    assert moves == []
    assert clipper.clear_calls == 0


def test_auto_switch_blocks_first_move_after_focus_risk_when_local_clip_was_lost():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-17T00:00:00",
    )
    clipper = FakeClipper()
    moves = []
    positions = iter(((1919, 540), (1919, 540), (1927, 540)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        pointer_clipper=clipper,
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    blocked = switcher.process(
        {"kind": "mouse_move", "x": 1920, "y": 540, "x_norm": 1920 / 3839, "y_norm": 540 / 1079}
    )
    switcher.note_local_hold_risk()
    clipper._current_clip_rect = None
    guarded = switcher.process(
        {"kind": "mouse_move", "x": 1927, "y": 540, "x_norm": 1927 / 3839, "y_norm": 540 / 1079, "ts": 1.0}
    )

    assert blocked == MoveProcessingResult(None, True)
    assert guarded["kind"] == "mouse_move"
    assert guarded["x"] == 1919
    assert guarded["y"] == 540
    assert switcher._executor._edge_hold is not None
    assert switcher._executor._edge_hold.display_id == "1"
    assert switcher._executor._edge_hold.direction == "right"
    assert switcher._executor._edge_hold.state == "latched"
    assert moves == []
    assert clipper.clip_calls[-1] == (0, 0, 1919, 1079)


def test_auto_switch_sync_self_pointer_state_does_not_override_local_hold_display():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-17T00:00:00",
    )
    clipper = FakeClipper()
    positions = iter(((1919, 540), (1920, 540)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: None,
        pointer_clipper=clipper,
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    blocked = switcher.process(
        {"kind": "mouse_move", "x": 1920, "y": 540, "x_norm": 1920 / 3839, "y_norm": 540 / 1079}
    )
    switcher.sync_self_pointer_state()

    assert blocked == MoveProcessingResult(None, True)
    assert switcher._executor._edge_hold is not None
    assert switcher._executor._edge_hold.display_id == "1"
    assert switcher._display_state_by_node["A"] == "1"
    assert switcher._executor._edge_hold.state == "latched"


def test_auto_switch_refresh_self_clip_does_not_mark_active_hold_guarded():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-17T00:00:00",
    )
    clipper = FakeClipper()
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: None,
        pointer_clipper=clipper,
        actual_pointer_provider=lambda: (1919, 540),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    blocked = switcher.process(
        {"kind": "mouse_move", "x": 1920, "y": 540, "x_norm": 1920 / 3839, "y_norm": 540 / 1079}
    )
    switcher.refresh_self_clip()

    assert blocked == MoveProcessingResult(None, True)
    assert switcher._executor._edge_hold is not None
    assert switcher._executor._edge_hold.state == "latched"


def test_auto_switch_note_local_hold_risk_is_ignored_while_remote_target_is_active():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-17T00:00:00",
    )
    router = FakeRouter(selected_target=None, active_target=None)
    clipper = FakeClipper()
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        router,
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: None,
        pointer_clipper=clipper,
        actual_pointer_provider=lambda: (1919, 540),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    blocked = switcher.process(
        {"kind": "mouse_move", "x": 1920, "y": 540, "x_norm": 1920 / 3839, "y_norm": 540 / 1079}
    )
    router._active_target = "B"
    switcher.note_local_hold_risk()

    assert blocked == MoveProcessingResult(None, True)
    assert switcher._executor._edge_hold is not None
    assert switcher._executor._edge_hold.state == "latched"


def test_auto_switch_repairs_rebound_leak_on_display2_right_dead_edge():
    display1 = r"\\.\DISPLAY1"
    display2 = r"\\.\DISPLAY2"
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[[display2, display1]],
        physical_rows=[[display1, display2]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem(display1, display1, MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem(display2, display2, MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-17T00:00:00",
    )
    moves = []
    clipper = FakeClipper()
    positions = iter(((3839, 540), (3844, 540)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        pointer_clipper=clipper,
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )
    switcher._display_state_by_node["A"] = display2

    blocked = switcher.process(
        {"kind": "mouse_move", "x": 3839, "y": 540, "x_norm": 1.0, "y_norm": 0.5}
    )
    repaired = switcher.process(
        {"kind": "mouse_move", "x": 3844, "y": 540, "x_norm": 3844 / 3839, "y_norm": 0.5, "ts": 1.0}
    )

    assert blocked == MoveProcessingResult(None, True)
    assert repaired == MoveProcessingResult(None, True)
    assert moves == [(3839, 540)]
    assert switcher._display_state_by_node["A"] == display2
    assert switcher._executor._edge_hold is not None
    assert switcher._executor._edge_hold.display_id == display2
    assert switcher._executor._edge_hold.direction == "right"


def test_auto_switch_repairs_self_logical_gap_leak_during_block_admission():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-18T00:00:00",
    )
    moves = []
    clipper = FakeClipper()
    positions = iter(((1918, 540), (1927, 540), (1927, 540)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        pointer_clipper=clipper,
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    inside = switcher.process(
        {"kind": "mouse_move", "x": 1918, "y": 540, "x_norm": 1918 / 3839, "y_norm": 540 / 1079}
    )
    blocked = switcher.process(
        {"kind": "mouse_move", "x": 1927, "y": 540, "x_norm": 1927 / 3839, "y_norm": 540 / 1079, "ts": 1.0}
    )

    assert inside["kind"] == "mouse_move"
    assert blocked == MoveProcessingResult(None, True)
    assert moves == [(1919, 540)]
    assert switcher._executor._edge_hold is not None
    assert switcher._executor._edge_hold.display_id == "1"
    assert switcher._executor._edge_hold.direction == "right"
    assert clipper.clip_calls == [
        (0, 0, 1919, 1079),
        (0, 0, 1919, 1079),
    ]
    assert clipper.clear_calls == 0


def test_auto_switch_routes_self_block_from_raw_edge_sample_before_actual_rebound_is_repaired():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-18T00:00:00",
    )
    moves = []
    clipper = FakeClipper()
    positions = iter(((1918, 540), (1927, 540), (1919, 540)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        pointer_clipper=clipper,
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    inside = switcher.process(
        {"kind": "mouse_move", "x": 1918, "y": 540, "x_norm": 1918 / 3839, "y_norm": 540 / 1079}
    )
    blocked = switcher.process(
        {"kind": "mouse_move", "x": 1920, "y": 540, "x_norm": 1920 / 3839, "y_norm": 540 / 1079, "ts": 1.0}
    )

    assert inside["kind"] == "mouse_move"
    assert blocked == MoveProcessingResult(None, True)
    assert moves == []
    assert switcher._executor._edge_hold is not None
    assert switcher._executor._edge_hold.display_id == "1"
    assert switcher._executor._edge_hold.direction == "right"
    assert clipper.clip_calls == [(0, 0, 1919, 1079)]


def test_auto_switch_preblocks_slow_self_logical_gap_approach_before_crossing_edge():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-18T00:00:00",
    )
    moves = []
    clipper = FakeClipper()
    positions = iter(((1914, 540), (1918, 540), (1919, 540)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        pointer_clipper=clipper,
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    inside = switcher.process(
        {"kind": "mouse_move", "x": 1914, "y": 540, "x_norm": 1914 / 3839, "y_norm": 540 / 1079}
    )
    blocked = switcher.process(
        {"kind": "mouse_move", "x": 1918, "y": 540, "x_norm": 1918 / 3839, "y_norm": 540 / 1079, "ts": 1.0}
    )

    assert inside["kind"] == "mouse_move"
    assert blocked == MoveProcessingResult(None, True)
    assert moves == []
    assert switcher._executor._edge_hold is not None
    assert switcher._executor._edge_hold.display_id == "1"
    assert switcher._executor._edge_hold.direction == "right"
    assert clipper.clip_calls == [(0, 0, 1919, 1079)]


def test_auto_switch_uses_self_gate_sample_for_fast_crossing_after_route_sample_is_cleared():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem("2", "2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-18T00:00:00",
    )
    moves = []
    clipper = FakeClipper()
    positions = iter(
        (
            (1918, 540),
            (1919, 540),
            (1919, 540),
            (1919, 540),
            (1919, 540),
        )
    )
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        pointer_clipper=clipper,
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    inside = switcher.process(
        {"kind": "mouse_move", "x": 1918, "y": 540, "x_norm": 1918 / 3839, "y_norm": 540 / 1079}
    )
    blocked = switcher.process(
        {"kind": "mouse_move", "x": 1927, "y": 540, "x_norm": 1927 / 3839, "y_norm": 540 / 1079, "ts": 1.0}
    )
    switcher._executor.release_edge_hold()
    blocked_again = switcher.process(
        {"kind": "mouse_move", "x": 1928, "y": 540, "x_norm": 1928 / 3839, "y_norm": 540 / 1079, "ts": 1.1}
    )

    assert inside["kind"] == "mouse_move"
    assert blocked == MoveProcessingResult(None, True)
    assert switcher._last_route_sample_by_node.get("A") is None
    gate_sample = switcher._last_self_gate_sample_by_node.get("A")
    assert gate_sample is not None
    assert gate_sample.display_id == "1"
    assert gate_sample.event["x"] == 1918
    assert blocked_again == MoveProcessingResult(None, True)
    assert moves == []
    assert switcher._executor._edge_hold is not None
    assert switcher._executor._edge_hold.display_id == "1"
    assert switcher._executor._edge_hold.direction == "right"
    assert clipper.clear_calls == 1
    assert clipper.clip_calls == [
        (0, 0, 1919, 1079),
        (0, 0, 1919, 1079),
    ]


def test_auto_switch_keeps_self_dead_edge_hold_without_repeated_warp_jitter():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1"]],
        physical_rows=[["1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),),
        captured_at="2026-04-17T00:00:00",
    )
    moves = []
    clipper = FakeClipper()
    positions = iter(((1919, 400), (1919, 400)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        pointer_clipper=clipper,
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=1920),
        now_fn=FakeClock(),
    )

    first = switcher.process({"kind": "mouse_move", "x": 1919, "y": 400, "x_norm": 1.0, "y_norm": 400 / 1079})
    second_event = {"kind": "mouse_move", "x": 1926, "y": 400, "x_norm": 1.0036, "y_norm": 400 / 1079}
    second = switcher.process(second_event)

    assert first == MoveProcessingResult(None, True)
    assert second["kind"] == "mouse_move"
    assert second["x"] == 1919
    assert second["y"] == 400
    assert moves == []
    assert clipper.clear_calls == 0


def test_auto_switch_keeps_self_dead_edge_hold_on_two_pixel_raw_inward_drift():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1"]],
        physical_rows=[["1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),),
        captured_at="2026-04-17T00:00:00",
    )
    moves = []
    clipper = FakeClipper()
    positions = iter(((1919, 400), (1919, 400)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        pointer_clipper=clipper,
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=1920),
        now_fn=FakeClock(),
    )

    first = switcher.process({"kind": "mouse_move", "x": 1919, "y": 400, "x_norm": 1.0, "y_norm": 400 / 1079})
    second_event = {"kind": "mouse_move", "x": 1917, "y": 400, "x_norm": 1917 / 1919, "y_norm": 400 / 1079}
    second = switcher.process(second_event)

    assert first == MoveProcessingResult(None, True)
    assert second["kind"] == "mouse_move"
    assert second["x"] == 1919
    assert second["y"] == 400
    assert moves == []
    assert clipper.clear_calls == 0
    assert switcher._executor._edge_hold is not None


def test_auto_switch_ignores_anchor_echo_after_internal_self_warp():
    display1 = r"\\.\DISPLAY1"
    display2 = r"\\.\DISPLAY2"
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[[display1, display2]],
        physical_rows=[[display1, display2]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem(display1, display1, MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem(display2, display2, MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-15T00:00:00",
    )
    moves = []
    positions = iter(((1919, 540), (1920, 540)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    first = switcher.process(
        {"kind": "mouse_move", "x": 1919, "y": 540, "x_norm": 1919 / 3839, "y_norm": 540 / 1079}
    )
    second = switcher.process(
        {"kind": "mouse_move", "x": 1920, "y": 540, "x_norm": 1920 / 3839, "y_norm": 540 / 1079}
    )

    assert first == MoveProcessingResult(None, True)
    assert second["kind"] == "mouse_move"
    assert second["x"] == 1920
    assert second["y"] == 540
    assert moves == [(1920, 540)]
    assert switcher._display_state_by_node["A"] == display2


def test_auto_switch_allows_reverse_self_warp_after_leaving_anchor():
    display1 = r"\\.\DISPLAY1"
    display2 = r"\\.\DISPLAY2"
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[[display1, display2]],
        physical_rows=[[display1, display2]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem(display1, display1, MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem(display2, display2, MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-15T00:00:00",
    )
    moves = []
    positions = iter(((1919, 540), (1922, 540), (1920, 540)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    first = switcher.process(
        {"kind": "mouse_move", "x": 1919, "y": 540, "x_norm": 1919 / 3839, "y_norm": 540 / 1079}
    )
    interior = {"kind": "mouse_move", "x": 1922, "y": 540, "x_norm": 1922 / 3839, "y_norm": 540 / 1079}
    second = switcher.process(interior)
    third = switcher.process(
        {"kind": "mouse_move", "x": 1920, "y": 540, "x_norm": 1920 / 3839, "y_norm": 540 / 1079}
    )

    assert first == MoveProcessingResult(None, True)
    assert second["kind"] == "mouse_move"
    assert second["x"] == interior["x"]
    assert second["y"] == interior["y"]
    assert third == MoveProcessingResult(None, True)
    assert moves == [(1920, 540), (1919, 540)]
    assert switcher._display_state_by_node["A"] == display1


def test_auto_switch_ignores_unrelated_pointer_jump_after_self_warp():
    display1 = r"\\.\DISPLAY1"
    display2 = r"\\.\DISPLAY2"
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[[display1, display2]],
        physical_rows=[[display1, display2]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem(display1, display1, MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem(display2, display2, MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-17T00:00:00",
    )
    moves = []
    positions = iter(((1919, 540), (2500, 300)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    first = switcher.process(
        {"kind": "mouse_move", "x": 1919, "y": 540, "x_norm": 1919 / 3839, "y_norm": 540 / 1079}
    )
    jumped = {"kind": "mouse_move", "x": 2500, "y": 300, "x_norm": 2500 / 3839, "y_norm": 300 / 1079}
    second = switcher.process(jumped)

    assert first == MoveProcessingResult(None, True)
    assert second["kind"] == jumped["kind"]
    assert second["x"] == jumped["x"]
    assert second["y"] == jumped["y"]
    assert second["x_norm"] == jumped["x_norm"]
    assert second["y_norm"] == jumped["y_norm"]
    assert moves == [(1920, 540)]
    assert switcher._display_state_by_node["A"] == display2


def test_auto_switch_allows_follow_up_self_warp_to_next_display_without_delay():
    display1 = r"\\.\DISPLAY1"
    display2 = r"\\.\DISPLAY2"
    display3 = r"\\.\DISPLAY3"
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[[display1, display2, display3]],
        physical_rows=[[display1, display2, display3]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem(display1, display1, MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem(display2, display2, MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
            MonitorInventoryItem(display3, display3, MonitorBounds(3840, 0, 1920, 1080), logical_order=2),
        ),
        captured_at="2026-04-15T00:00:00",
    )
    moves = []
    positions = iter(((1919, 540), (3839, 540)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=5760),
        now_fn=FakeClock(),
    )

    first = switcher.process(
        {"kind": "mouse_move", "x": 1919, "y": 540, "x_norm": 1919 / 5759, "y_norm": 540 / 1079}
    )
    second = switcher.process(
        {"kind": "mouse_move", "x": 3839, "y": 540, "x_norm": 3839 / 5759, "y_norm": 540 / 1079}
    )

    assert first == MoveProcessingResult(None, True)
    assert second == MoveProcessingResult(None, True)
    assert moves == [(1920, 540), (3840, 540)]
    assert switcher._display_state_by_node["A"] == display3


def test_auto_switch_left_block_allows_other_axis_motion():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1"]],
        physical_rows=[["1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),),
        captured_at="2026-04-15T00:00:00",
    )
    moves = []
    clipper = FakeClipper()
    positions = iter(((0, 400), (0, 400), (0, 401)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        pointer_clipper=clipper,
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=1920),
        now_fn=FakeClock(),
    )
    switcher.refresh_self_clip()

    blocked = switcher.process({"kind": "mouse_move", "x": 0, "y": 400, "x_norm": 0.0, "y_norm": 400 / 1079})
    slide = {"kind": "mouse_move", "x": 1, "y": 401, "x_norm": 1 / 1919, "y_norm": 401 / 1079, "ts": 1.0}
    moved = switcher.process(slide)

    assert blocked == MoveProcessingResult(None, True)
    assert moved["kind"] == "mouse_move"
    assert moved["x"] == 0
    assert moved["y"] == slide["y"]
    assert moves == []


def test_auto_switch_up_block_allows_other_axis_motion():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "A",
        logical_rows=[["1"]],
        physical_rows=[["1"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(MonitorInventoryItem("1", "1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),),
        captured_at="2026-04-15T00:00:00",
    )
    moves = []
    clipper = FakeClipper()
    positions = iter(((800, 0), (800, 0), (801, 0)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        pointer_clipper=clipper,
        actual_pointer_provider=lambda: next(positions),
        screen_bounds_provider=lambda: FakeBounds(width=1920),
        now_fn=FakeClock(),
    )
    switcher.refresh_self_clip()

    blocked = switcher.process({"kind": "mouse_move", "x": 800, "y": 0, "x_norm": 800 / 1919, "y_norm": 0.0})
    slide = {"kind": "mouse_move", "x": 801, "y": 1, "x_norm": 801 / 1919, "y_norm": 1 / 1079, "ts": 1.0}
    moved = switcher.process(slide)

    assert blocked == MoveProcessingResult(None, True)
    assert moved["kind"] == "mouse_move"
    assert moved["x"] == slide["x"]
    assert moved["y"] == 0
    assert moves == []


def test_auto_switch_remote_internal_warp_forwards_anchor_and_blocks_local_move():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0), LayoutNode("B", 1, 0)),
            auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
        ),
        "B",
        logical_rows=[["1", "2"]],
        physical_rows=[["1", "2"]],
    )
    moves = []
    router = FakeRouter(selected_target="B", active_target="B")
    switcher = AutoTargetSwitcher(
        _ctx(layout),
        router,
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )
    switcher._display_state_by_node["B"] = "1"

    result = switcher.process(
        {"kind": "mouse_move", "x": 1919, "y": 480, "x_norm": 0.5, "y_norm": 0.45}
    )

    assert isinstance(result, MoveProcessingResult)
    assert result.block_local is True
    assert result.event is not None
    assert result.event["kind"] == "mouse_move"
    assert moves == [(1919, 480), (1920, 480)]
    assert switcher._display_state_by_node["B"] == "2"


def test_auto_switch_respects_cooldown_window():
    clock = FakeClock()
    requests = []
    switcher = AutoTargetSwitcher(
        _ctx(_layout(enabled=True)),
        FakeRouter(selected_target=None),
        request_target=requests.append,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: None,
        screen_bounds_provider=lambda: FakeBounds(),
        now_fn=clock,
    )

    event = {"kind": "mouse_move", "x": 1919, "y": 600, "x_norm": 0.999, "y_norm": 0.55}
    assert switcher.process(event) == MoveProcessingResult(None, True)
    assert switcher.process(event) == event
    clock.advance(0.3)
    assert switcher.process(event) == MoveProcessingResult(None, True)
    assert requests == ["B", "B"]


def test_auto_switch_pending_target_keeps_current_node_as_self():
    requests = []
    moves = []
    router = FakeRouter(selected_target="B", active_target=None)
    switcher = AutoTargetSwitcher(
        _ctx(_layout(enabled=True)),
        router,
        request_target=requests.append,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        screen_bounds_provider=lambda: FakeBounds(),
        now_fn=FakeClock(),
    )

    event = {"kind": "mouse_move", "x": 1919, "y": 540, "x_norm": 0.999, "y_norm": 0.5}
    result = switcher.process(event)

    assert result == MoveProcessingResult(None, True)
    assert requests == ["B"]
    assert moves == []


def test_active_target_uses_virtual_remote_pointer_and_blocks_local_move():
    layout = _layout(enabled=True)
    router = FakeRouter(
        selected_target="B",
        active_target="B",
        last_remote_anchor_event={"kind": "mouse_move", "x": 960, "y": 540, "x_norm": 0.5, "y_norm": 0.5},
    )
    moves = []
    clock = FakeClock()
    pointer_positions = [(100, 100)]
    switcher = AutoTargetSwitcher(
        _ctx(layout),
        router,
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        actual_pointer_provider=lambda: pointer_positions[-1],
        screen_bounds_provider=lambda: FakeBounds(),
        now_fn=clock,
    )
    switcher._display_state_by_node["B"] = "1"
    switcher.on_router_state_change("active", "B")

    result = switcher.process(
        {"kind": "mouse_move", "x": 130, "y": 120, "x_norm": 130 / 1919, "y_norm": 120 / 1079, "ts": 1.0}
    )

    assert isinstance(result, MoveProcessingResult)
    assert result.block_local is True
    assert result.event is not None
    assert result.event["kind"] == "mouse_move"
    assert result.event["x"] == 990
    assert result.event["y"] == 560
    assert moves == [(100, 100)]


def test_active_target_clears_self_hold_before_remote_translation():
    layout = _layout(enabled=True)
    router = FakeRouter(selected_target=None, active_target=None)
    moves = []
    clipper = FakeClipper()
    clock = FakeClock()
    pointer_positions = [(0, 100)]
    switcher = AutoTargetSwitcher(
        _ctx(layout),
        router,
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        pointer_clipper=clipper,
        actual_pointer_provider=lambda: pointer_positions[-1],
        screen_bounds_provider=lambda: FakeBounds(),
        now_fn=clock,
    )

    blocked = switcher.process(
        {"kind": "mouse_move", "x": 0, "y": 100, "x_norm": 0.0, "y_norm": 100 / 1079, "ts": 1.0}
    )

    assert blocked == MoveProcessingResult(None, True)
    assert clipper.clear_calls == 0

    router._selected_target = "B"
    router._active_target = "B"
    router._last_remote_anchor_event = {
        "kind": "mouse_move",
        "x": 960,
        "y": 540,
        "x_norm": 0.5,
        "y_norm": 0.5,
    }
    switcher._display_state_by_node["B"] = "1"
    pointer_positions.append((100, 100))
    switcher.on_router_state_change("active", "B")

    result = switcher.process(
        {"kind": "mouse_move", "x": 130, "y": 120, "x_norm": 130 / 1919, "y_norm": 120 / 1079, "ts": 2.0}
    )

    assert isinstance(result, MoveProcessingResult)
    assert result.block_local is True
    assert result.event is not None
    assert result.event["x"] == 990
    assert result.event["y"] == 560
    assert clipper.clear_calls == 1
    assert moves == [(100, 100)]


def test_active_target_uses_late_remote_handoff_anchor_instead_of_center():
    layout = _layout(enabled=True)
    router = FakeRouter(selected_target="B", active_target="B", last_remote_anchor_event=None)
    moves = []
    clock = FakeClock()
    pointer_positions = [(100, 100)]
    switcher = AutoTargetSwitcher(
        _ctx(layout),
        router,
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        actual_pointer_provider=lambda: pointer_positions[-1],
        screen_bounds_provider=lambda: FakeBounds(),
        now_fn=clock,
    )
    switcher._display_state_by_node["B"] = "1"
    switcher.on_router_state_change("active", "B")
    router._last_remote_anchor_event = {
        "kind": "mouse_move",
        "x": 1919,
        "y": 540,
        "x_norm": 1919 / 1919,
        "y_norm": 540 / 1079,
    }

    result = switcher.process(
        {"kind": "mouse_move", "x": 97, "y": 100, "x_norm": 97 / 1919, "y_norm": 100 / 1079, "ts": 1.0}
    )

    assert isinstance(result, MoveProcessingResult)
    assert result.block_local is True
    assert result.event is not None
    assert result.event["x"] == 1916
    assert result.event["y"] == 540
    assert moves == [(100, 100)]
