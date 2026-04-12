"""Tests for routing/edge_actions.py."""

from capture.input_capture import MoveProcessingResult
from routing.edge_actions import EdgeActionExecutor
from routing.edge_routing import EdgeRoute
from routing.edge_runtime import AutoSwitchFrame, EdgeTransition
from runtime.context import NodeInfo, RuntimeContext
from runtime.layouts import AutoSwitchSettings, DisplayRef, LayoutConfig, LayoutNode


class FakeRouter:
    def __init__(self):
        self.handoffs = []

    def prepare_pointer_handoff(self, anchor_event):
        self.handoffs.append(anchor_event)


class FakeDisplayState:
    def __init__(self):
        self.remembered = []

    def build_edge_anchor_event(
        self,
        node,
        display_id,
        direction,
        cross_axis_ratio,
        bounds,
        source_event=None,
        *,
        blocked=False,
    ):
        base_x = 0 if direction == "right" else 1919
        base_y = 540
        if blocked:
            if direction in {"left", "right"}:
                base_x = 25
                if source_event is not None:
                    base_y = int(source_event["y"])
            else:
                if source_event is not None:
                    base_x = int(source_event["x"])
                base_y = 35
        return {
            "kind": "mouse_move",
            "x": base_x,
            "y": base_y,
            "x_norm": 0.0,
            "y_norm": 0.5,
        }

    def remember(self, node_id, display_id):
        self.remembered.append((node_id, display_id))

    def build_edge_hold_rect(self, node, display_id, direction, bounds):
        left, top, right, bottom = 0, 0, bounds.width - 1, bounds.height - 1
        if direction == "left":
            return (left, top, left, bottom)
        if direction == "right":
            return (right, top, right, bottom)
        if direction == "up":
            return (left, top, right, top)
        if direction == "down":
            return (left, bottom, right, bottom)
        raise ValueError(direction)


class FakeBounds:
    def __init__(self, left=0, top=0, width=1920, height=1080):
        self.left = left
        self.top = top
        self.width = width
        self.height = height


class FakeClipper:
    def __init__(self):
        self.clip_calls = []
        self.clear_calls = 0

    def clip_to_rect(self, left, top, right, bottom):
        self.clip_calls.append((left, top, right, bottom))
        return True

    def clear_clip(self):
        self.clear_calls += 1
        return True


def _ctx():
    nodes = [
        NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000}),
        NodeInfo.from_dict({"name": "B", "ip": "127.0.0.1", "port": 5001}),
    ]
    layout = LayoutConfig(
        nodes=(LayoutNode("A", 0, 0), LayoutNode("B", 1, 0)),
        auto_switch=AutoSwitchSettings(enabled=True, cooldown_ms=250, return_guard_ms=400),
    )
    return RuntimeContext(self_node=nodes[0], nodes=nodes, layout=layout)


def test_edge_action_executor_switches_target_and_records_guard_state():
    requests = []
    clears = []
    moves = []
    display_state = FakeDisplayState()
    router = FakeRouter()
    executor = EdgeActionExecutor(
        ctx=_ctx(),
        router=router,
        request_target=requests.append,
        clear_target=lambda: clears.append("clear"),
        pointer_mover=lambda x, y: moves.append((x, y)),
        pointer_clipper=None,
        display_state=display_state,
    )
    layout = _ctx().layout

    result = executor.apply_route(
        EdgeTransition(
            frame=AutoSwitchFrame(
                layout=layout,
                current_node_id="A",
                current_node=layout.get_node("A"),
                current_display_id="1",
                bounds=FakeBounds(),
                now=10.0,
            ),
            direction="right",
            cross_ratio=0.5,
            event={"kind": "mouse_move", "x": 1919, "y": 540, "ts": 100.0},
        ),
        EdgeRoute("target-switch", destination=DisplayRef("B", "1")),
    )

    assert result == MoveProcessingResult(None, True)
    assert requests == ["B"]
    assert clears == []
    assert router.handoffs
    assert moves == [(0, 540)]
    assert display_state.remembered == [("B", "1")]
    assert executor.should_drop_stale_move({"kind": "mouse_move", "x": 10, "y": 10, "ts": 100.01}) is True
    assert executor.is_inside_anchor_guard({"kind": "mouse_move", "x": 0, "y": 540}, 10.1) is True


def test_edge_action_executor_blocks_remote_edge_with_anchor_event():
    display_state = FakeDisplayState()
    executor = EdgeActionExecutor(
        ctx=_ctx(),
        router=FakeRouter(),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: None,
        pointer_clipper=None,
        display_state=display_state,
    )
    layout = _ctx().layout

    result = executor.apply_route(
        EdgeTransition(
            frame=AutoSwitchFrame(
                layout=layout,
                current_node_id="B",
                current_node=layout.get_node("B"),
                current_display_id="1",
                bounds=FakeBounds(),
                now=10.0,
            ),
            direction="left",
            cross_ratio=0.5,
            event={"kind": "mouse_move", "x": 0, "y": 540},
        ),
        EdgeRoute("block", reason="target-logical-gap"),
    )

    assert isinstance(result, MoveProcessingResult)
    assert result.block_local is True
    assert result.event["x"] == 25


