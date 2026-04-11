"""Tests for runtime/layout_editor.py and viewport helpers."""

from PySide6.QtCore import QPointF

from runtime.context import build_runtime_context
from runtime.layout_editor import LayoutEditor
from runtime.layout_geometry import LayoutGeometrySpec, fit_viewport, layout_world_bounds, screen_to_world, zoom_at_point
from runtime.monitor_inventory import MonitorBounds, MonitorInventoryItem, MonitorInventorySnapshot
from runtime.status_view import build_status_view


class FakeRegistry:
    def __init__(self, pairs):
        self._pairs = pairs

    def all(self):
        return list(self._pairs)


class FakeCoordClient:
    def __init__(self):
        self.published_layouts = []
        self.request_layout_edit_calls = 0
        self.cleared = 0
        self.requested = []
        self._is_editor = True

    def is_layout_editor(self):
        return self._is_editor

    def publish_layout(self, layout, persist=True):
        self.published_layouts.append((layout, persist))
        return True

    def get_layout_editor(self):
        return "A" if self._is_editor else None

    def is_layout_edit_pending(self):
        return False

    def end_layout_edit(self):
        self._is_editor = False
        return True

    def request_layout_edit(self):
        self.request_layout_edit_calls += 1
        self._is_editor = True

    def clear_target(self):
        self.cleared += 1
        return True

    def request_target(self, node_id):
        self.requested.append(node_id)
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
    return build_runtime_context(config, override_name="A", config_path="config/config.json")


def _view(ctx):
    return build_status_view(ctx, FakeRegistry([]), coordinator_resolver=lambda: ctx.get_node("A"))


def test_layout_drag_publishes_preview_then_persist(qtbot):
    ctx = _layout_ctx()
    coord_client = FakeCoordClient()
    editor = LayoutEditor(ctx, FakeRegistry([]), coordinator_resolver=lambda: None, coord_client=coord_client)
    qtbot.addWidget(editor)
    editor.refresh(_view(ctx))

    event = type("Event", (), {"scenePos": lambda self=object(): QPointF(editor._spec.grid_pitch_x, 0)})()
    start = type("Event", (), {"scenePos": lambda self=object(): QPointF(0, 0)})()

    editor.on_node_pressed("B", start)
    editor.on_node_moved("B", event)
    editor.on_node_released("B", event)

    assert [persist for _layout, persist in coord_client.published_layouts] == [False, True]


def test_request_selected_target_clears_when_self_selected(qtbot):
    ctx = _layout_ctx()
    coord_client = FakeCoordClient()
    editor = LayoutEditor(ctx, FakeRegistry([]), coordinator_resolver=lambda: None, coord_client=coord_client)
    qtbot.addWidget(editor)
    editor.refresh(_view(ctx))
    editor.select_node("A")

    editor.request_selected_target()

    assert coord_client.cleared == 1


def test_request_selected_target_skips_offline_node(qtbot):
    ctx = _layout_ctx()
    coord_client = FakeCoordClient()
    editor = LayoutEditor(ctx, FakeRegistry([]), coordinator_resolver=lambda: None, coord_client=coord_client)
    qtbot.addWidget(editor)
    editor.refresh(_view(ctx))
    editor.select_node("B")

    editor.request_selected_target()

    assert coord_client.requested == []


def test_zoom_helpers_keep_anchor_world_point_stable():
    ctx = _layout_ctx()
    spec = LayoutGeometrySpec()
    bounds = layout_world_bounds(ctx.layout, spec)
    viewport = fit_viewport(bounds, 900, 600, spec)
    world_before = screen_to_world(450, 300, viewport)

    zoomed = zoom_at_point(viewport, factor=1.2, anchor_screen_x=450, anchor_screen_y=300, spec=spec)
    world_after = screen_to_world(450, 300, zoomed)

    assert round(world_before[0], 6) == round(world_after[0], 6)
    assert round(world_before[1], 6) == round(world_after[1], 6)


def test_layout_canvas_pan_moves_scene_even_when_content_fits_view(qtbot):
    ctx = _layout_ctx()
    coord_client = FakeCoordClient()
    editor = LayoutEditor(ctx, FakeRegistry([]), coordinator_resolver=lambda: None, coord_client=coord_client)
    qtbot.addWidget(editor)
    editor.resize(960, 640)
    editor.show()
    editor.refresh(_view(ctx))

    before = editor._canvas.mapToScene(editor._canvas.viewport().rect().center())
    editor.pan_by(120, 80)
    after = editor._canvas.mapToScene(editor._canvas.viewport().rect().center())

    assert round(after.x() - before.x(), 3) != 0
    assert round(after.y() - before.y(), 3) != 0


def test_monitor_button_is_enabled_for_editable_detected_node(qtbot):
    ctx = _layout_ctx()
    ctx.replace_monitor_inventory(
        MonitorInventorySnapshot(
            node_id="B",
            monitors=(
                MonitorInventoryItem(
                    monitor_id="\\\\.\\DISPLAY1",
                    display_name="DISPLAY1",
                    bounds=MonitorBounds(left=0, top=0, width=1920, height=1080),
                    is_primary=True,
                    logical_order=0,
                ),
            ),
            captured_at="2026-04-11T12:00:00",
        )
    )
    coord_client = FakeCoordClient()
    editor = LayoutEditor(ctx, FakeRegistry([]), coordinator_resolver=lambda: None, coord_client=coord_client)
    qtbot.addWidget(editor)
    editor.refresh(_view(ctx))
    editor.select_node("B")

    assert editor._monitor_button.isEnabled() is True


def test_selected_node_draws_explicit_highlight_tag(qtbot):
    ctx = _layout_ctx()
    coord_client = FakeCoordClient()
    editor = LayoutEditor(ctx, FakeRegistry([]), coordinator_resolver=lambda: None, coord_client=coord_client)
    qtbot.addWidget(editor)
    editor.refresh(_view(ctx))
    editor.select_node("B")

    item = editor._items["B"]

    assert item.pen().width() == 4
    assert item._tag_text.isVisible() is True
    assert item._tag_text.text() == "선택"
