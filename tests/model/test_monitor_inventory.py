"""Tests for model/display/monitor_inventory.py."""

from datetime import datetime

from model.display.monitor_inventory import (
    compare_detected_and_physical_rows,
    describe_monitor_freshness,
    deserialize_monitor_inventory_snapshot,
    MonitorBounds,
    MonitorInventoryItem,
    MonitorInventorySnapshot,
    merge_detected_and_physical_override,
    serialize_monitor_inventory_snapshot,
    snapshot_to_logical_rows,
)


def test_snapshot_orders_monitors_by_logical_order():
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("b", "Display B", MonitorBounds(0, 0, 100, 100), logical_order=2),
            MonitorInventoryItem("a", "Display A", MonitorBounds(100, 0, 100, 100), logical_order=1),
        ),
    )

    assert snapshot.monitor_ids() == ("a", "b")


def test_merge_detected_and_physical_override_preserves_override_rows():
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "Display 1", MonitorBounds(0, 0, 100, 100), is_primary=True),
        ),
    )

    merged = merge_detected_and_physical_override(snapshot, (("1", None),))

    assert merged["node_id"] == "A"
    assert merged["logical_monitors"][0]["monitor_id"] == "1"
    assert merged["physical_override"] == [["1", None]]


def test_snapshot_serialization_round_trip():
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "Display 1", MonitorBounds(0, 0, 100, 100), is_primary=True),
        ),
        captured_at="10:00:00",
    )

    restored = deserialize_monitor_inventory_snapshot(
        serialize_monitor_inventory_snapshot(snapshot)
    )

    assert restored.node_id == "A"
    assert restored.captured_at == "10:00:00"
    assert restored.monitors[0].display_name == "Display 1"


def test_snapshot_to_logical_rows_uses_display_positions():
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "Display 1", MonitorBounds(0, 0, 100, 100), logical_order=0),
            MonitorInventoryItem("2", "Display 2", MonitorBounds(100, 0, 100, 100), logical_order=1),
            MonitorInventoryItem("3", "Display 3", MonitorBounds(0, 100, 100, 100), logical_order=2),
        ),
    )

    assert snapshot_to_logical_rows(snapshot) == [["1", "2"], ["3", None]]


def test_describe_monitor_freshness_marks_recent_snapshot_as_fresh():
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "Display 1", MonitorBounds(0, 0, 100, 100), logical_order=0),
        ),
        captured_at="10:00:00",
    )

    freshness = describe_monitor_freshness(
        snapshot,
        online=True,
        now=datetime.strptime("10:04:00", "%H:%M:%S"),
    )

    assert freshness.label == "최신"
    assert freshness.is_stale is False


def test_compare_detected_and_physical_rows_reports_moved_monitors():
    diff = compare_detected_and_physical_rows(
        [["1", "2"]],
        [["2", "1"]],
    )

    assert diff.has_difference is True
    assert diff.moved_ids == ("1", "2")


def test_describe_monitor_freshness_accepts_iso_timestamp():
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "Display 1", MonitorBounds(0, 0, 100, 100), logical_order=0),
        ),
        captured_at="2026-04-15T10:00:00Z",
    )

    freshness = describe_monitor_freshness(
        snapshot,
        online=True,
        now=datetime.strptime("2026-04-15T10:04:00", "%Y-%m-%dT%H:%M:%S"),
    )

    assert freshness.is_stale is False


def test_describe_monitor_freshness_suppresses_stale_alert_for_recently_seen_online_node():
    snapshot = MonitorInventorySnapshot(
        node_id="A",
        monitors=(
            MonitorInventoryItem("1", "Display 1", MonitorBounds(0, 0, 100, 100), logical_order=0),
        ),
        captured_at="10:00:00",
    )
    now = datetime.strptime("10:20:00", "%H:%M:%S")

    freshness = describe_monitor_freshness(
        snapshot,
        online=True,
        now=now,
        last_seen_at=now,
    )

    assert freshness.label == "연결 중"
    assert freshness.is_stale is False
