"""Internal watchdog companion for cursor/input recovery."""

from __future__ import annotations

import argparse

from platform.windows.clip_recovery import release_input_guards, wait_for_parent_exit


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Watch the main process and release recovery guards when it exits.")
    parser.add_argument("--watch-parent", type=int, required=True, help="Parent PID to watch.")
    parser.add_argument("--poll-interval", type=float, default=0.25, help="Polling interval while watching the parent.")
    parser.add_argument("--quiet", action="store_true", help="Accepted for parity with detached watchdog launches.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    wait_for_parent_exit(
        args.watch_parent,
        poll_interval=args.poll_interval,
        on_parent_exit=release_input_guards,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
