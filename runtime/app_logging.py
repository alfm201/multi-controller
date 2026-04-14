"""Project-wide logging helpers and custom log levels."""

from __future__ import annotations

import logging

DETAIL_LEVEL = 15
DETAIL_LEVEL_NAME = "DETAIL"


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
