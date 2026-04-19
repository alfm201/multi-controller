"""Tests for control/state/status_projection.py."""

from datetime import datetime, timedelta

from app.update.app_version import get_current_version
from control.state.context import NodeInfo, RuntimeContext, build_runtime_context
from model.display.monitor_inventory import MonitorBounds, MonitorInventoryItem, MonitorInventorySnapshot
import control.state.status_projection as status_view_module
from control.state.status_projection import (
    build_advanced_peer_text,
    build_connection_summary_text,
    build_layout_editor_hint,
    build_layout_lock_text,
    build_layout_node_label,
    build_peer_summary_text,
    build_primary_status_text,
    build_selected_node_text,
    build_selection_hint_text,
    build_status_view,
    build_target_button_text,
    build_viewport_summary,
)


def _next_version(version: str) -> str:
    parts = [int(part) for part in version.split(".")]
    parts[-1] += 1
    return ".".join(str(part) for part in parts)


class FakeConn:
    def __init__(
        self,
        closed=False,
        *,
        peer_app_version=None,
        peer_compatibility_version=None,
    ):
        self.closed = closed
        self.peer_app_version = peer_app_version
        self.peer_compatibility_version = peer_compatibility_version


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


def test_build_status_view_includes_runtime_fields():
    ctx = _ctx()
    registry = FakeRegistry([("B", FakeConn()), ("C", FakeConn(closed=True))])
    router = FakeRouter("active", "B")
    sink = FakeSink("B")

    view = build_status_view(
        ctx,
        registry,
        coordinator_resolver=lambda: ctx.get_node("A"),
        router=router,
        sink=sink,
    )

    assert view.self_id == "A"
    assert view.coordinator_id == "A"
    assert view.online_peers == ("B",)
    assert view.connected_peer_count == 2
    assert view.total_peer_count == 3
    assert view.summary_cards[1].value == "2 / 3"
    assert view.router_state == "active"
    assert view.selected_target == "B"
    assert view.authorized_controller == "B"
    assert view.config_path is None
    assert {peer.node_id for peer in view.peers} == {"B", "C"}


