"""Tests for capture/hotkey.py::TargetCycler.

Mocks RuntimeContext.peers and InputRouter. No pynput, no sockets, no threads.
"""

from capture.hotkey import TargetCycler


class FakeNode:
    def __init__(self, name, roles=("controller", "target")):
        self.name = name
        self.node_id = name
        self.roles = roles

    def has_role(self, role):
        return role in self.roles


class FakeCtx:
    def __init__(self, peers):
        self.peers = peers


class FakeRouter:
    def __init__(self):
        self._active = None

    def get_active_target(self):
        return self._active

    def set_active_target(self, node_id):
        self._active = node_id


class FakeCoordClient:
    def __init__(self):
        self.claims = []

    def claim(self, target_id):
        self.claims.append(target_id)
        return True


# --------------------------------------------------------------------------- #
# targets()
# --------------------------------------------------------------------------- #

def test_targets_filters_to_target_role_only():
    ctx = FakeCtx([
        FakeNode("A", roles=("controller",)),
        FakeNode("B", roles=("target",)),
        FakeNode("C", roles=("controller", "target")),
    ])
    c = TargetCycler(ctx, FakeRouter())
    assert c.targets() == ["B", "C"]


def test_targets_empty_when_no_target_role():
    ctx = FakeCtx([FakeNode("A", roles=("controller",))])
    c = TargetCycler(ctx, FakeRouter())
    assert c.targets() == []


# --------------------------------------------------------------------------- #
# cycle()
# --------------------------------------------------------------------------- #

def test_cycle_first_call_picks_first_target():
    ctx = FakeCtx([FakeNode("B"), FakeNode("C")])
    router = FakeRouter()
    c = TargetCycler(ctx, router)
    assert c.cycle() == "B"
    assert router.get_active_target() == "B"


def test_cycle_second_call_advances_to_next_target():
    ctx = FakeCtx([FakeNode("B"), FakeNode("C"), FakeNode("D")])
    router = FakeRouter()
    c = TargetCycler(ctx, router)
    c.cycle()
    assert c.cycle() == "C"
    assert c.cycle() == "D"
    assert c.cycle() == "B"  # wraps


def test_cycle_when_current_not_in_list_restarts_at_zero():
    ctx = FakeCtx([FakeNode("B"), FakeNode("C")])
    router = FakeRouter()
    router.set_active_target("stale")
    c = TargetCycler(ctx, router)
    assert c.cycle() == "B"


def test_cycle_no_targets_returns_none():
    ctx = FakeCtx([FakeNode("A", roles=("controller",))])
    router = FakeRouter()
    c = TargetCycler(ctx, router)
    assert c.cycle() is None
    assert router.get_active_target() is None


def test_cycle_single_target_idempotent():
    ctx = FakeCtx([FakeNode("B")])
    router = FakeRouter()
    c = TargetCycler(ctx, router)
    assert c.cycle() == "B"
    assert c.cycle() == "B"
    assert router.get_active_target() == "B"


# --------------------------------------------------------------------------- #
# coordinator integration
# --------------------------------------------------------------------------- #

def test_cycle_sends_claim_when_coord_client_present():
    ctx = FakeCtx([FakeNode("B"), FakeNode("C")])
    router = FakeRouter()
    coord = FakeCoordClient()
    c = TargetCycler(ctx, router, coord_client=coord)
    c.cycle()
    c.cycle()
    assert coord.claims == ["B", "C"]


def test_cycle_claim_failure_does_not_block_local_switch():
    class FailingCoord:
        def claim(self, target_id):
            raise RuntimeError("no connection")

    ctx = FakeCtx([FakeNode("B"), FakeNode("C")])
    router = FakeRouter()
    c = TargetCycler(ctx, router, coord_client=FailingCoord())
    assert c.cycle() == "B"
    assert router.get_active_target() == "B"


def test_cycle_no_claim_when_no_coord_client():
    ctx = FakeCtx([FakeNode("B")])
    router = FakeRouter()
    c = TargetCycler(ctx, router, coord_client=None)
    c.cycle()
    # Just a no-raise check — coverage is via other tests


def test_cycle_same_target_no_reclaim():
    """유일한 target 이 이미 활성이면 claim 재전송하지 않는다."""
    ctx = FakeCtx([FakeNode("B")])
    router = FakeRouter()
    coord = FakeCoordClient()
    c = TargetCycler(ctx, router, coord_client=coord)
    c.cycle()  # B
    c.cycle()  # still B, no new claim
    assert coord.claims == ["B"]
