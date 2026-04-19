"""CLI 인자와 시작 모드 해석을 담당한다."""

from __future__ import annotations

import argparse


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="multi-controller: shared keyboard and mouse control"
    )
    parser.add_argument(
        "--config",
        help="Path to config/config.json. Defaults to bundled/project/CWD discovery with legacy root fallback.",
    )
    parser.add_argument(
        "--init-config",
        action="store_true",
        help="Create a starter split config and exit.",
    )
    parser.add_argument(
        "--migrate-config",
        action="store_true",
        help="Load the current config and rewrite it into the split config/ structure.",
    )
    parser.add_argument(
        "--validate-config",
        action="store_true",
        help="Load and validate the current config, then print the resolved file layout.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow init/migrate commands to overwrite existing files.",
    )
    parser.add_argument(
        "--active-target",
        help="Set an initial target at startup.",
    )
    parser.add_argument(
        "--status-interval",
        type=float,
        default=10.0,
        help="Seconds between periodic status logs. Use 0 to disable.",
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="Print local Windows privilege/display diagnostics and exit.",
    )
    parser.add_argument(
        "--layout-diagnostics",
        action="store_true",
        help="Print resolved PC layout, monitor topology, and auto-switch diagnostics and exit.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging for troubleshooting.",
    )
    ui_group = parser.add_mutually_exclusive_group()
    ui_group.add_argument(
        "--console",
        action="store_true",
        help="Run without the status window. Use this for log-first or console-only operation.",
    )
    ui_group.add_argument(
        "--tray",
        action="store_true",
        help="Run a system tray icon for coordinator/target state and quick actions.",
    )
    return parser.parse_args(argv)


def validate_startup_args(ctx, active_target):
    if not active_target:
        return
    target = ctx.get_node(active_target)
    if target is None:
        raise ValueError(f"--active-target '{active_target}' is not defined in config.nodes")
    if target.node_id == ctx.self_node.node_id:
        raise ValueError("--active-target cannot point to self")


def resolve_ui_mode(args):
    """실행 인자에 따라 사용할 UI 모드를 결정한다."""
    if args.tray:
        return "tray"
    if args.console:
        return "console"
    return "gui"
