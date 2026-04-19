import logging
from pathlib import Path

from app.logging.app_log_buffer import UILogHandler, get_application_log_store
from app.logging.app_logging import install_logging_levels
from app.logging.log_manager import ManagedDailyLogHandler

_ACTIVE_FILE_HANDLERS: list[ManagedDailyLogHandler] = []
_DEFAULT_LOG_FORMAT = "[%(asctime)s] [%(levelname)s] %(message)s"
_LOG_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    *,
    debug: bool = False,
    log_dir: str | Path | None = None,
    retention_days: int = 14,
    max_total_size_mb: int = 50,
) -> Path | None:
    global _ACTIVE_FILE_HANDLERS
    install_logging_levels()
    formatter = logging.Formatter(
        fmt=_DEFAULT_LOG_FORMAT,
        datefmt=_LOG_TIME_FORMAT,
    )
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    log_store = get_application_log_store()
    log_store.clear()
    ui_log_handler = UILogHandler(log_store)
    ui_log_handler.setFormatter(formatter)
    handlers: list[logging.Handler] = [stream_handler, ui_log_handler]
    log_path: Path | None = None
    _ACTIVE_FILE_HANDLERS = []
    file_handlers, log_path, _used_dir = _build_file_handlers(
        debug=debug,
        log_dir=log_dir,
        retention_days=retention_days,
        max_total_size_mb=max_total_size_mb,
    )
    for handler in file_handlers:
        handler.setFormatter(formatter)
    handlers.extend(file_handlers)
    _ACTIVE_FILE_HANDLERS = [handler for handler in file_handlers if isinstance(handler, ManagedDailyLogHandler)]
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        force=True,
        handlers=handlers,
    )
    return log_path


def _build_file_handlers(
    *,
    debug: bool,
    log_dir: str | Path | None,
    retention_days: int,
    max_total_size_mb: int,
):
    candidates = []
    if log_dir is not None:
        candidates.append(Path(log_dir))

    last_error = None
    for candidate in candidates:
        try:
            handlers: list[ManagedDailyLogHandler] = []
            application_handler = ManagedDailyLogHandler(
                log_dir=candidate,
                kind="application",
                min_level=logging.INFO,
                retention_days=retention_days,
                max_total_size_mb=max_total_size_mb,
            )
            warning_handler = ManagedDailyLogHandler(
                log_dir=candidate,
                kind="warning",
                min_level=logging.WARNING,
                max_level=logging.WARNING,
                retention_days=retention_days,
                max_total_size_mb=max_total_size_mb,
            )
            error_handler = ManagedDailyLogHandler(
                log_dir=candidate,
                kind="error",
                min_level=logging.ERROR,
                retention_days=retention_days,
                max_total_size_mb=max_total_size_mb,
            )
            handlers.extend([application_handler, warning_handler, error_handler])
            log_path = application_handler.current_log_path
            if debug:
                debug_handler = ManagedDailyLogHandler(
                    log_dir=candidate,
                    kind="debug",
                    min_level=logging.DEBUG,
                    retention_days=retention_days,
                    max_total_size_mb=max_total_size_mb,
                )
                handlers.append(debug_handler)
                log_path = debug_handler.current_log_path
            return handlers, log_path, candidate
        except OSError as exc:
            last_error = exc
            continue

    if last_error is not None:
        stream = logging.StreamHandler()
        stream.setLevel(logging.WARNING)
        bootstrap = logging.LogRecord(
            name="multiscreenpass",
            level=logging.WARNING,
            pathname=__file__,
            lineno=0,
            msg="[LOG] file logging unavailable: %s",
            args=(last_error,),
            exc_info=None,
        )
        stream.emit(bootstrap)
    return [], None, None


def update_logging_settings(*, retention_days: int, max_total_size_mb: int) -> None:
    if not _ACTIVE_FILE_HANDLERS:
        return
    for handler in _ACTIVE_FILE_HANDLERS:
        handler.update_policy(
            retention_days=retention_days,
            max_total_size_mb=max_total_size_mb,
        )