def test_build_status_view_exposes_detected_vs_saved_detail():
    ctx = _layout_ctx()
    ctx.replace_monitor_inventory(
        MonitorInventorySnapshot(
            node_id="B",
            monitors=(
                MonitorInventoryItem("1", "Display 1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
                MonitorInventoryItem("2", "Display 2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
            ),
            captured_at="10:00:01",
        )
    )
    now = datetime.now()
    view = build_status_view(
        ctx,
        FakeRegistry([("B", FakeConn())]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        router=FakeRouter("active", "B"),
        sink=FakeSink("B"),
        last_seen={"A": now - timedelta(seconds=5), "B": now},
    )

    assert [card.title for card in view.summary_cards] == [
        "현재 대상",
        "연결 상태",
        "코디네이터",
    ]
    assert view.selected_detail.node_id == "B"
    assert any(field.label == "실제 감지 모니터" and field.value == "2" for field in view.selected_detail.fields)
    assert any(field.label == "최근 감지" and field.value == "10:00:01" for field in view.selected_detail.fields)
    assert any(field.label == "감지 상태" for field in view.selected_detail.fields)
    assert any(field.label == "감지/저장 차이" for field in view.selected_detail.fields)
    assert any(field.label == "모니터 배치" for field in view.selected_detail.fields)
    assert [badge.text for badge in view.selected_detail.badges] == ["연결됨"]
    assert view.monitor_alert is None


    assert any(field.label == "최근 연결" and field.value == "0초 전" for field in view.selected_detail.fields)
    peer_b = next(peer for peer in view.peers if peer.node_id == "B")
    assert peer_b.last_seen == "0초 전"


def test_build_status_view_hides_monitor_alert_even_when_diff_exists():
    ctx = _layout_ctx()
    ctx.replace_monitor_inventory(
        MonitorInventorySnapshot(
            node_id="B",
            monitors=(
                MonitorInventoryItem("1", "Display 1", MonitorBounds(0, 0, 1920, 1080), logical_order=0),
                MonitorInventoryItem("2", "Display 2", MonitorBounds(1920, 0, 1920, 1080), logical_order=1),
            ),
            captured_at="10:00:00",
        )
    )
    now = datetime.now()

    view = build_status_view(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        last_seen={"A": now, "B": now - timedelta(minutes=30)},
    )

    assert view.monitor_alert is None
    peer_b = next(peer for peer in view.peers if peer.node_id == "B")
    assert peer_b.has_monitor_diff is True
    assert peer_b.freshness_label == "오프라인"


def test_build_status_view_tracks_peer_version_compatibility():
    ctx = _ctx()
    view = build_status_view(
        ctx,
        FakeRegistry(
            [
                (
                    "B",
                    FakeConn(
                        peer_app_version="0.3.17",
                        peer_compatibility_version="0.3.17",
                    ),
                )
            ]
        ),
        coordinator_resolver=lambda: ctx.get_node("A"),
    )

    peer_b = next(peer for peer in view.peers if peer.node_id == "B")

    assert peer_b.current_version_label == "v0.3.17"
    assert peer_b.version_status == "outdated"
    assert peer_b.is_version_compatible is False
    assert "오래된 버전" in peer_b.version_tooltip


def test_build_status_view_marks_newer_peer_as_ahead():
    ctx = _ctx()
    newer_version = _next_version(get_current_version())
    view = build_status_view(
        ctx,
        FakeRegistry(
            [
                (
                        "B",
                        FakeConn(
                            peer_app_version=newer_version,
                            peer_compatibility_version=newer_version,
                        ),
                    )
                ]
            ),
        coordinator_resolver=lambda: ctx.get_node("A"),
    )

    peer_b = next(peer for peer in view.peers if peer.node_id == "B")

    assert peer_b.version_status == "ahead"
    assert peer_b.is_version_compatible is False
    assert "더 최신 버전" in peer_b.version_tooltip


def test_build_status_view_uses_cached_version_for_offline_peer(monkeypatch):
    ctx = _ctx()
    now = datetime(2026, 1, 1, 10, 5, 0)
    last_seen = datetime(2026, 1, 1, 10, 4, 30)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is None else now.astimezone(tz)

    monkeypatch.setattr(status_view_module, "datetime", FrozenDateTime)
    view = build_status_view(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        last_seen={"B": last_seen},
        version_cache={"B": ("0.3.17", "0.3.17")},
    )

    peer_b = next(peer for peer in view.peers if peer.node_id == "B")

    assert peer_b.online is False
    assert peer_b.current_version_label == "v0.3.17"
    assert peer_b.last_seen == "30초 전"


def test_summary_card_details_use_friendlier_overview_labels():
    ctx = _ctx()
    view = build_status_view(
        ctx,
        FakeRegistry([("B", FakeConn())]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        router=FakeRouter("active", "B"),
        sink=FakeSink("B"),
    )

    assert view.summary_cards[0].detail == "현재 제어 중인 대상 PC입니다."
    assert view.summary_cards[1].detail == "현재 노드 그룹에 연결된 PC 수입니다."
    assert view.summary_cards[2].detail == "현재 노드 그룹에서 입력 전환과 상태 동기화를 조율하는 PC입니다."


def test_primary_status_text_prefers_active_target_message():
    ctx = _ctx()
    view = build_status_view(
        ctx,
        FakeRegistry([("B", FakeConn())]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        router=FakeRouter("active", "B"),
    )
    assert build_primary_status_text(view) == "B(127.0.0.1) PC가 현재 제어 대상입니다."
    assert build_connection_summary_text(view) == "연결된 PC 2 / 3"
    assert build_selection_hint_text(view) == ""


def test_primary_status_text_handles_no_connected_peers():
    ctx = _ctx()
    view = build_status_view(
        ctx,
        FakeRegistry([]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        router=FakeRouter("inactive", None),
    )
    assert build_primary_status_text(view) == "다른 PC 연결을 기다리는 중입니다."
    assert build_selection_hint_text(view) == ""


def test_pending_target_is_hidden_from_status_view_until_active():
    ctx = _ctx()
    view = build_status_view(
        ctx,
        FakeRegistry([("B", FakeConn())]),
        coordinator_resolver=lambda: ctx.get_node("A"),
        router=FakeRouter("pending", "B"),
    )
    assert view.router_state is None
    assert view.selected_target is None
    assert build_primary_status_text(view) == ""


def test_target_and_peer_texts_expose_user_and_advanced_detail():
    target = type(
        "Target",
        (),
        {"node_id": "B", "label": "B(127.0.0.1)", "online": True, "selected": True, "state": None},
    )()
    peer = type(
        "Peer",
        (),
        {
            "node_id": "B",
            "label": "B(127.0.0.1)",
            "online": True,
            "is_coordinator": True,
            "is_authorized_controller": True,
            "detection_summary": "실제 감지 기준",
        },
    )()
    assert build_target_button_text(target) == "B(127.0.0.1) | 연결됨 | 준비됨"
    assert build_peer_summary_text(peer) == "B(127.0.0.1) | 연결됨 | 제어권 보유"
    assert build_advanced_peer_text(peer) == "B(127.0.0.1) | 연결됨 | 실제 감지 기준 | 코디네이터 | 제어권 보유"


def test_layout_helpers_reflect_lock_state_and_selection_detail():
    assert build_layout_editor_hint(True, "A", "A", pending=False) == "편집 모드: 켜짐 | 빈 공간 또는 오른쪽 버튼 드래그로 화면을 이동하세요"
    assert build_layout_editor_hint(False, "B", "A", pending=False) == "편집 모드: B PC가 사용 중 | B PC가 현재 편집 중입니다"
    assert build_layout_editor_hint(False, None, "A", pending=True) == "편집 모드: 대기 중 | 선택한 PC의 모니터 맵을 수정하세요"
    assert build_layout_lock_text("A", "A", pending=False) == "편집 잠금: 내 편집"
    assert build_layout_lock_text("B", "A", pending=False) == "편집 잠금: B 사용 중"
    assert build_layout_node_label("A(127.0.0.1)", is_self=True, is_online=True, is_selected=True, state="active") == "A(127.0.0.1)\n내 PC"
    assert build_layout_node_label("B(127.0.0.1)", is_self=False, is_online=True, is_selected=True, state=None) == "B(127.0.0.1)\n연결됨"

    ctx = _layout_ctx()
    assert build_selected_node_text(ctx.layout.get_node("B")) == "선택된 PC: B | 모니터 감지 대기"
    assert build_viewport_summary(1.2, 10.4, -20.2) == "보기: 120% | 이동 (10, -20)"
