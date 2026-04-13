"""Tests for capture/hotkey.py::TargetCycler."""

from capture.hotkey import TargetCycler


class FakeNode:
    def __init__(self, name, roles=("controller", "target")):
        self.name = name
        self.node_id = name
        self.roles = roles

    def has_role(self, role):
        return role in ("controller", "target")


class FakeCtx:
    def __init__(self, peers, self_id="A"):
        self.peers = peers
        self.self_node = FakeNode(self_id)


class FakeRouter:
    def __init__(self):
        self._state = "inactive"
        self._target = None

    def get_requested_target(self):
        return self._target

    def get_selected_target(self):
        return self._target

    def set_pending_target(self, node_id):
        self._state = "pending"
        self._target = node_id

    def activate_target(self, node_id):
        self._state = "active"
        self._target = node_id

    def clear_target(self, reason=None):
        self._state = "inactive"
        self._target = None


class FakeCoordClient:
    def __init__(self):
        self.requests = []
        self.clears = 0

    def request_target(self, target_id, source=None):
        self.requests.append((target_id, source))
        return True

    def clear_target(self):
        self.clears += 1


def test_targets_include_all_peers():
    ctx = FakeCtx(
        [
            FakeNode("A", roles=("controller",)),
            FakeNode("B", roles=("target",)),
            FakeNode("C", roles=("controller", "target")),
        ]
    )
    cycler = TargetCycler(ctx, FakeRouter())
    assert cycler.targets() == ["A", "B", "C"]


def test_next_first_call_picks_first_target():
    ctx = FakeCtx([FakeNode("B"), FakeNode("C")])
    router = FakeRouter()
    cycler = TargetCycler(ctx, router)
    assert cycler.next() == "B"
    assert router.get_selected_target() == "B"


def test_next_wraps():
    ctx = FakeCtx([FakeNode("B"), FakeNode("C"), FakeNode("D")])
    router = FakeRouter()
    router.activate_target("D")
    cycler = TargetCycler(ctx, router)
    assert cycler.next() == "A"


def test_previous_first_call_picks_last_target():
    ctx = FakeCtx([FakeNode("B"), FakeNode("C"), FakeNode("D")])
    router = FakeRouter()
    cycler = TargetCycler(ctx, router)
    assert cycler.previous() == "D"


def test_previous_wraps():
    ctx = FakeCtx([FakeNode("B"), FakeNode("C"), FakeNode("D")])
    router = FakeRouter()
    router.activate_target("B")
    cycler = TargetCycler(ctx, router)
    assert cycler.previous() == "A"


def test_cycle_alias_matches_next_behavior():
    ctx = FakeCtx([FakeNode("B"), FakeNode("C")])
    router = FakeRouter()
    cycler = TargetCycler(ctx, router)
    assert cycler.cycle() == "B"
    assert cycler.cycle() == "C"


def test_step_no_targets_returns_none():
    ctx = FakeCtx([])
    router = FakeRouter()
    cycler = TargetCycler(ctx, router)
    assert cycler.next() is None
    assert cycler.previous() is None


def test_next_uses_coordinator_when_present():
    ctx = FakeCtx([FakeNode("B"), FakeNode("C")])
    router = FakeRouter()
    coord = FakeCoordClient()
    cycler = TargetCycler(ctx, router, coord_client=coord)
    cycler.next()
    cycler.next()
    assert coord.requests == [("B", "hotkey"), ("C", "hotkey")]


def test_previous_uses_coordinator_when_present():
    ctx = FakeCtx([FakeNode("B"), FakeNode("C"), FakeNode("D")])
    router = FakeRouter()
    coord = FakeCoordClient()
    cycler = TargetCycler(ctx, router, coord_client=coord)
    cycler.previous()
    cycler.previous()
    assert coord.requests == [("D", "hotkey"), ("C", "hotkey")]


def test_targets_provider_overrides_default_peer_list():
    ctx = FakeCtx([FakeNode("B"), FakeNode("C"), FakeNode("D")])
    cycler = TargetCycler(ctx, FakeRouter(), targets_provider=lambda: ["C"])

    assert cycler.targets() == ["C"]


def test_default_targets_include_self_first():
    ctx = FakeCtx([FakeNode("B"), FakeNode("C")], self_id="A")
    cycler = TargetCycler(ctx, FakeRouter())

    assert cycler.targets() == ["A", "B", "C"]


def test_next_from_self_moves_to_first_remote_target():
    ctx = FakeCtx([FakeNode("B"), FakeNode("C")], self_id="A")
    router = FakeRouter()
    cycler = TargetCycler(ctx, router)

    assert cycler.next() == "B"
    assert router.get_selected_target() == "B"


def test_previous_from_first_remote_target_returns_to_self():
    ctx = FakeCtx([FakeNode("B"), FakeNode("C")], self_id="A")
    router = FakeRouter()
    router.activate_target("B")
    coord = FakeCoordClient()
    cycler = TargetCycler(ctx, router, coord_client=coord)

    assert cycler.previous() == "A"
    assert coord.clears == 1
    assert coord.requests == []


def test_before_select_hook_runs_before_target_request():
    ctx = FakeCtx([FakeNode("B"), FakeNode("C")])
    router = FakeRouter()
    coord = FakeCoordClient()
    selected = []

    cycler = TargetCycler(
        ctx,
        router,
        coord_client=coord,
        before_select=lambda node_id: selected.append(node_id),
    )

    assert cycler.next() == "B"
    assert selected == ["B"]
    assert coord.requests == [("B", "hotkey")]
