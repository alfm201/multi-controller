import logging

from utils.logger_setup import setup_logging


def test_setup_logging_creates_debug_log_file(tmp_path):
    log_path = setup_logging(debug=True, log_dir=tmp_path)
    try:
        assert log_path is not None
        logging.debug("hello logger")
        for handler in logging.getLogger().handlers:
            handler.flush()
        assert log_path.exists()
        assert "hello logger" in log_path.read_text(encoding="utf-8")
    finally:
        logging.shutdown()
