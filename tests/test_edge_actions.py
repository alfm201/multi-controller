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
        self.local_returns = []

    def prepare_pointer_handoff(self, anchor_event):
        self.handoffs.append(anchor_event)

    def prepare_local_return(self, anchor_event):
        self.local_returns.append(anchor_event)


class FakeDisplayState:
    def __init__(self):
        self.remembered = []

    def node_screen_bounds(self, node_id, node, fallback_bounds):
        return fallback_bounds

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

    def build_local_edge_clip_rect(self, node, display_id, direction, bounds):
        del node, display_id, direction
        return (0, 0, bounds.width - 1, bounds.height - 1)


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


class FailingRefreshClipper(FakeClipper):
    def __init__(self):
        super().__init__()
        self.fail_refresh = False

    def clip_to_rect(self, left, top, right, bottom):
        if self.fail_refresh:
            self.clip_calls.append((left, top, right, bottom))
            return False
        return super().clip_to_rect(left, top, right, bottom)


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
    assert moves == []
    assert display_state.remembered == [("B", "1")]
    assert executor.should_drop_stale_move({"kind": "mouse_move", "x": 10, "y": 10, "ts": 100.01}) is False
    assert executor.is_inside_anchor_guard({"kind": "mouse_move", "x": 0, "y": 540}, 10.1) is False


def test_edge_action_executor_blocks_target_switch_while_dragging():
    requests = []
    clears = []
    display_state = FakeDisplayState()
    router = FakeRouter()
    router.has_pressed_mouse_buttons = lambda: True
    executor = EdgeActionExecutor(
        ctx=_ctx(),
        router=router,
        request_target=requests.append,
        clear_target=lambda: clears.append("clear"),
        pointer_mover=lambda x, y: None,
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

    assert result == {"kind": "mouse_move", "x": 1919, "y": 540, "ts": 100.0}
    assert requests == []
    assert clears == []


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


def test_edge_action_executor_return_to_self_records_local_return_without_immediate_warp():
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
                current_node_id="B",
                current_node=layout.get_node("B"),
                current_display_id="1",
                bounds=FakeBounds(),
                now=10.0,
            ),
            direction="left",
            cross_ratio=0.5,
            event={"kind": "mouse_move", "x": 0, "y": 540, "ts": 100.0},
        ),
        EdgeRoute("target-switch", destination=DisplayRef("A", "1")),
    )

    assert result == MoveProcessingResult(None, True)
    assert requests == []
    assert clears == ["clear"]
    assert router.local_returns
    assert moves == []


def test_edge_action_executor_blocks_self_edge_without_immediate_warp():
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
    assert moves == []
    assert clipper.clip_calls == [(0, 0, 1919, 1079)]


def test_edge_action_executor_blocks_self_edge_with_clip_before_any_fallback_warp():
    order = []

    class OrderedClipper(FakeClipper):
        def clip_to_rect(self, left, top, right, bottom):
            order.append(("clip", (left, top, right, bottom)))
            return super().clip_to_rect(left, top, right, bottom)

    clipper = OrderedClipper()
    display_state = FakeDisplayState()
    executor = EdgeActionExecutor(
        ctx=_ctx(),
        router=FakeRouter(),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: order.append(("warp", (x, y))),
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
            direction="right",
            cross_ratio=0.5,
            event={"kind": "mouse_move", "x": 1925, "y": 612, "ts": 100.0},
        ),
        EdgeRoute("block", reason="self-logical-gap"),
    )

    assert result == MoveProcessingResult(None, True)
    assert order == [("clip", (0, 0, 1919, 1079))]
    assert executor._edge_hold is not None
    assert executor._edge_hold.direction == "right"
    assert executor.should_drop_stale_move({"kind": "mouse_move", "x": 1925, "y": 612, "ts": 100.01}) is False
    assert executor.should_drop_stale_move({"kind": "mouse_move", "x": 1925, "y": 612, "ts": 100.06}) is False


