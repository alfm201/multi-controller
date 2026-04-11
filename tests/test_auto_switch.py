"""Tests for routing/auto_switch.py."""

from routing.auto_switch import AutoTargetSwitcher, detect_edge_direction
from runtime.context import NodeInfo, RuntimeContext
from runtime.layouts import AutoSwitchSettings, LayoutConfig, LayoutNode, replace_layout_monitors


class FakeRouter:
    def __init__(self, selected_target=None):
        self._selected_target = selected_target
        self.handoffs = []

    def get_selected_target(self):
        return self._selected_target

    def prepare_pointer_handoff(self, anchor_event):
        self.handoffs.append(anchor_event)


def _ctx(layout):
    nodes = [
        NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000}),
        NodeInfo.from_dict({"name": "B", "ip": "127.0.0.1", "port": 5001}),
        NodeInfo.from_dict({"name": "C", "ip": "127.0.0.1", "port": 5002}),
    ]
    return RuntimeContext(self_node=nodes[0], nodes=nodes, layout=layout)


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
    assert moves
    assert moves[-1][0] == 1920


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

    guarded_event = {"kind": "mouse_move", "x": 0, "y": 593, "x_norm": 0.0, "y_norm": 0.55}
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
