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
