"""Manual cursor recovery tool."""

from __future__ import annotations

import argparse
import ctypes
import sys

from platform.windows.clip_recovery import release_input_guards

MB_ICONINFORMATION = 0x00000040
MB_ICONERROR = 0x00000010


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Release cursor lock manually.")
    parser.add_argument("--quiet", action="store_true", help="Suppress console output.")
    return parser.parse_args(argv)


def _write_line(stream, message: str) -> bool:
    if stream is None:
        return False
    try:
        stream.write(message + "\n")
        stream.flush()
        return True
    except Exception:
        return False


def _show_message_box(message: str, *, error: bool) -> None:
    try:
        user32 = ctypes.windll.user32
    except Exception:
        return
    flags = MB_ICONERROR if error else MB_ICONINFORMATION
    try:
        user32.MessageBoxW(None, message, "Multi Screen Pass", flags)
    except Exception:
        return


def _notify_user(message: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    if _write_line(stream, message):
        return
    _show_message_box(message, error=error)


def main(argv=None) -> int:
    args = parse_args(argv)
    released = release_input_guards()
    if not args.quiet:
        _notify_user(
            "마우스 잠금을 해제했습니다." if released else "마우스 잠금 해제에 실패했습니다.",
            error=not released,
        )
    return 0 if released else 1


if __name__ == "__main__":
    raise SystemExit(main())
