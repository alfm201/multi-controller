"""Tests for runtime/layout_editor.py and viewport helpers."""

from runtime.context import build_runtime_context
from runtime.layout_editor import LayoutEditor
from runtime.layout_geometry import (
    LayoutGeometrySpec,
    ViewportState,
    fit_viewport,
    layout_world_bounds,
    screen_to_world,
    zoom_at_point,
)


class FakeRegistry:
    def __init__(self, pairs):
        self._pairs = pairs

    def all(self):
        return list(self._pairs)


class FakeVar:
    def __init__(self, value=None):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class FakeWidget:
    def __init__(self):
        self.states = []

    def state(self, states):
        self.states.append(tuple(states))


class FakeCanvas:
    def __init__(self):
        self.focused = False

    def focus_set(self):
        self.focused = True

    def find_withtag(self, _tag):
        return []


class FakeCoordClient:
    def __init__(self):
        self.published_layouts = []

    def is_layout_editor(self):
        return True

    def publish_layout(self, layout, persist=True):
        self.published_layouts.append((layout, persist))
        return True

    def get_layout_editor(self):
        return "A"

    def is_layout_edit_pending(self):
        return False

    def end_layout_edit(self):
        return True


def _layout_ctx():
    config = {
        "nodes": [
            {"name": "A", "ip": "127.0.0.1", "port": 5000},
            {"name": "B", "ip": "127.0.0.1", "port": 5001},
        ],
        "layout": {
            "nodes": {
                "A": {"x": 0, "y": 0, "width": 1, "height": 1},
                "B": {"x": 1, "y": 0, "width": 1, "height": 1},
            }
        },
    }
    return build_runtime_context(config, override_name="A", config_path="config.json")


def _wire_editor(editor):
    editor._vars = {
        "layout_hint": FakeVar(),
        "lock_summary": FakeVar(),
        "viewport": FakeVar(),
        "selected_node": FakeVar(),
        "layout_edit": FakeVar(True),
        "auto_switch_enabled": FakeVar(False),
    }
    editor._layout_edit_toggle = FakeWidget()
    editor._auto_switch_toggle = FakeWidget()
    editor._auto_switch_settings_button = FakeWidget()
    editor._monitor_editor_button = FakeWidget()
    editor._fit_button = FakeWidget()
    editor._zoom_reset_button = FakeWidget()
    editor._view_reset_button = FakeWidget()
    editor._canvas = FakeCanvas()
    editor._canvas_width = 900
    editor._canvas_height = 600
    editor._viewport_initialized = True


def test_refresh_keeps_rendering_layout_while_dragging():
    ctx = _layout_ctx()
    editor = LayoutEditor(ctx, FakeRegistry([]), coordinator_resolver=lambda: None)
    _wire_editor(editor)
    rendered = []
    editor.render = lambda view: rendered.append(view)
    editor.state.draft_layout = ctx.layout
    editor.state.drag.kind = "node"

    editor.refresh(editor._fallback_view())

    assert len(rendered) == 1
    assert rendered[0].self_id == "A"


def test_layout_drag_rerenders_immediately_after_publish():
    ctx = _layout_ctx()
    coord_client = FakeCoordClient()
    editor = LayoutEditor(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: None,
        coord_client=coord_client,
    )
    _wire_editor(editor)
    editor.state.draft_layout = ctx.layout
    editor.state.drag.kind = "node"
    editor.state.drag.node_id = "B"
    editor.state.drag.origin_screen = (0, 0)
    editor.state.drag.origin_grid = (1, 0)
    editor.state.drag.start_layout = ctx.layout
    rendered_positions = []
    editor.render = lambda view: rendered_positions.append(
        editor.state.draft_layout.get_node("B").x
    )

    event = type("Event", (), {"x": editor._spec.grid_pitch_x, "y": 0})()

    editor._on_canvas_drag(event)

    assert coord_client.published_layouts
    assert coord_client.published_layouts[0][1] is False
    assert editor.state.draft_layout.get_node("B").x == 2
    assert rendered_positions == [2]


def test_layout_release_persists_only_once_after_preview_drag():
    ctx = _layout_ctx()
    coord_client = FakeCoordClient()
    editor = LayoutEditor(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: None,
        coord_client=coord_client,
    )
    _wire_editor(editor)
    editor.state.draft_layout = ctx.layout
    editor.state.drag.kind = "node"
    editor.state.drag.node_id = "B"
    editor.state.drag.origin_screen = (0, 0)
    editor.state.drag.origin_grid = (1, 0)
    editor.state.drag.start_layout = ctx.layout
    editor.render = lambda view: None

    drag_event = type("Event", (), {"x": editor._spec.grid_pitch_x, "y": 0})()

    editor._on_canvas_drag(drag_event)
    editor._on_canvas_release(None)

    assert [persist for _layout, persist in coord_client.published_layouts] == [
        False,
        True,
    ]
    assert editor.state.drag.kind is None
    assert editor.state.drag.preview_dirty is False


def test_pan_drag_updates_viewport_without_touching_layout():
    ctx = _layout_ctx()
    coord_client = FakeCoordClient()
    editor = LayoutEditor(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: None,
        coord_client=coord_client,
    )
    _wire_editor(editor)
    editor.state.draft_layout = ctx.layout
    editor.state.viewport = ViewportState(zoom=1.0, pan_x=10.0, pan_y=20.0)
    editor.state.drag.kind = "pan"
    editor.state.drag.origin_screen = (100, 200)
    editor.state.drag.origin_pan = (10.0, 20.0)
    editor.render = lambda view: None

    editor._on_canvas_drag(type("Event", (), {"x": 130, "y": 240})())

    assert coord_client.published_layouts == []
    assert editor.state.viewport.pan_x == 40.0
    assert editor.state.viewport.pan_y == 60.0


def test_zoom_helpers_keep_anchor_world_point_stable():
    ctx = _layout_ctx()
    spec = LayoutGeometrySpec()
    bounds = layout_world_bounds(ctx.layout, spec)
    viewport = fit_viewport(bounds, 900, 600, spec)
    world_before = screen_to_world(450, 300, viewport)

    zoomed = zoom_at_point(
        viewport,
        factor=1.2,
        anchor_screen_x=450,
        anchor_screen_y=300,
        spec=spec,
    )
    world_after = screen_to_world(450, 300, zoomed)

    assert round(world_before[0], 6) == round(world_after[0], 6)
    assert round(world_before[1], 6) == round(world_after[1], 6)


def test_escape_during_preview_drag_restores_start_layout():
    ctx = _layout_ctx()
    coord_client = FakeCoordClient()
    editor = LayoutEditor(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: None,
        coord_client=coord_client,
    )
    _wire_editor(editor)
    editor.state.draft_layout = ctx.layout
    editor.state.drag.kind = "node"
    editor.state.drag.node_id = "B"
    editor.state.drag.origin_screen = (0, 0)
    editor.state.drag.origin_grid = (1, 0)
    editor.state.drag.start_layout = ctx.layout
    editor.render = lambda view: None

    editor._on_canvas_drag(type("Event", (), {"x": editor._spec.grid_pitch_x, "y": 0})())
    editor._on_escape(None)

    assert [persist for _layout, persist in coord_client.published_layouts] == [
        False,
        False,
    ]
    assert editor.state.draft_layout.get_node("B").x == 1
