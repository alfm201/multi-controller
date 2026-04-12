"""Tests for routing/auto_switch.py."""

import time

from routing.auto_switch import AutoTargetSwitcher, detect_edge_direction
from runtime.context import NodeInfo, RuntimeContext
from runtime.layouts import AutoSwitchSettings, LayoutConfig, LayoutNode, replace_layout_monitors
from runtime.monitor_inventory import (
    MonitorBounds,
    MonitorInventoryItem,
    MonitorInventorySnapshot,
)


class FakeRouter:
    def __init__(self, selected_target=None):
        self._selected_target = selected_target
        self.handoffs = []

    def get_selected_target(self):
        return self._selected_target

    def prepare_pointer_handoff(self, anchor_event):
        self.handoffs.append(anchor_event)


class FakeClipper:
    def __init__(self):
        self.rects = []
        self.cleared = 0
        self.ops = []

    def clip_to_rect(self, left, top, right, bottom):
        self.rects.append((left, top, right, bottom))
        self.ops.append(("clip", left, top, right, bottom))
        return True

    def clear_clip(self):
        self.cleared += 1
        self.ops.append(("clear",))
        return True


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


def test_auto_switch_requests_adjacent_target_and_warps_pointer():
    requests = []
    clears = []
    moves = []
    router = FakeRouter(selected_target="B")
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

    assert result is None
    assert requests == ["C"]
    assert clears == []
    assert router.handoffs[-1]["x_norm"] == 0.0
    assert moves == [(0, 600)]


def test_auto_switch_can_return_to_self_and_clear_target():
    requests = []
    clears = []
    moves = []
    router = FakeRouter(selected_target="B")
    switcher = AutoTargetSwitcher(
        _ctx(_layout(enabled=True)),
        router,
        request_target=requests.append,
        clear_target=lambda: clears.append("clear"),
        pointer_mover=lambda x, y: moves.append((x, y)),
        screen_bounds_provider=lambda: FakeBounds(),
        now_fn=FakeClock(),
    )

    event = {"kind": "mouse_move", "x": 0, "y": 450, "x_norm": 0.0, "y_norm": 0.4}
    result = switcher.process(event)

    assert result is None
    assert requests == []
    assert clears == ["clear"]
    assert router.handoffs[-1]["x_norm"] == 1.0
    assert moves == [(1919, 450)]


def test_auto_switch_uses_internal_monitor_edges_without_target_switch():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(
                LayoutNode("A", 0, 0),
                LayoutNode("B", 3, 0),
            ),
            auto_switch=AutoSwitchSettings(
                enabled=True,
                cooldown_ms=250,
                return_guard_ms=400,
            ),
        ),
        "A",
        logical_rows=[["1", "2", "3", "4", "5", "6"]],
        physical_rows=[["1", "2", "3"], ["4", "5", "6"]],
    )
    requests = []
    router = FakeRouter(selected_target=None)
    moves = []
    switcher = AutoTargetSwitcher(
        _ctx(layout),
        router,
        request_target=requests.append,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    event = {"kind": "mouse_move", "x": 1996, "y": 0, "x_norm": 0.52, "y_norm": 0.0}
    assert switcher.process(event) is None
    assert requests == []
    assert router.handoffs == []
    assert moves


def test_auto_switch_warps_between_self_monitors_when_physical_order_differs():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(
                enabled=True,
                cooldown_ms=250,
                return_guard_ms=400,
            ),
        ),
        "A",
        logical_rows=[["2", "1"]],
        physical_rows=[["1", "2"]],
    )
    moves = []
    switcher = AutoTargetSwitcher(
        _ctx(layout),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    event = {"kind": "mouse_move", "x": 3839, "y": 540, "x_norm": 0.999, "y_norm": 0.5}

    assert switcher.process(event) is None
    assert moves
    assert moves[-1][0] < 1920


def test_auto_switch_uses_actual_cursor_position_for_self_edge_detection():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(
                enabled=True,
                cooldown_ms=250,
                return_guard_ms=400,
            ),
        ),
        "A",
        logical_rows=[[r"\\.\DISPLAY2", r"\\.\DISPLAY1"]],
        physical_rows=[[r"\\.\DISPLAY1", r"\\.\DISPLAY2"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem(r"\\.\DISPLAY2", r"\\.\DISPLAY2", MonitorBounds(-1920, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem(r"\\.\DISPLAY1", r"\\.\DISPLAY1", MonitorBounds(0, 0, 1920, 1080), logical_order=1),
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
        actual_pointer_provider=lambda: (1919, 439),
        screen_bounds_provider=lambda: FakeBounds(left=-1920, width=3840),
        now_fn=FakeClock(),
    )

    event = {"kind": "mouse_move", "x": 1920, "y": 439, "x_norm": 1.0, "y_norm": 0.4}

    assert switcher.process(event) is None
    assert moves == [(-1920, 439)]


def test_auto_switch_blocks_logical_crossing_when_no_physical_neighbor_exists():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(
                enabled=True,
                cooldown_ms=250,
                return_guard_ms=400,
            ),
        ),
        "A",
        logical_rows=[["1", "2"]],
        physical_rows=[["2", "1"]],
    )
    moves = []
    switcher = AutoTargetSwitcher(
        _ctx(layout),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )

    event = {"kind": "mouse_move", "x": 1920, "y": 540, "x_norm": 0.50013, "y_norm": 0.5}

    assert switcher.process(event) is None
    assert moves == []


def test_auto_switch_blocks_center_crossing_with_actual_pointer_when_physical_neighbor_is_missing():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(
                enabled=True,
                cooldown_ms=250,
                return_guard_ms=400,
            ),
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
    clipper = FakeClipper()
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        actual_pointer_provider=lambda: (1919, 540),
        pointer_clipper=clipper,
        screen_bounds_provider=lambda: FakeBounds(width=3840),
        now_fn=FakeClock(),
    )
    switcher._last_actual_self_pointer = (1919, 540)

    event = {"kind": "mouse_move", "x": 1920, "y": 540, "x_norm": 0.50013, "y_norm": 0.5}

    assert switcher.process(event) is None
    assert moves == []
    assert clipper.rects[-1] == (0, 0, 1919, 1079)


