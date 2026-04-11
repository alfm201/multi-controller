"""Reproduce self-node auto-switch edge behavior against the current config."""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from routing.auto_switch import AutoTargetSwitcher
from runtime.config_loader import load_config
from runtime.context import build_runtime_context
from runtime.display import ScreenBounds, normalize_position


@dataclass
class RecordedMove:
    x: int
    y: int


class _RouterStub:
    def get_selected_target(self):
        return None

    def prepare_pointer_handoff(self, _anchor_event):
        return None


class _Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, delta: float) -> None:
        self.value += delta


def _event(x: int, y: int, bounds: ScreenBounds) -> dict:
    x_norm, y_norm = normalize_position(x, y, bounds)
    return {
        "kind": "mouse_move",
        "x": int(x),
        "y": int(y),
        "x_norm": x_norm,
        "y_norm": y_norm,
    }


def _find_item(snapshot, monitor_id: str):
    for item in snapshot.monitors:
        if item.monitor_id == monitor_id:
            return item
    raise KeyError(monitor_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/config.json")
    parser.add_argument("--node-name", default="A")
    parser.add_argument("--iterations", type=int, default=10)
    args = parser.parse_args()

    config, config_path = load_config(args.config)
    ctx = build_runtime_context(config, override_name=args.node_name, config_path=config_path)
    node = ctx.layout.get_node(ctx.self_node.node_id)
    snapshot = ctx.get_monitor_inventory(ctx.self_node.node_id)
    if node is None or snapshot is None or len(snapshot.monitors) < 2:
        raise SystemExit("self node or monitor inventory is missing")

    bounds = ScreenBounds(
        left=min(item.bounds.left for item in snapshot.monitors),
        top=min(item.bounds.top for item in snapshot.monitors),
        width=max(item.bounds.left + item.bounds.width for item in snapshot.monitors)
        - min(item.bounds.left for item in snapshot.monitors),
        height=max(item.bounds.top + item.bounds.height for item in snapshot.monitors)
        - min(item.bounds.top for item in snapshot.monitors),
    )

    print(f"self node: {ctx.self_node.node_id}")
    print(
        "inventory:",
        [
            (
                item.monitor_id,
                item.bounds.left,
                item.bounds.top,
                item.bounds.width,
                item.bounds.height,
            )
            for item in snapshot.monitors
        ],
    )
    print(
        "logical monitors:",
        [(display.display_id, display.x, display.y) for display in node.monitors().logical],
    )
    print(
        "physical monitors:",
        [(display.display_id, display.x, display.y) for display in node.monitors().physical],
    )

    display1 = _find_item(snapshot, r"\\.\DISPLAY1")
    display2 = _find_item(snapshot, r"\\.\DISPLAY2")

    clock = _Clock()
    moves: list[RecordedMove] = []
    switcher = AutoTargetSwitcher(
        ctx,
        _RouterStub(),
        request_target=lambda _node_id: None,
        clear_target=lambda: None,
        is_target_online=lambda _node_id: False,
        pointer_mover=lambda x, y: moves.append(RecordedMove(int(x), int(y))),
        screen_bounds_provider=lambda: bounds,
        now_fn=clock,
    )

    print("\n[scenario 1] dead-edge repeated block on DISPLAY1 left edge")
    dead_edge_event = _event(display1.bounds.left, display1.bounds.top + display1.bounds.height // 2, bounds)
    dead_edge_results = []
    for _ in range(args.iterations):
        moves.clear()
        result = switcher.process(dict(dead_edge_event))
        dead_edge_results.append((result is None, moves[-1] if moves else None))
        clock.advance(0.1)
    for index, (consumed, move) in enumerate(dead_edge_results, start=1):
        print(f"  hit {index:02d}: consumed={consumed} move={move}")

    print("\n[scenario 2] internal warp sweep from DISPLAY1 right edge")
    warp_results = []
    for probe_y in (
        display1.bounds.top + 20,
        display1.bounds.top + 100,
        display1.bounds.top + display1.bounds.height // 2,
        display1.bounds.top + display1.bounds.height - 100,
        display1.bounds.top + display1.bounds.height - 20,
    ):
        moves.clear()
        result = switcher.process(_event(display1.bounds.left + display1.bounds.width - 1, probe_y, bounds))
        warp_results.append((probe_y, result is None, moves[-1] if moves else None))
        clock.advance(0.2)
    for probe_y, consumed, move in warp_results:
        print(f"  y={probe_y}: consumed={consumed} move={move}")

    reproduced_dead_edge = any((not consumed) or move is None for consumed, move in dead_edge_results)
    reproduced_internal_warp = False
    for probe_y, consumed, move in warp_results:
        if not consumed or move is None:
            reproduced_internal_warp = True
            continue
        if move.x != display2.bounds.left + 1:
            reproduced_internal_warp = True
            continue
        expected_ratio = (probe_y - display1.bounds.top) / max(display1.bounds.height - 1, 1)
        expected_y = display2.bounds.top + round(
            expected_ratio * max(display2.bounds.height - 1, 0)
        )
        if abs(move.y - expected_y) > 1:
            reproduced_internal_warp = True

    print("\n[result]")
    print(f"  dead-edge bug reproduced: {reproduced_dead_edge}")
    print(f"  internal-warp bug reproduced: {reproduced_internal_warp}")
    return 1 if (reproduced_dead_edge or reproduced_internal_warp) else 0


if __name__ == "__main__":
    raise SystemExit(main())
