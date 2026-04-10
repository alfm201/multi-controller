"""Tests for runtime/monitor_inventory.py."""

from runtime.monitor_inventory import (
    MonitorBounds,
    MonitorInventoryItem,
    MonitorInventorySnapshot,
    merge_detected_and_physical_override,
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
