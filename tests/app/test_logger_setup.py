import logging
import re
from pathlib import Path

from platform.injection.os_injector import LoggingOSInjector
from control.routing.sink import InputSink
from app.logging.app_logging import DETAIL_LEVEL, log_detail
from app.logging.logger_setup import setup_logging


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


def test_setup_logging_includes_level_name_in_log_output(tmp_path):
    log_path = setup_logging(
        debug=False,
        log_dir=tmp_path,
        retention_days=14,
        max_total_size_mb=100,
    )
    try:
        assert log_path is not None
        logging.info("hello format")
        for handler in logging.getLogger().handlers:
            handler.flush()
        contents = log_path.read_text(encoding="utf-8")
        assert "INFO" in contents
        assert "hello format" in contents
        assert "[INFO" in contents
        assert contents.startswith("[")
        assert "] [INFO] " in contents
        assert contents[:11].count("-") == 2
    finally:
        logging.shutdown()


def test_runtime_log_tags_do_not_use_padding_spaces(caplog):
    sink = InputSink(injector=LoggingOSInjector(), require_authorization=False)

    with caplog.at_level(logging.INFO):
        sink.set_authorized_controller("peer-a")
        sink.handle("peer-a", {"kind": "key_down", "key": "Key.ctrl"})

    messages = [record.getMessage() for record in caplog.records]

    assert any("[SINK]" in message and "lease" in message for message in messages)
    assert any("[INJECT]" in message and "key down" in message for message in messages)
    assert not any("[SINK    ]" in message for message in messages)
    assert not any("[INJECT    ]" in message for message in messages)


def test_source_log_tags_do_not_contain_alignment_padding():
    repo_root = Path(__file__).resolve().parents[2]
    code_roots = (
        repo_root / "app",
        repo_root / "control",
        repo_root / "model",
        repo_root / "platform",
        repo_root / "transport",
    )
    tag_pattern = re.compile(r"\[[^\]\n]*\s{2,}\]")
    offenders = []

    for root in code_roots:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for line_no, line in enumerate(text.splitlines(), start=1):
                if "[%" in line:
                    continue
                if tag_pattern.search(line):
                    offenders.append(f"{path.relative_to(repo_root)}:{line_no}")

    assert offenders == []
