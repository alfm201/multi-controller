"""Tests for runtime/status_controller.py."""

from runtime.context import build_runtime_context
from runtime.status_controller import StatusController


class FakeConn:
    def __init__(self, closed=False):
        self.closed = closed


class FakeRegistry:
    def __init__(self, pairs):
        self._pairs = pairs

    def all(self):
        return list(self._pairs)


class FakeRouter:
    def __init__(self, state, target):
        self._state = state
        self._target = target

    def get_target_state(self):
        return self._state

    def get_requested_target(self):
        return self._target

    def get_active_target(self):
        if self._state == "active":
            return self._target
        return None

    def get_selected_target(self):
        return self._target


class FakeCoordClient:
    def __init__(self):
        self._is_editor = False
        self._pending = False
        self._editor_id = None
        self._deny_reason = None

    def is_layout_editor(self):
        return self._is_editor

    def is_layout_edit_pending(self):
        return self._pending

    def get_layout_editor(self):
        return self._editor_id

    def get_layout_edit_denial(self):
        return self._deny_reason


def _ctx():
    return build_runtime_context(
        {
            "nodes": [
                {"name": "A", "ip": "127.0.0.1", "port": 5000},
                {"name": "B", "ip": "127.0.0.1", "port": 5001},
            ]
        },
        override_name="A",
        config_path="config/config.json",
    )


def test_controller_emits_summary_once_for_unchanged_state(qtbot):
    ctx = _ctx()
    controller = StatusController(
        ctx,
        FakeRegistry([("B", FakeConn())]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        router=FakeRouter("active", "B"),
        refresh_ms=250,
    )
    summaries = []
    controller.summaryChanged.connect(lambda view: summaries.append(view))

    controller.refresh_now()
    controller.refresh_now()

    assert len(summaries) == 1


def test_controller_emits_selected_node_when_selection_changes(qtbot):
    ctx = _ctx()
    controller = StatusController(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        refresh_ms=250,
    )
    details = []
    controller.selectedNodeChanged.connect(lambda detail: details.append(detail.node_id))

    controller.refresh_now()
    controller.set_selected_node("B")

    assert details[-1] == "B"


def test_controller_emits_layout_when_layout_edit_state_changes(qtbot):
    ctx = _ctx()
    coord_client = FakeCoordClient()
    controller = StatusController(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        coord_client=coord_client,
        refresh_ms=250,
    )
    layouts = []
    controller.layoutChanged.connect(lambda view: layouts.append(view))

    controller.refresh_now()
    coord_client._pending = True
    controller.refresh_now()
    coord_client._pending = False
    coord_client._is_editor = True
    coord_client._editor_id = "A"
    controller.refresh_now()

    assert len(layouts) == 3


def test_controller_keeps_bounded_message_history(qtbot):
    ctx = _ctx()
    controller = StatusController(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        refresh_ms=250,
    )
    histories = []
    controller.messageHistoryChanged.connect(lambda items: histories.append(items))

    for index in range(controller.MAX_MESSAGE_HISTORY + 5):
        controller.set_message(f"message-{index}", "neutral")

    assert len(controller.message_history) == controller.MAX_MESSAGE_HISTORY
    assert controller.message_history[0]["message"] == f"message-{controller.MAX_MESSAGE_HISTORY + 4}"
    assert controller.message_history[-1]["message"] == "message-5"
    assert len(histories[-1]) == controller.MAX_MESSAGE_HISTORY
