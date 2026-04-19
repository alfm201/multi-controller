"""Tests for control/state/state_watcher.py."""

from control.state.context import NodeInfo, RuntimeContext
from control.state.state_watcher import (
    RuntimeState,
    StateWatcher,
    collect_runtime_state,
    describe_state_changes,
)


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


class FakeSink:
    def __init__(self, controller_id):
        self._controller_id = controller_id

    def get_authorized_controller(self):
        return self._controller_id


def _ctx():
    nodes = [
        NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000}),
        NodeInfo.from_dict({"name": "B", "ip": "127.0.0.1", "port": 5001}),
        NodeInfo.from_dict({"name": "C", "ip": "127.0.0.1", "port": 5002}),
    ]
    return RuntimeContext(self_node=nodes[0], nodes=nodes)


def test_collect_runtime_state_reads_core_values():
    ctx = _ctx()
    registry = FakeRegistry([("B", FakeConn()), ("C", FakeConn(closed=True))])
    router = FakeRouter("pending", "B")
    sink = FakeSink("B")

    state = collect_runtime_state(
        ctx,
        registry,
        coordinator_resolver=lambda: ctx.get_node("A"),
        router=router,
        sink=sink,
    )

    assert state.coordinator_id == "A"
    assert state.online_peers == ("B",)
    assert state.router_state == "pending"
    assert state.requested_target == "B"
    assert state.active_target is None
    assert state.authorized_controller == "B"


def test_describe_state_changes_reports_all_major_transitions():
    previous = RuntimeState(
        coordinator_id="A",
        online_peers=("B",),
        router_state="pending",
        requested_target="B",
        active_target=None,
        authorized_controller="B",
    )
    current = RuntimeState(
        coordinator_id="C",
        online_peers=("C",),
        router_state="active",
        requested_target="C",
        active_target="C",
        authorized_controller="C",
    )

    messages = describe_state_changes(previous, current)

    assert "[EVENT COORDINATOR] A -> C" in messages
    assert "[EVENT ONLINE] joined=['C'] left=['B'] now=['C']" in messages
    assert "[EVENT ROUTER] pending:req=B,active=None -> active:req=C,active=C" in messages
    assert "[EVENT LEASE] B -> C" in messages


def test_describe_state_changes_is_empty_without_previous_state():
    current = RuntimeState(
        coordinator_id="A",
        online_peers=("B",),
        router_state="inactive",
        requested_target=None,
        active_target=None,
        authorized_controller=None,
    )

    assert describe_state_changes(None, current) == []


def test_state_watcher_detects_self_ip_change(monkeypatch):
    ctx = _ctx()
    registry = FakeRegistry([])
    changes = []
    watcher = StateWatcher(
        ctx,
        registry,
        coordinator_resolver=lambda: ctx.get_node("A"),
        self_ip_change_callback=lambda old_ip, new_ip, details=None: changes.append((old_ip, new_ip, details)),
    )

    monkeypatch.setattr("control.state.state_watcher.get_local_ips", lambda: {"127.0.0.1", "192.168.0.10"})

    watcher._detect_self_ip_change()

    assert changes == [
        (
            "127.0.0.1",
            "192.168.0.10",
            {
                "local_ips": ("127.0.0.1", "192.168.0.10"),
                "state": None,
                "ambiguous": False,
            },
        )
    ]


def test_state_watcher_ignores_ambiguous_multiple_non_loopback_ips(monkeypatch):
    ctx = _ctx()
    registry = FakeRegistry([])
    changes = []
    watcher = StateWatcher(
        ctx,
        registry,
        coordinator_resolver=lambda: ctx.get_node("A"),
        self_ip_change_callback=lambda old_ip, new_ip, details=None: changes.append((old_ip, new_ip, details)),
    )

    monkeypatch.setattr(
        "control.state.state_watcher.get_local_ips",
        lambda: {"192.168.0.10", "10.0.0.5", "127.0.0.1"},
    )

    watcher._detect_self_ip_change()

    assert changes == []


def test_state_watcher_picks_single_new_non_loopback_candidate(monkeypatch):
    ctx = _ctx()
    registry = FakeRegistry([])
    changes = []
    watcher = StateWatcher(
        ctx,
        registry,
        coordinator_resolver=lambda: ctx.get_node("A"),
        self_ip_change_callback=lambda old_ip, new_ip, details=None: changes.append((old_ip, new_ip, details)),
    )
    watcher._last_self_ip = "192.168.0.10"
    watcher._last_local_ips = {"127.0.0.1", "192.168.0.10", "10.0.0.5"}

    monkeypatch.setattr(
        "control.state.state_watcher.get_local_ips",
        lambda: {"127.0.0.1", "10.0.0.5", "10.0.0.9"},
    )

    watcher._detect_self_ip_change()

    assert changes == [
        (
            "192.168.0.10",
            "10.0.0.9",
            {
                "local_ips": ("10.0.0.5", "10.0.0.9", "127.0.0.1"),
                "state": None,
                "ambiguous": False,
            },
        )
    ]


def test_state_watcher_reports_ambiguous_self_ip_change(monkeypatch):
    ctx = _ctx()
    registry = FakeRegistry([])
    changes = []
    watcher = StateWatcher(
        ctx,
        registry,
        coordinator_resolver=lambda: ctx.get_node("A"),
        self_ip_change_callback=lambda old_ip, new_ip, details=None: changes.append((old_ip, new_ip, details)),
    )
    watcher._last_self_ip = "192.168.0.10"
    watcher._last_local_ips = {"127.0.0.1", "192.168.0.10"}

    monkeypatch.setattr(
        "control.state.state_watcher.get_local_ips",
        lambda: {"127.0.0.1", "10.0.0.5", "172.16.0.7"},
    )

    watcher._detect_self_ip_change()

    assert changes == [
        (
            "192.168.0.10",
            "",
            {
                "local_ips": ("10.0.0.5", "127.0.0.1", "172.16.0.7"),
                "state": None,
                "ambiguous": True,
            },
        )
    ]