def test_edge_action_executor_falls_back_to_warp_when_local_clip_cannot_start():
    order = []

    class FailingClipper(FakeClipper):
        def clip_to_rect(self, left, top, right, bottom):
            order.append(("clip", (left, top, right, bottom)))
            return False

    display_state = FakeDisplayState()
    executor = EdgeActionExecutor(
        ctx=_ctx(),
        router=FakeRouter(),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda x, y: order.append(("warp", (x, y))),
        pointer_clipper=FailingClipper(),
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
            event={"kind": "mouse_move", "x": 1925, "y": 612, "ts": 100.0},
        ),
        EdgeRoute("block", reason="self-logical-gap"),
    )

    assert result == MoveProcessingResult(None, True)
    assert order == [
        ("clip", (0, 0, 1919, 1079)),
        ("warp", (25, 612)),
    ]
    assert executor._edge_hold is None
    assert executor.should_drop_stale_move({"kind": "mouse_move", "x": 1925, "y": 612, "ts": 100.01}) is True


def test_edge_action_executor_stale_guard_allows_inward_release_move_after_self_block():
    clipper = FakeClipper()
    display_state = FakeDisplayState()
    executor = EdgeActionExecutor(
        ctx=_ctx(),
        router=FakeRouter(),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        pointer_mover=lambda _x, _y: None,
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
            direction="right",
            cross_ratio=0.5,
            event={"kind": "mouse_move", "x": 1925, "y": 612, "ts": 100.0},
        ),
        EdgeRoute("block", reason="self-logical-gap"),
    )

    assert executor.should_drop_stale_move({"kind": "mouse_move", "x": 1917, "y": 612, "ts": 100.01}) is False


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
    assert moves == []
    assert clipper.clip_calls == [(0, 0, 1919, 1079)]


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

    executor.release_expired_edge_hold(10.03)
    assert clipper.clear_calls == 0
    executor.release_expired_edge_hold(10.07)
    assert clipper.clear_calls == 0
    executor.release_expired_edge_hold(10.07, force=True)
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

    assert clipper.clip_calls == [(0, 0, 1919, 1079)]


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

    released = executor.continue_edge_hold(
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

    assert released == {"kind": "mouse_move", "x": 4, "y": 612}
    assert clipper.clear_calls == 1


def test_edge_action_executor_continues_hold_while_pressing_blocked_edge():
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

    held = executor.continue_edge_hold(
        {"kind": "mouse_move", "x": 0, "y": 630},
        AutoSwitchFrame(
            layout=layout,
            current_node_id="A",
            current_node=layout.get_node("A"),
            current_display_id="1",
            bounds=FakeBounds(),
            now=10.001,
        ),
    )

    assert held == {"kind": "mouse_move", "x": 0, "y": 630}
    assert clipper.clear_calls == 0


def test_edge_action_executor_keeps_left_hold_on_one_pixel_raw_drift():
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

    held = executor.continue_edge_hold(
        {"kind": "mouse_move", "x": 0, "y": 630, "__self_event_rebound__": True},
        AutoSwitchFrame(
            layout=layout,
            current_node_id="A",
            current_node=layout.get_node("A"),
            current_display_id="1",
            bounds=FakeBounds(),
            now=10.001,
        ),
        source_event={"kind": "mouse_move", "x": 1, "y": 630},
    )

    assert held["x"] == 0
    assert held["y"] == 630
    assert executor._edge_hold is not None
    assert clipper.clear_calls == 0


def test_edge_action_executor_keeps_right_hold_on_two_pixel_raw_drift():
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
            direction="right",
            cross_ratio=0.5,
            event={"kind": "mouse_move", "x": 1924, "y": 612},
        ),
        EdgeRoute("block", reason="self-dead-edge"),
    )

    held = executor.continue_edge_hold(
        {"kind": "mouse_move", "x": 1919, "y": 612, "__self_event_rebound__": True},
        AutoSwitchFrame(
            layout=layout,
            current_node_id="A",
            current_node=layout.get_node("A"),
            current_display_id="1",
            bounds=FakeBounds(),
            now=10.001,
        ),
        source_event={"kind": "mouse_move", "x": 1917, "y": 612},
    )

    assert held["x"] == 1919
    assert held["y"] == 612
    assert executor._edge_hold is not None
    assert clipper.clear_calls == 0


