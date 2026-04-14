"""Detached helper that waits for the app to exit, runs the installer, and relaunches the app."""

from __future__ import annotations

import argparse
import logging

from runtime.app_update import run_update_handoff


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Install a prepared Multi Screen Pass update and relaunch the app."
    )
    parser.add_argument("--manifest", required=True, help="Path to a prepared update handoff manifest.")
    args = parser.parse_args(argv)
    return run_update_handoff(args.manifest)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - defensive logging for detached runs
        logging.exception("[UPDATE] detached updater failed: %s", exc)
        raise
