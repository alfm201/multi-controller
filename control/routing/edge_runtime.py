"""Runtime context objects shared across edge detection/routing/execution."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AutoSwitchFrame:
    """Resolved runtime state for one mouse-move evaluation."""

    layout: object
    current_node_id: str
    current_node: object
    current_display_id: str
    bounds: object
    now: float


@dataclass(frozen=True)
class EdgeTransition:
    """One concrete edge press being routed/executed."""

    frame: AutoSwitchFrame
    direction: str
    cross_ratio: float
    event: dict