def test_edge_action_executor_blocks_self_edge_with_axis_preserving_pointer_move():
    moves = []
    clipper = FakeClipper()
    display_state = FakeDisplayState()
    executor = EdgeActionExecutor(
        ctx=_ctx(),
        router=FakeRouter(),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        pointer_clipper=clipper,
        display_state=display_state,
    )
    layout = _ctx().layout

    result = executor.apply_route(
        EdgeTransition(
            frame=AutoSwitchFrame(
                layout=layout,
                current_node_id="A",
                current_node=layout.get_node("A"),
                current_display_id="1",
                bounds=FakeBounds(),
                now=10.0,
            ),
            direction="left",
            cross_ratio=0.5,
            event={"kind": "mouse_move", "x": -5, "y": 612},
        ),
        EdgeRoute("block", reason="self-dead-edge"),
    )

    assert result == MoveProcessingResult(None, True)
    assert moves == [(25, 612)]
    assert clipper.clip_calls == [(0, 0, 0, 1079)]


def test_edge_action_executor_blocks_vertical_self_edge_with_axis_preserving_pointer_move():
    moves = []
    clipper = FakeClipper()
    display_state = FakeDisplayState()
    executor = EdgeActionExecutor(
        ctx=_ctx(),
        router=FakeRouter(),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: moves.append((x, y)),
        pointer_clipper=clipper,
        display_state=display_state,
    )
    layout = _ctx().layout

    result = executor.apply_route(
        EdgeTransition(
            frame=AutoSwitchFrame(
                layout=layout,
                current_node_id="A",
                current_node=layout.get_node("A"),
                current_display_id="1",
                bounds=FakeBounds(),
                now=10.0,
            ),
            direction="up",
            cross_ratio=0.5,
            event={"kind": "mouse_move", "x": 744, "y": -3},
        ),
        EdgeRoute("block", reason="self-dead-edge"),
    )

    assert result == MoveProcessingResult(None, True)
    assert moves == [(744, 35)]
    assert clipper.clip_calls == [(0, 0, 1919, 0)]


def test_edge_action_executor_releases_expired_self_block_edge_hold():
    clipper = FakeClipper()
    display_state = FakeDisplayState()
    executor = EdgeActionExecutor(
        ctx=_ctx(),
        router=FakeRouter(),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: None,
        pointer_clipper=clipper,
        display_state=display_state,
    )
    layout = _ctx().layout

    executor.apply_route(
        EdgeTransition(
            frame=AutoSwitchFrame(
                layout=layout,
                current_node_id="A",
                current_node=layout.get_node("A"),
                current_display_id="1",
                bounds=FakeBounds(),
                now=10.0,
            ),
            direction="left",
            cross_ratio=0.5,
            event={"kind": "mouse_move", "x": -5, "y": 612},
        ),
        EdgeRoute("block", reason="self-dead-edge"),
    )

    executor.release_expired_edge_hold(10.01)
    assert clipper.clear_calls == 0
    executor.release_expired_edge_hold(10.02)
    assert clipper.clear_calls == 1


def test_edge_action_executor_does_not_refresh_same_hold_while_active():
    clipper = FakeClipper()
    display_state = FakeDisplayState()
    executor = EdgeActionExecutor(
        ctx=_ctx(),
        router=FakeRouter(),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: None,
        pointer_clipper=clipper,
        display_state=display_state,
    )
    layout = _ctx().layout

    transition = EdgeTransition(
        frame=AutoSwitchFrame(
            layout=layout,
            current_node_id="A",
            current_node=layout.get_node("A"),
            current_display_id="1",
            bounds=FakeBounds(),
            now=10.0,
        ),
        direction="left",
        cross_ratio=0.5,
        event={"kind": "mouse_move", "x": -5, "y": 612},
    )
    executor.apply_route(transition, EdgeRoute("block", reason="self-dead-edge"))
    executor.apply_route(
        EdgeTransition(
            frame=AutoSwitchFrame(
                layout=layout,
                current_node_id="A",
                current_node=layout.get_node("A"),
                current_display_id="1",
                bounds=FakeBounds(),
                now=10.005,
            ),
            direction="left",
            cross_ratio=0.5,
            event={"kind": "mouse_move", "x": -4, "y": 613},
        ),
        EdgeRoute("block", reason="self-dead-edge"),
    )

    assert clipper.clip_calls == [(0, 0, 0, 1079)]


def test_edge_action_executor_releases_hold_on_inward_motion():
    clipper = FakeClipper()
    display_state = FakeDisplayState()
    executor = EdgeActionExecutor(
        ctx=_ctx(),
        router=FakeRouter(),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: None,
        pointer_clipper=clipper,
        display_state=display_state,
    )
    layout = _ctx().layout
    frame = AutoSwitchFrame(
        layout=layout,
        current_node_id="A",
        current_node=layout.get_node("A"),
        current_display_id="1",
        bounds=FakeBounds(),
        now=10.0,
    )

    executor.apply_route(
        EdgeTransition(
            frame=frame,
            direction="left",
            cross_ratio=0.5,
            event={"kind": "mouse_move", "x": -5, "y": 612},
        ),
        EdgeRoute("block", reason="self-dead-edge"),
    )

    released = executor.maybe_release_edge_hold(
        {"kind": "mouse_move", "x": 4, "y": 612},
        AutoSwitchFrame(
            layout=layout,
            current_node_id="A",
            current_node=layout.get_node("A"),
            current_display_id="1",
            bounds=FakeBounds(),
            now=10.001,
        ),
    )

    assert released is True
    assert clipper.clear_calls == 1
