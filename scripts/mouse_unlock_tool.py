"""Manual cursor recovery tool and watchdog companion."""

from __future__ import annotations

import argparse
import sys

from runtime.clip_recovery import release_cursor_clip, wait_for_parent_exit


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Release cursor lock or watch a parent process.")
    parser.add_argument("--watch-parent", type=int, help="Parent PID to watch. Release cursor lock when it exits.")
    parser.add_argument("--poll-interval", type=float, default=0.25, help="Polling interval for watchdog mode.")
    parser.add_argument("--quiet", action="store_true", help="Suppress console output.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.watch_parent:
        wait_for_parent_exit(
            args.watch_parent,
            poll_interval=args.poll_interval,
            on_parent_exit=release_cursor_clip,
        )
        return 0

    released = release_cursor_clip()
    if not args.quiet:
        sys.stdout.write("released\n" if released else "release-failed\n")
    return 0 if released else 1


if __name__ == "__main__":
    raise SystemExit(main())
