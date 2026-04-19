"""Project-wide logging helpers and custom log levels."""

from __future__ import annotations

import logging

DETAIL_LEVEL = 15
DETAIL_LEVEL_NAME = "DETAIL"

# Log tags are intentionally short, domain-only labels.
# Severity belongs to the logging level, and outcomes/details belong to the message body.
TAG_CAPTURE = "CAPTURE"
TAG_CONFIG = "CONFIG"
TAG_COORD = "COORD"
TAG_CURSOR = "CURSOR"
TAG_DISPATCH = "DISPATCH"
TAG_ERROR = "ERROR"
TAG_EXIT = "EXIT"
TAG_GUI = "GUI"
TAG_HOTKEY = "HOTKEY"
TAG_HTTP = "HTTP"
TAG_INJECT = "INJECT"
TAG_LOG = "LOG"
TAG_MONITOR = "MONITOR"
TAG_PEER = "PEER"
TAG_PRIVILEGE = "PRIVILEGE"
TAG_ROUTER = "ROUTER"
TAG_SELF = "SELF"
TAG_SHUTDOWN = "SHUTDOWN"
TAG_SINK = "SINK"
TAG_STARTUP = "STARTUP"
TAG_STATE = "STATE"
TAG_STATUS = "STATUS"
TAG_SWITCH = "SWITCH"
TAG_UPDATE = "UPDATE"


def tag_message(tag: str, message: str = "") -> str:
    normalized = str(tag or "").strip().upper()
    if not normalized:
        return message
    if not message:
        return f"[{normalized}]"
    return f"[{normalized}] {message}"


def install_logging_levels() -> None:
    if getattr(logging, "DETAIL", None) == DETAIL_LEVEL and hasattr(logging, "detail"):
        return

    logging.addLevelName(DETAIL_LEVEL, DETAIL_LEVEL_NAME)
    setattr(logging, "DETAIL", DETAIL_LEVEL)

    def _logger_detail(self, message, *args, **kwargs):
        if self.isEnabledFor(DETAIL_LEVEL):
            self._log(DETAIL_LEVEL, message, args, **kwargs)

    def _detail(message, *args, **kwargs):
        logging.log(DETAIL_LEVEL, message, *args, **kwargs)

    logging.Logger.detail = _logger_detail  # type: ignore[attr-defined]
    logging.detail = _detail  # type: ignore[attr-defined]


def log_detail(message, *args, **kwargs) -> None:
    install_logging_levels()
    logging.log(DETAIL_LEVEL, message, *args, **kwargs)


install_logging_levels()