def test_edge_action_executor_clips_local_hold_to_display_rect():
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
            direction="right",
            cross_ratio=0.5,
            event={"kind": "mouse_move", "x": 1924, "y": 612},
        ),
        EdgeRoute("block", reason="self-dead-edge"),
    )

    assert clipper.clip_calls == [(0, 0, 1919, 1079)]
    assert executor._edge_hold is not None
    assert executor._edge_hold.rect == (1919, 0, 1919, 1079)


def test_edge_action_executor_keeps_right_hold_on_three_pixel_raw_drift():
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
            direction="right",
            cross_ratio=0.5,
            event={"kind": "mouse_move", "x": 1924, "y": 612},
        ),
        EdgeRoute("block", reason="self-dead-edge"),
    )

    held = executor.continue_edge_hold(
        {"kind": "mouse_move", "x": 1919, "y": 612, "__self_event_rebound__": True},
        AutoSwitchFrame(
            layout=layout,
            current_node_id="A",
            current_node=layout.get_node("A"),
            current_display_id="1",
            bounds=FakeBounds(),
            now=10.001,
        ),
        source_event={"kind": "mouse_move", "x": 1916, "y": 612},
    )

    assert held["x"] == 1919
    assert held["y"] == 612
    assert executor._edge_hold is not None
    assert clipper.clear_calls == 0


def test_edge_action_executor_releases_local_hold_on_large_inward_move():
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

    released = executor.continue_edge_hold(
        {"kind": "mouse_move", "x": 4, "y": 612, "__self_event_rebound__": True},
        AutoSwitchFrame(
            layout=layout,
            current_node_id="A",
            current_node=layout.get_node("A"),
            current_display_id="1",
            bounds=FakeBounds(),
            now=10.001,
        ),
        source_event={"kind": "mouse_move", "x": 4, "y": 612},
    )

    assert released["x"] == 4
    assert released["y"] == 612
    assert executor._edge_hold is None
    assert clipper.clear_calls == 1


def test_edge_action_executor_releases_local_hold_after_two_small_inward_samples():
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

    first = executor.continue_edge_hold(
        {"kind": "mouse_move", "x": 2, "y": 612, "__self_event_rebound__": True},
        AutoSwitchFrame(
            layout=layout,
            current_node_id="A",
            current_node=layout.get_node("A"),
            current_display_id="1",
            bounds=FakeBounds(),
            now=10.001,
        ),
        source_event={"kind": "mouse_move", "x": 2, "y": 612},
    )
    second = executor.continue_edge_hold(
        {"kind": "mouse_move", "x": 2, "y": 612, "__self_event_rebound__": True},
        AutoSwitchFrame(
            layout=layout,
            current_node_id="A",
            current_node=layout.get_node("A"),
            current_display_id="1",
            bounds=FakeBounds(),
            now=10.002,
        ),
        source_event={"kind": "mouse_move", "x": 2, "y": 612},
    )

    assert first == MoveProcessingResult(None, True)
    assert second["x"] == 2
    assert second["y"] == 612
    assert executor._edge_hold is None
    assert clipper.clear_calls == 1


def test_edge_action_executor_ignores_stale_raw_inward_move_during_rebound_hold():
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
            direction="right",
            cross_ratio=0.5,
            event={"kind": "mouse_move", "x": 1924, "y": 612},
        ),
        EdgeRoute("block", reason="self-dead-edge"),
    )

    held = executor.continue_edge_hold(
        {"kind": "mouse_move", "x": 1919, "y": 612, "__self_event_rebound__": True},
        AutoSwitchFrame(
            layout=layout,
            current_node_id="A",
            current_node=layout.get_node("A"),
            current_display_id="1",
            bounds=FakeBounds(),
            now=10.001,
        ),
        source_event={"kind": "mouse_move", "x": 1918, "y": 612},
    )

    assert held["x"] == 1919
    assert executor._edge_hold is not None
    assert (executor._edge_hold.node_id, executor._edge_hold.display_id, executor._edge_hold.direction) == (
        "A",
        "1",
        "right",
    )
    assert clipper.clear_calls == 0


