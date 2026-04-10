"""Read-only monitor inventory models for future automatic detection."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MonitorBounds:
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class MonitorInventoryItem:
    monitor_id: str
    display_name: str
    bounds: MonitorBounds
    is_primary: bool = False
    dpi_scale: float = 1.0
    logical_order: int = 0


@dataclass(frozen=True)
class MonitorInventorySnapshot:
    node_id: str
    monitors: tuple[MonitorInventoryItem, ...]

    def ordered(self) -> tuple[MonitorInventoryItem, ...]:
        return tuple(sorted(self.monitors, key=lambda item: (item.logical_order, item.monitor_id)))

    def monitor_ids(self) -> tuple[str, ...]:
        return tuple(item.monitor_id for item in self.ordered())


def merge_detected_and_physical_override(
    detected: MonitorInventorySnapshot,
    physical_rows: tuple[tuple[str | None, ...], ...] | None,
) -> dict:
    """Prepare a future merge payload without changing runtime behavior yet."""
    return {
        "node_id": detected.node_id,
        "logical_monitors": [
            {
                "monitor_id": item.monitor_id,
                "display_name": item.display_name,
                "bounds": {
                    "left": item.bounds.left,
                    "top": item.bounds.top,
                    "width": item.bounds.width,
                    "height": item.bounds.height,
                },
                "is_primary": item.is_primary,
                "dpi_scale": item.dpi_scale,
                "logical_order": item.logical_order,
            }
            for item in detected.ordered()
        ],
        "physical_override": [] if physical_rows is None else [list(row) for row in physical_rows],
    }
