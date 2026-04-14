import logging

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