def test_edge_action_executor_pins_remote_hold_to_blocked_edge():
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
        current_node_id="B",
        current_node=layout.get_node("B"),
        current_display_id="1",
        bounds=FakeBounds(),
        now=10.0,
    )

    executor.apply_route(
        EdgeTransition(
            frame=frame,
            direction="right",
            cross_ratio=0.5,
            event={"kind": "mouse_move", "x": 1924, "y": 612},
        ),
        EdgeRoute("block", reason="target-logical-gap"),
    )

    held = executor.continue_edge_hold(
        {"kind": "mouse_move", "x": 1926, "y": 630},
        AutoSwitchFrame(
            layout=layout,
            current_node_id="B",
            current_node=layout.get_node("B"),
            current_display_id="1",
            bounds=FakeBounds(),
            now=10.001,
        ),
    )

    assert held["x"] == 1919
    assert held["y"] == 630
    assert clipper.clip_calls == []
    assert clipper.clear_calls == 0


def test_edge_action_executor_releases_remote_hold_on_inward_motion():
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
        current_node_id="B",
        current_node=layout.get_node("B"),
        current_display_id="1",
        bounds=FakeBounds(),
        now=10.0,
    )

    executor.apply_route(
        EdgeTransition(
            frame=frame,
            direction="right",
            cross_ratio=0.5,
            event={"kind": "mouse_move", "x": 1924, "y": 612},
        ),
        EdgeRoute("block", reason="target-logical-gap"),
    )

    released = executor.continue_edge_hold(
        {"kind": "mouse_move", "x": 1918, "y": 620},
        AutoSwitchFrame(
            layout=layout,
            current_node_id="B",
            current_node=layout.get_node("B"),
            current_display_id="1",
            bounds=FakeBounds(),
            now=10.001,
        ),
    )

    assert released == {"kind": "mouse_move", "x": 1918, "y": 620}
    assert executor._edge_hold is None
    assert clipper.clip_calls == []
    assert clipper.clear_calls == 0


def test_edge_action_executor_applies_hold_display_hint_to_rebound_event():
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
            direction="right",
            cross_ratio=0.5,
            event={"kind": "mouse_move", "x": -5, "y": 612},
        ),
        EdgeRoute("block", reason="self-dead-edge"),
    )

    hinted = executor.apply_edge_hold_routing_hint(
        {"kind": "mouse_move", "x": 1920, "y": 612, "__self_event_rebound__": True},
        current_node_id="A",
    )

    assert hinted["__routing_display_id__"] == "1"
    assert executor._edge_hold is not None
    assert (executor._edge_hold.node_id, executor._edge_hold.display_id, executor._edge_hold.direction) == (
        "A",
        "1",
        "right",
    )
    assert clipper.clear_calls == 0

    executor.release_expired_edge_hold(10.07, force=True)
    assert clipper.clear_calls == 1


def test_edge_action_executor_applies_hold_display_hint_while_self_hold_is_active():
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
            direction="right",
            cross_ratio=0.5,
            event={"kind": "mouse_move", "x": 1924, "y": 612},
        ),
        EdgeRoute("block", reason="self-dead-edge"),
    )

    hinted = executor.apply_edge_hold_routing_hint(
        {"kind": "mouse_move", "x": 1919, "y": 612},
        current_node_id="A",
    )

    assert hinted["__routing_display_id__"] == "1"


def test_edge_action_executor_blocks_uncertain_local_hold_after_focus_transition():
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
            direction="right",
            cross_ratio=0.5,
            event={"kind": "mouse_move", "x": 1924, "y": 612},
        ),
        EdgeRoute("block", reason="self-dead-edge"),
    )
    clipper.clear_clip()
    assert executor.mark_local_hold_risk(reason="focus-risk") is True
    assert executor._edge_hold is not None
    assert executor._edge_hold.state == "guarded"
    moves.clear()

    blocked = executor.continue_edge_hold(
        {"kind": "mouse_move", "x": 1927, "y": 612, "ts": 1.0},
        AutoSwitchFrame(
            layout=layout,
            current_node_id="A",
            current_node=layout.get_node("A"),
            current_display_id="1",
            bounds=FakeBounds(),
            now=10.001,
        ),
    )

    assert blocked == MoveProcessingResult(None, True)
    assert executor._edge_hold is not None
    assert clipper.clip_calls[-1] == (0, 0, 1919, 1079)
    assert moves == [(25, 612)]