def test_auto_switch_dead_edge_block_ignores_cooldown_for_self_monitor():
    display1 = r"\\.\DISPLAY1"
    display2 = r"\\.\DISPLAY2"
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(
                enabled=True,
                cooldown_ms=250,
                return_guard_ms=400,
            ),
        ),
        "A",
        logical_rows=[[display1, display2]],
        physical_rows=[[display2, display1]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem(display1, display1, MonitorBounds(0, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem(display2, display2, MonitorBounds(1920, 100, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-11T00:00:00",
    )
    clock = FakeClock()
    moves = []
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        screen_bounds_provider=lambda: FakeBounds(width=3840, height=1180),
        now_fn=clock,
    )

    event = {"kind": "mouse_move", "x": 1920, "y": 640, "x_norm": 0.5, "y_norm": 0.55}

    assert switcher.process(event) is None
    clock.advance(0.1)
    assert switcher.process(event) is None
    assert moves == []


def test_auto_switch_self_internal_warp_uses_actual_monitor_bounds_ratio():
    display1 = r"\\.\DISPLAY1"
    display2 = r"\\.\DISPLAY2"
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(
                enabled=True,
                cooldown_ms=250,
                return_guard_ms=400,
            ),
        ),
        "A",
        logical_rows=[[display2, display1]],
        physical_rows=[[display1, display2]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem(display2, display2, MonitorBounds(0, 120, 1920, 1080), logical_order=0),
            MonitorInventoryItem(display1, display1, MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
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
        screen_bounds_provider=lambda: FakeBounds(width=3840, height=1200),
        now_fn=FakeClock(),
    )

    event = {"kind": "mouse_move", "x": 3839, "y": 540, "x_norm": 0.999, "y_norm": 0.45}

    assert switcher.process(event) is None
    assert moves[-1][0] == 0
    assert moves[-1][1] in range(659, 662)


def test_auto_switch_respects_cooldown_window():
    clock = FakeClock()
    requests = []
    switcher = AutoTargetSwitcher(
        _ctx(_layout(enabled=True)),
        FakeRouter(selected_target="B"),
        request_target=requests.append,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: None,
        screen_bounds_provider=lambda: FakeBounds(),
        now_fn=clock,
    )

    event = {"kind": "mouse_move", "x": 1919, "y": 600, "x_norm": 0.999, "y_norm": 0.55}
    assert switcher.process(event) is None
    assert switcher.process(event) == event
    clock.advance(0.3)
    assert switcher.process(event) is None
    assert requests == ["C", "C"]


def test_auto_switch_return_guard_skips_immediate_retrigger_near_anchor():
    clock = FakeClock()
    requests = []
    router = FakeRouter(selected_target="B")
    switcher = AutoTargetSwitcher(
        _ctx(_layout(enabled=True)),
        router,
        request_target=requests.append,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: None,
        screen_bounds_provider=lambda: FakeBounds(),
        now_fn=clock,
    )

    edge_event = {"kind": "mouse_move", "x": 1919, "y": 600, "x_norm": 0.999, "y_norm": 0.55}
    assert switcher.process(edge_event) is None

    guarded_event = {"kind": "mouse_move", "x": 1, "y": 600, "x_norm": 0.00052, "y_norm": 0.55}
    assert switcher.process(guarded_event) == guarded_event
    assert requests == ["C"]


def test_auto_switch_ignores_events_when_disabled():
    requests = []
    switcher = AutoTargetSwitcher(
        _ctx(_layout(enabled=False)),
        FakeRouter(selected_target="B"),
        request_target=requests.append,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: None,
        screen_bounds_provider=lambda: FakeBounds(),
        now_fn=FakeClock(),
    )

    event = {"kind": "mouse_move", "x": 1919, "y": 600, "x_norm": 0.999, "y_norm": 0.55}
    assert switcher.process(event) == event
    assert requests == []


def test_auto_switch_skips_offline_adjacent_target_without_warp():
    requests = []
    clears = []
    moves = []
    router = FakeRouter(selected_target="B")
    switcher = AutoTargetSwitcher(
        _ctx(_layout(enabled=True)),
        router,
        request_target=requests.append,
        clear_target=lambda: clears.append("clear"),
        is_target_online=lambda node_id: node_id != "C",
        pointer_mover=lambda x, y: moves.append((x, y)),
        screen_bounds_provider=lambda: FakeBounds(),
        now_fn=FakeClock(),
    )

    event = {"kind": "mouse_move", "x": 1919, "y": 600, "x_norm": 0.999, "y_norm": 0.55}

    assert switcher.process(event) == event
    assert requests == []
    assert clears == []
    assert moves == []
    assert router.handoffs == []


def test_auto_switch_blocks_offline_adjacent_target_as_dead_edge_for_self():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(
                LayoutNode("A", 0, 0),
                LayoutNode("B", 2, 0),
            ),
            auto_switch=AutoSwitchSettings(
                enabled=True,
                cooldown_ms=50,
                return_guard_ms=50,
            ),
        ),
        "A",
        logical_rows=[[r"\\.\DISPLAY2", r"\\.\DISPLAY1"]],
        physical_rows=[[r"\\.\DISPLAY1", r"\\.\DISPLAY2"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem(r"\\.\DISPLAY2", r"\\.\DISPLAY2", MonitorBounds(-1920, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem(r"\\.\DISPLAY1", r"\\.\DISPLAY1", MonitorBounds(0, 0, 1920, 1080), logical_order=1),
        ),
        captured_at="2026-04-11T00:00:00",
    )
    moves = []
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        is_target_online=lambda node_id: node_id != "B",
        pointer_mover=lambda x, y: moves.append((x, y)),
        screen_bounds_provider=lambda: FakeBounds(left=-1920, width=3840),
        now_fn=FakeClock(),
    )

    event = {"kind": "mouse_move", "x": -1, "y": 495, "x_norm": 0.499, "y_norm": 0.458}

    assert switcher.process(event) is None
    assert moves == []


def test_auto_switch_blocks_self_dead_edge_using_actual_cursor_position():
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(
                enabled=True,
                cooldown_ms=50,
                return_guard_ms=50,
            ),
        ),
        "A",
        logical_rows=[[r"\\.\DISPLAY2", r"\\.\DISPLAY1"]],
        physical_rows=[[r"\\.\DISPLAY1", r"\\.\DISPLAY2"]],
    )
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem(r"\\.\DISPLAY2", r"\\.\DISPLAY2", MonitorBounds(-1920, 0, 1920, 1080), logical_order=0),
            MonitorInventoryItem(r"\\.\DISPLAY1", r"\\.\DISPLAY1", MonitorBounds(0, 0, 1920, 1080), logical_order=1),
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
        actual_pointer_provider=lambda: (0, 57),
        screen_bounds_provider=lambda: FakeBounds(left=-1920, width=3840),
        now_fn=FakeClock(),
    )

    event = {"kind": "mouse_move", "x": -1, "y": 57, "x_norm": 0.5, "y_norm": 0.05}

    assert switcher.process(event) is None
    assert moves == []


