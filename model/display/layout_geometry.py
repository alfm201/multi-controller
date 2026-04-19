"""Geometry helpers for the status-window layout editor."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Bounds:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def center_x(self) -> float:
        return self.left + self.width / 2.0

    @property
    def center_y(self) -> float:
        return self.top + self.height / 2.0


@dataclass(frozen=True)
class ViewportState:
    zoom: float = 1.0
    pan_x: float = 0.0
    pan_y: float = 0.0


@dataclass(frozen=True)
class LayoutGeometrySpec:
    grid_pitch_x: float = 140.0
    grid_pitch_y: float = 110.0
    tile_margin_x: float = 12.0
    tile_margin_y: float = 14.0
    scene_padding: float = 24.0
    fit_padding: float = 12.0
    min_zoom: float = 0.35
    max_zoom: float = 2.5


def clamp_zoom(zoom: float, spec: LayoutGeometrySpec) -> float:
    return max(spec.min_zoom, min(spec.max_zoom, float(zoom)))


def pan_viewport(viewport: ViewportState, dx: float, dy: float) -> ViewportState:
    return ViewportState(
        zoom=viewport.zoom,
        pan_x=viewport.pan_x + dx,
        pan_y=viewport.pan_y + dy,
    )


def screen_to_world(
    screen_x: float,
    screen_y: float,
    viewport: ViewportState,
) -> tuple[float, float]:
    zoom = max(viewport.zoom, 0.0001)
    return (
        (screen_x - viewport.pan_x) / zoom,
        (screen_y - viewport.pan_y) / zoom,
    )


def world_to_screen(
    world_x: float,
    world_y: float,
    viewport: ViewportState,
) -> tuple[float, float]:
    return (
        world_x * viewport.zoom + viewport.pan_x,
        world_y * viewport.zoom + viewport.pan_y,
    )


def screen_delta_to_grid(
    delta_x: float,
    delta_y: float,
    viewport: ViewportState,
    spec: LayoutGeometrySpec,
) -> tuple[int, int]:
    zoom = max(viewport.zoom, 0.0001)
    world_dx = delta_x / zoom
    world_dy = delta_y / zoom
    return (
        round(world_dx / spec.grid_pitch_x),
        round(world_dy / spec.grid_pitch_y),
    )


def zoom_at_point(
    viewport: ViewportState,
    *,
    factor: float,
    anchor_screen_x: float,
    anchor_screen_y: float,
    spec: LayoutGeometrySpec,
) -> ViewportState:
    next_zoom = clamp_zoom(viewport.zoom * factor, spec)
    if abs(next_zoom - viewport.zoom) < 1e-9:
        return viewport
    world_x, world_y = screen_to_world(anchor_screen_x, anchor_screen_y, viewport)
    return ViewportState(
        zoom=next_zoom,
        pan_x=anchor_screen_x - world_x * next_zoom,
        pan_y=anchor_screen_y - world_y * next_zoom,
    )


def node_world_bounds(node, spec: LayoutGeometrySpec) -> Bounds:
    return Bounds(
        left=node.x * spec.grid_pitch_x + spec.tile_margin_x,
        top=node.y * spec.grid_pitch_y + spec.tile_margin_y,
        right=(node.x + node.width) * spec.grid_pitch_x - spec.tile_margin_x,
        bottom=(node.y + node.height) * spec.grid_pitch_y - spec.tile_margin_y,
    )


def layout_world_bounds(layout, spec: LayoutGeometrySpec) -> Bounds:
    if layout is None or not layout.nodes:
        return Bounds(
            left=-spec.scene_padding,
            top=-spec.scene_padding,
            right=spec.scene_padding,
            bottom=spec.scene_padding,
        )
    nodes = [node_world_bounds(node, spec) for node in layout.nodes]
    return Bounds(
        left=min(bounds.left for bounds in nodes) - spec.scene_padding,
        top=min(bounds.top for bounds in nodes) - spec.scene_padding,
        right=max(bounds.right for bounds in nodes) + spec.scene_padding,
        bottom=max(bounds.bottom for bounds in nodes) + spec.scene_padding,
    )


def fit_viewport(
    bounds: Bounds,
    viewport_width: float,
    viewport_height: float,
    spec: LayoutGeometrySpec,
) -> ViewportState:
    usable_width = max(viewport_width - spec.fit_padding * 2.0, 1.0)
    usable_height = max(viewport_height - spec.fit_padding * 2.0, 1.0)
    zoom_x = usable_width / max(bounds.width, 1.0)
    zoom_y = usable_height / max(bounds.height, 1.0)
    zoom = clamp_zoom(min(zoom_x, zoom_y), spec)
    return ViewportState(
        zoom=zoom,
        pan_x=viewport_width / 2.0 - bounds.center_x * zoom,
        pan_y=viewport_height / 2.0 - bounds.center_y * zoom,
    )


def center_viewport(
    bounds: Bounds,
    viewport_width: float,
    viewport_height: float,
    spec: LayoutGeometrySpec,
    *,
    zoom: float = 1.0,
) -> ViewportState:
    next_zoom = clamp_zoom(zoom, spec)
    return ViewportState(
        zoom=next_zoom,
        pan_x=viewport_width / 2.0 - bounds.center_x * next_zoom,
        pan_y=viewport_height / 2.0 - bounds.center_y * next_zoom,
    )
