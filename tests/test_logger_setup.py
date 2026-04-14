import logging

from runtime.app_logging import DETAIL_LEVEL, log_detail
from utils.logger_setup import setup_logging


def test_setup_logging_creates_debug_log_file(tmp_path):
    log_path = setup_logging(
        debug=True,
        log_dir=tmp_path,
        retention_days=14,
        max_total_size_mb=50,
    )
    try:
        assert log_path is not None
        logging.debug("hello logger")
        logging.info("hello info")
        for handler in logging.getLogger().handlers:
            handler.flush()
        assert log_path.exists()
        assert "hello logger" in log_path.read_text(encoding="utf-8")
        application_logs = list(tmp_path.glob("application-*.log"))
        warning_logs = list(tmp_path.glob("warning-*.log"))
        error_logs = list(tmp_path.glob("error-*.log"))
        debug_logs = list(tmp_path.glob("debug-*.log"))
        assert len(application_logs) == 1
        assert len(warning_logs) == 1
        assert len(error_logs) == 1
        assert len(debug_logs) == 1
        assert "hello info" in application_logs[0].read_text(encoding="utf-8")
    finally:
        logging.shutdown()


def test_setup_logging_writes_detail_only_to_debug_log(tmp_path):
    setup_logging(
        debug=True,
        log_dir=tmp_path,
        retention_days=14,
        max_total_size_mb=100,
    )
    try:
        logging.getLogger().setLevel(logging.DEBUG)
        log_detail("hello detail")
        for handler in logging.getLogger().handlers:
            handler.flush()
        application_log = next(tmp_path.glob("application-*.log"))
        debug_log = next(tmp_path.glob("debug-*.log"))
        assert "hello detail" not in application_log.read_text(encoding="utf-8")
        assert "hello detail" in debug_log.read_text(encoding="utf-8")
        assert DETAIL_LEVEL == 15
    finally:
        logging.shutdown()