def test_auto_switch_falls_back_to_raw_edge_when_actual_pointer_is_clipped_at_boundary():
    layout = _layout(enabled=True)
    requests = []
    switcher = AutoTargetSwitcher(
        _ctx(layout),
        FakeRouter(selected_target=None),
        request_target=requests.append,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: None,
        actual_pointer_provider=lambda: (1919, 600),
        screen_bounds_provider=lambda: FakeBounds(),
        now_fn=FakeClock(),
    )
    switcher._last_actual_self_pointer = (1919, 600)

    event = {"kind": "mouse_move", "x": 1920, "y": 600, "x_norm": 1.0, "y_norm": 0.55}

    assert switcher.process(event) is None
    assert requests == ["B"]


def test_auto_switch_self_reposition_consumes_first_post_warp_event():
    display1 = r"\\.\DISPLAY1"
    display2 = r"\\.\DISPLAY2"
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(
                enabled=True,
                cooldown_ms=50,
                return_guard_ms=50,
            ),
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
    actual_positions = iter(((1919, 477), (-1920, 477), (-1919, 477)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        actual_pointer_provider=lambda: next(actual_positions),
        screen_bounds_provider=lambda: FakeBounds(left=-1920, width=3840),
        now_fn=FakeClock(),
    )

    trigger_ts = time.time()
    trigger_event = {
        "kind": "mouse_move",
        "x": 1919,
        "y": 477,
        "x_norm": 0.999,
        "y_norm": 0.44,
        "ts": trigger_ts,
    }
    stale_event = {
        "kind": "mouse_move",
        "x": 1920,
        "y": 477,
        "x_norm": 1.0,
        "y_norm": 0.44,
        "ts": trigger_ts + 0.01,
    }

    assert switcher.process(trigger_event) is None
    assert switcher.process(stale_event) is None
    assert moves == [(-1920, 477)]


def test_auto_switch_self_internal_warp_updates_clip_destination():
    display1 = r"\\.\DISPLAY1"
    display2 = r"\\.\DISPLAY2"
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(
                enabled=True,
                cooldown_ms=50,
                return_guard_ms=50,
            ),
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
    clipper = FakeClipper()
    actual_positions = iter(((1919, 396), (-1920, 396), (-1919, 396)))
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: (moves.append((x, y)), clipper.ops.append(("move", x, y))),
        actual_pointer_provider=lambda: next(actual_positions),
        pointer_clipper=clipper,
        screen_bounds_provider=lambda: FakeBounds(left=-1920, width=3840),
        now_fn=FakeClock(),
    )

    trigger_event = {
        "kind": "mouse_move",
        "x": 1920,
        "y": 396,
        "x_norm": 1.0,
        "y_norm": 0.36,
        "ts": time.time(),
    }
    settle_event = {
        "kind": "mouse_move",
        "x": 0,
        "y": 396,
        "x_norm": 0.5,
        "y_norm": 0.36,
        "ts": trigger_event["ts"] + 0.01,
    }
    assert switcher.process(trigger_event) is None
    assert switcher.process(settle_event) is None
    assert moves == [(-1920, 396)]
    assert clipper.rects[-1] == (-1920, 0, -1, 1079)
    assert clipper.ops == [
        ("clip", 0, 0, 1919, 1079),
        ("clear",),
        ("move", -1920, 396),
        ("clip", -1920, 0, -1919, 1079),
        ("clip", -1920, 0, -1, 1079),
    ]


