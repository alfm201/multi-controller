"""Detached helper that waits for the app to exit, runs the installer, and relaunches the app."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.update.app_update import run_update_handoff  # noqa: E402


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
