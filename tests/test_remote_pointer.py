from routing.remote_pointer import ActiveRemotePointer


class FakeBounds:
    def __init__(self, left=0, top=0, width=1920, height=1080):
        self.left = left
        self.top = top
        self.width = width
        self.height = height


class FakeDisplayState:
    def __init__(self, *, scale=(1.0, 1.0), rect=(0, 0, 1919, 1079)):
        self.scale = scale
        self.rect = rect

    def build_display_center_event(self, node, display_id, bounds):
        return {
            "kind": "mouse_move",
            "x": 100,
            "y": 200,
            "x_norm": 100 / 1919,
            "y_norm": 200 / 1079,
        }

    def display_pixel_rect(self, node, display_id, bounds):
        return self.rect

    def pointer_speed_scale(
        self,
        *,
        source_node,
        source_display_id,
        source_bounds,
        target_node,
        target_display_id,
        target_bounds,
    ):
        return self.scale


class DummyNode:
    def __init__(self, node_id="A"):
        self.node_id = node_id


def test_remote_pointer_accumulates_fractional_scaled_motion():
    moves = []
    pointer = ActiveRemotePointer(pointer_mover=lambda x, y: moves.append((x, y)))
    display_state = FakeDisplayState(scale=(0.5, 0.5))
    source_node = DummyNode("A")
    target_node = DummyNode("B")

    pointer.begin(
        node_id="B",
        display_id="1",
        source_node_id="A",
        source_display_id="1",
        anchor_local=(100, 100),
        initial_event={"kind": "mouse_move", "x": 100, "y": 200, "x_norm": 0.1, "y_norm": 0.2},
    )

    first = pointer.translate_local_move(
        node_id="B",
        display_id="1",
        node=target_node,
        bounds=FakeBounds(),
        source_node=source_node,
        source_bounds=FakeBounds(),
        local_event={"kind": "mouse_move", "x": 101, "y": 100, "ts": 1.0},
        display_state=display_state,
    )
    second = pointer.translate_local_move(
        node_id="B",
        display_id="1",
        node=target_node,
        bounds=FakeBounds(),
        source_node=source_node,
        source_bounds=FakeBounds(),
        local_event={"kind": "mouse_move", "x": 101, "y": 100, "ts": 2.0},
        display_state=display_state,
    )

    assert first is None
    assert second is not None
    assert second["x"] == 101
    assert second["y"] == 200
    assert moves == [(100, 100), (100, 100)]


def test_remote_pointer_applies_speed_scale_to_large_move():
    moves = []
    pointer = ActiveRemotePointer(pointer_mover=lambda x, y: moves.append((x, y)))
    display_state = FakeDisplayState(scale=(1.5, 0.5))
    source_node = DummyNode("A")
    target_node = DummyNode("B")

    pointer.begin(
        node_id="B",
        display_id="1",
        source_node_id="A",
        source_display_id="1",
        anchor_local=(100, 100),
        initial_event={"kind": "mouse_move", "x": 100, "y": 200, "x_norm": 0.1, "y_norm": 0.2},
    )

    translated = pointer.translate_local_move(
        node_id="B",
        display_id="1",
        node=target_node,
        bounds=FakeBounds(),
        source_node=source_node,
        source_bounds=FakeBounds(),
        local_event={"kind": "mouse_move", "x": 104, "y": 98, "ts": 1.0},
        display_state=display_state,
    )

    assert translated is not None
    assert translated["x"] == 106
    assert translated["y"] == 199
    assert moves == [(100, 100)]
