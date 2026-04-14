import logging
from pathlib import Path

from runtime.log_manager import ManagedDailyLogHandler

_ACTIVE_FILE_HANDLERS: list[ManagedDailyLogHandler] = []


def setup_logging(
    *,
    debug: bool = False,
    log_dir: str | Path | None = None,
    retention_days: int = 14,
    max_total_size_mb: int = 50,
) -> Path | None:
    global _ACTIVE_FILE_HANDLERS
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_path: Path | None = None
    _ACTIVE_FILE_HANDLERS = []
    if log_dir is not None:
        log_dir = Path(log_dir)
        application_handler = ManagedDailyLogHandler(
            log_dir=log_dir,
            kind="application",
            min_level=logging.INFO,
            retention_days=retention_days,
            max_total_size_mb=max_total_size_mb,
        )
        warning_handler = ManagedDailyLogHandler(
            log_dir=log_dir,
            kind="warning",
            min_level=logging.WARNING,
            max_level=logging.WARNING,
            retention_days=retention_days,
            max_total_size_mb=max_total_size_mb,
        )
        error_handler = ManagedDailyLogHandler(
            log_dir=log_dir,
            kind="error",
            min_level=logging.ERROR,
            retention_days=retention_days,
            max_total_size_mb=max_total_size_mb,
        )
        handlers.extend([application_handler, warning_handler, error_handler])
        _ACTIVE_FILE_HANDLERS = [application_handler, warning_handler, error_handler]
        log_path = application_handler.current_log_path
        if debug:
            debug_handler = ManagedDailyLogHandler(
                log_dir=log_dir,
                kind="debug",
                min_level=logging.DEBUG,
                retention_days=retention_days,
                max_total_size_mb=max_total_size_mb,
            )
            handlers.append(debug_handler)
            _ACTIVE_FILE_HANDLERS.append(debug_handler)
            log_path = debug_handler.current_log_path
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
        handlers=handlers,
    )
    return log_path


def update_logging_settings(*, retention_days: int, max_total_size_mb: int) -> None:
    if not _ACTIVE_FILE_HANDLERS:
        return
    for handler in _ACTIVE_FILE_HANDLERS:
        handler.update_policy(
            retention_days=retention_days,
            max_total_size_mb=max_total_size_mb,
        )