def test_edge_action_executor_repairs_rebound_leak_back_to_local_hold_anchor():
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
            direction="right",
            cross_ratio=0.5,
            event={"kind": "mouse_move", "x": 1924, "y": 612},
        ),
        EdgeRoute("block", reason="self-dead-edge"),
    )
    moves.clear()

    repaired = executor.continue_edge_hold(
        {"kind": "mouse_move", "x": 1927, "y": 612, "__self_event_rebound__": True, "ts": 1.0},
        AutoSwitchFrame(
            layout=layout,
            current_node_id="A",
            current_node=layout.get_node("A"),
            current_display_id="1",
            bounds=FakeBounds(),
            now=10.001,
        ),
        source_event={"kind": "mouse_move", "x": 1919, "y": 612, "ts": 1.0},
    )

    assert repaired == MoveProcessingResult(None, True)
    assert executor._edge_hold is not None
    assert moves == [(25, 612)]


def test_edge_action_executor_releases_local_hold_after_guarded_inward_samples():
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
            direction="right",
            cross_ratio=0.5,
            event={"kind": "mouse_move", "x": 1924, "y": 612},
        ),
        EdgeRoute("block", reason="self-dead-edge"),
    )
    clipper.clear_clip()
    assert executor.mark_local_hold_risk(reason="focus-risk") is True

    first = executor.continue_edge_hold(
        {"kind": "mouse_move", "x": 1917, "y": 612, "ts": 1.0},
        AutoSwitchFrame(
            layout=layout,
            current_node_id="A",
            current_node=layout.get_node("A"),
            current_display_id="1",
            bounds=FakeBounds(),
            now=10.001,
        ),
    )
    second = executor.continue_edge_hold(
        {"kind": "mouse_move", "x": 1916, "y": 612, "ts": 1.01},
        AutoSwitchFrame(
            layout=layout,
            current_node_id="A",
            current_node=layout.get_node("A"),
            current_display_id="1",
            bounds=FakeBounds(),
            now=10.002,
        ),
    )

    assert first == MoveProcessingResult(None, True)
    assert second == {"kind": "mouse_move", "x": 1916, "y": 612, "ts": 1.01}
    assert executor._edge_hold is None


def test_edge_action_executor_returns_guarded_local_hold_to_latched_on_stable_edge_sample():
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
            direction="right",
            cross_ratio=0.5,
            event={"kind": "mouse_move", "x": 1924, "y": 612},
        ),
        EdgeRoute("block", reason="self-dead-edge"),
    )
    assert executor.mark_local_hold_risk(reason="focus-risk") is True

    stabilized = executor.continue_edge_hold(
        {"kind": "mouse_move", "x": 1919, "y": 612, "ts": 1.0},
        AutoSwitchFrame(
            layout=layout,
            current_node_id="A",
            current_node=layout.get_node("A"),
            current_display_id="1",
            bounds=FakeBounds(),
            now=10.001,
        ),
    )

    assert stabilized == {"kind": "mouse_move", "x": 1919, "y": 612, "ts": 1.0}
    assert executor._edge_hold is not None
    assert executor._edge_hold.state == "latched"


def test_edge_action_executor_keeps_guarded_local_hold_when_clip_refresh_fails():
    clipper = FailingRefreshClipper()
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
            direction="right",
            cross_ratio=0.5,
            event={"kind": "mouse_move", "x": 1924, "y": 612},
        ),
        EdgeRoute("block", reason="self-dead-edge"),
    )
    clipper.clear_clip()
    clipper.fail_refresh = True
    assert executor.mark_local_hold_risk(reason="focus-risk") is True

    blocked = executor.continue_edge_hold(
        {"kind": "mouse_move", "x": 1919, "y": 612, "ts": 1.0},
        AutoSwitchFrame(
            layout=layout,
            current_node_id="A",
            current_node=layout.get_node("A"),
            current_display_id="1",
            bounds=FakeBounds(),
            now=10.001,
        ),
    )

    assert blocked == MoveProcessingResult(None, True)
    assert executor._edge_hold is not None
    assert executor._edge_hold.state == "guarded"