def test_auto_switch_dead_edge_uses_clip_wall_without_reposition_move():
    display1 = r"\\.\DISPLAY1"
    display2 = r"\\.\DISPLAY2"
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(
                enabled=True,
                cooldown_ms=50,
                return_guard_ms=50,
            ),
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
    clipper = FakeClipper()
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        pointer_clipper=clipper,
        screen_bounds_provider=lambda: FakeBounds(left=-1920, width=3840),
        now_fn=FakeClock(),
    )

    block_event = {
        "kind": "mouse_move",
        "x": 0,
        "y": 57,
        "x_norm": 0.5,
        "y_norm": 0.05,
        "ts": time.time(),
    }
    assert switcher.process(block_event) is None
    assert moves == []
    assert clipper.rects[-1] == (0, 0, 1919, 1079)


def test_auto_switch_refresh_self_clip_reapplies_current_display_after_click():
    display1 = r"\\.\DISPLAY1"
    display2 = r"\\.\DISPLAY2"
    layout = replace_layout_monitors(
        LayoutConfig(
            nodes=(LayoutNode("A", 0, 0),),
            auto_switch=AutoSwitchSettings(
                enabled=True,
                cooldown_ms=50,
                return_guard_ms=50,
            ),
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
    clipper = FakeClipper()
    switcher = AutoTargetSwitcher(
        _ctx_with_inventory(layout, snapshot),
        FakeRouter(selected_target=None),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_clipper=clipper,
        actual_pointer_provider=lambda: (0, 527),
        screen_bounds_provider=lambda: FakeBounds(left=-1920, width=3840),
        now_fn=FakeClock(),
    )

    switcher.refresh_self_clip()

    assert clipper.rects[-1] == (0, 0, 1919, 1079)
