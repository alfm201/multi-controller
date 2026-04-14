from __future__ import annotations

import logging
import os
import zipfile
from datetime import datetime, timedelta

from runtime.log_manager import ManagedDailyLogHandler
from utils.logger_setup import setup_logging


def test_setup_logging_writes_info_logs_without_debug(tmp_path):
    log_path = setup_logging(
        debug=False,
        log_dir=tmp_path,
        retention_days=14,
        max_total_size_mb=50,
    )
    try:
        assert log_path is not None
        logging.debug("debug hidden")
        logging.warning("hello warning")
        logging.error("hello error")
        logging.info("hello info")
        for handler in logging.getLogger().handlers:
            handler.flush()
        application = (tmp_path / "application-2026-04-14.log") if False else log_path
        content = application.read_text(encoding="utf-8")
        assert "hello info" in content
        assert "hello warning" in content
        assert "hello error" in content
        assert "debug hidden" not in content
        warning_path = tmp_path / application.name.replace("application-", "warning-", 1)
        error_path = tmp_path / application.name.replace("application-", "error-", 1)
        warning_content = warning_path.read_text(encoding="utf-8")
        error_content = error_path.read_text(encoding="utf-8")
        assert "hello warning" in warning_content
        assert "hello error" not in warning_content
        assert "hello error" in error_content
        assert "hello warning" not in error_content
    finally:
        logging.shutdown()


def test_managed_daily_log_handler_compresses_previous_day_logs(tmp_path):
    today = datetime(2026, 4, 14, 9, 0, 0)
    old_application = tmp_path / "application-2026-04-13.log"
    old_warning = tmp_path / "warning-2026-04-13.log"
    old_application.write_text("old application\n", encoding="utf-8")
    old_warning.write_text("old warning\n", encoding="utf-8")

    handler = ManagedDailyLogHandler(
        log_dir=tmp_path,
        kind="application",
        min_level=logging.INFO,
        retention_days=14,
        max_total_size_mb=50,
        now_provider=lambda: today,
    )
    try:
        assert not old_application.exists()
        assert not old_warning.exists()
        archive = tmp_path / "archive" / "2026-04-13.zip"
        assert archive.exists()
        with zipfile.ZipFile(archive, "r") as zf:
            assert zf.read("application-2026-04-13.log").decode("utf-8").replace("\r\n", "\n") == "old application\n"
            assert zf.read("warning-2026-04-13.log").decode("utf-8").replace("\r\n", "\n") == "old warning\n"
    finally:
        handler.close()


def test_managed_daily_log_handler_prunes_old_logs_by_age(tmp_path):
    now = datetime(2026, 4, 14, 9, 0, 0)
    archive_root = tmp_path / "archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    old_archive = archive_root / "2026-03-01.zip"
    keep_archive = archive_root / "2026-04-13.zip"
    for target, payload in (
        (old_archive, "o" * 128),
        (keep_archive, "k" * 128),
    ):
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(target.stem, payload)

    old_time = (now - timedelta(days=30)).timestamp()
    keep_time = (now - timedelta(days=1)).timestamp()
    os.utime(old_archive, (old_time, old_time))
    os.utime(keep_archive, (keep_time, keep_time))

    handler = ManagedDailyLogHandler(
        log_dir=tmp_path,
        kind="application",
        min_level=logging.INFO,
        retention_days=14,
        max_total_size_mb=50,
        now_provider=lambda: now,
    )
    try:
        assert not old_archive.exists()
        assert keep_archive.exists()
    finally:
        handler.close()
