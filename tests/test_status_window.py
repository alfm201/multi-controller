"""Tests for runtime/status_window.py."""

from runtime.context import NodeInfo, RuntimeContext
from runtime.status_window import (
    build_advanced_peer_text,
    build_connection_summary_text,
    build_layout_editor_hint,
    build_layout_node_label,
    build_peer_summary_text,
    build_primary_status_text,
    build_selection_hint_text,
    build_status_view,
    build_target_button_text,
    format_monitor_grid_text,
    parse_monitor_grid_text,
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


def test_build_status_view_includes_runtime_fields():
    ctx = _ctx()
    registry = FakeRegistry([("B", FakeConn()), ("C", FakeConn(closed=True))])
    router = FakeRouter("active", "B")
    sink = FakeSink("B")

    view = build_status_view(ctx, registry, coordinator_resolver=lambda: ctx.get_node("A"), router=router, sink=sink)

    assert view.self_id == "A"
    assert view.coordinator_id == "A"
    assert view.online_peers == ("B",)
    assert view.connected_peer_count == 1
    assert view.total_peer_count == 2
    assert view.router_state == "active"
    assert view.selected_target == "B"
    assert view.authorized_controller == "B"
    assert view.config_path is None
    assert {peer.node_id for peer in view.peers} == {"B", "C"}


def test_build_status_view_marks_target_state_and_online_status():
    ctx = _ctx()
    registry = FakeRegistry([("B", FakeConn()), ("C", FakeConn(closed=True))])
    view = build_status_view(ctx, registry, coordinator_resolver=lambda: ctx.get_node("A"), router=FakeRouter("pending", "C"))
    targets = {target.node_id: target for target in view.targets}
    assert targets["B"].online is True
    assert targets["C"].online is False
    assert targets["C"].selected is True
    assert targets["C"].state == "pending"


def test_build_status_view_marks_peer_flags():
    ctx = _ctx()
    registry = FakeRegistry([("B", FakeConn()), ("C", FakeConn(closed=True))])
    view = build_status_view(ctx, registry, coordinator_resolver=lambda: ctx.get_node("C"), sink=FakeSink("B"))
    peers = {peer.node_id: peer for peer in view.peers}
    assert peers["B"].roles == ("controller", "target")
    assert peers["B"].online is True
    assert peers["B"].is_authorized_controller is True
    assert peers["C"].is_coordinator is True


def test_primary_status_text_prefers_active_target_message():
    ctx = _ctx()
    view = build_status_view(ctx, FakeRegistry([("B", FakeConn())]), coordinator_resolver=lambda: ctx.get_node("A"), router=FakeRouter("active", "B"))
    assert build_primary_status_text(view) == "B PC를 제어 중입니다."
    assert build_connection_summary_text(view) == "연결된 PC 1 / 2"
    assert build_selection_hint_text(view) == "마우스와 키보드 입력은 현재 선택된 PC로 전달됩니다."


def test_primary_status_text_handles_no_connected_peers():
    ctx = _ctx()
    view = build_status_view(ctx, FakeRegistry([]), coordinator_resolver=lambda: ctx.get_node("A"), router=FakeRouter("inactive", None))
    assert build_primary_status_text(view) == "연결된 PC를 찾는 중입니다."
    assert build_selection_hint_text(view) == "네트워크와 대상 PC 실행 상태를 확인해 주세요."


def test_target_button_and_peer_text_split_user_and_advanced_detail():
    target = type("Target", (), {"node_id": "B", "online": True, "selected": True, "state": "pending"})()
    peer = type("Peer", (), {"node_id": "B", "roles": ("controller", "target"), "online": True, "is_coordinator": True, "is_authorized_controller": True})()
    assert build_target_button_text(target) == "B | 연결됨 | 연결 중"
    assert build_peer_summary_text(peer) == "B | 연결됨 | 현재 제어 권한 보유"
    assert build_advanced_peer_text(peer) == "B | controller/target | connected | coordinator | lease-holder"


def test_layout_editor_helper_texts_reflect_locking_and_node_state():
    assert build_layout_editor_hint(True, False, "A", "A", pending=False) == "편집 모드: 켜짐 | 자동 전환: 꺼짐 | 내 변경이 바로 반영됩니다"
    assert build_layout_editor_hint(False, True, "B", "A", pending=False) == "편집 모드: 잠김 (B) | 자동 전환: 켜짐 | B PC가 편집 중입니다"
    assert build_layout_editor_hint(False, True, None, "A", pending=True) == "편집 모드: 요청 중 | 자동 전환: 켜짐 | 변경사항은 바로 반영됩니다"
    assert build_layout_node_label("A", is_self=True, is_online=True, is_selected=True, state="active") == "A\n내 PC"
    assert build_layout_node_label("B", is_self=False, is_online=True, is_selected=True, state="pending") == "B\n연결 중"


def test_monitor_grid_text_round_trip():
    rows = [["1", "2", None], ["3", ".", "4"]]
    text = format_monitor_grid_text(rows)
    assert text == "1 2 .\n3 . 4"
    assert parse_monitor_grid_text(text) == [["1", "2", None], ["3", None, "4"]]
