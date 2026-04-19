"""Tests for platform/windows/windows_interaction.py."""

import logging

from platform.windows.windows_interaction import (
    detect_windows_interaction_diagnostics,
    format_windows_interaction_diagnostics,
    is_probable_access_denied,
    log_possible_admin_interaction_warning,
)


class FakeShell32:
    def __init__(self, is_admin_result):
        self._is_admin_result = is_admin_result

    def IsUserAnAdmin(self):
        return self._is_admin_result


class WinErr5(Exception):
    def __init__(self):
        super().__init__("Access is denied")
        self.winerror = 5


def test_detect_windows_interaction_diagnostics_non_windows():
    diagnostics = detect_windows_interaction_diagnostics(platform="linux")
    assert diagnostics.is_windows is False
    assert diagnostics.is_elevated is None


def test_detect_windows_interaction_diagnostics_elevated():
    diagnostics = detect_windows_interaction_diagnostics(
        shell32=FakeShell32(True),
        platform="win32",
    )
    assert diagnostics.is_windows is True
    assert diagnostics.is_elevated is True


def test_detect_windows_interaction_diagnostics_not_elevated():
    diagnostics = detect_windows_interaction_diagnostics(
        shell32=FakeShell32(False),
        platform="win32",
    )
    assert diagnostics.is_windows is True
    assert diagnostics.is_elevated is False


def test_format_windows_interaction_diagnostics_for_non_elevated_process():
    diagnostics = detect_windows_interaction_diagnostics(
        shell32=FakeShell32(False),
        platform="win32",
    )
    message = format_windows_interaction_diagnostics(diagnostics)
    assert "not elevated" in message
    assert "administrator apps" in message


def test_is_probable_access_denied_matches_winerror_5():
    assert is_probable_access_denied(WinErr5()) is True


def test_log_possible_admin_interaction_warning_logs_for_access_denied(caplog):
    with caplog.at_level(logging.WARNING):
        log_possible_admin_interaction_warning(WinErr5())
    assert any("UIPI mismatch" in record.message for record in caplog.records)
