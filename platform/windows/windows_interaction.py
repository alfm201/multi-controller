"""Windows 권한/상호작용 진단 유틸리티."""

import ctypes
import logging
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class WindowsInteractionDiagnostics:
    """현재 프로세스의 Windows 입력 상호작용 관련 상태."""

    is_windows: bool
    is_elevated: bool | None


def detect_windows_interaction_diagnostics(shell32=None, platform=None):
    """현재 프로세스의 관리자 권한 상태를 감지한다."""
    platform = platform or sys.platform
    if not str(platform).startswith("win"):
        return WindowsInteractionDiagnostics(is_windows=False, is_elevated=None)

    if shell32 is None:
        try:
            shell32 = ctypes.windll.shell32
        except Exception:
            shell32 = None

    if shell32 is None or not hasattr(shell32, "IsUserAnAdmin"):
        return WindowsInteractionDiagnostics(is_windows=True, is_elevated=None)

    try:
        return WindowsInteractionDiagnostics(
            is_windows=True,
            is_elevated=bool(shell32.IsUserAnAdmin()),
        )
    except Exception:
        return WindowsInteractionDiagnostics(is_windows=True, is_elevated=None)


def format_windows_interaction_diagnostics(diagnostics):
    """권한 상태를 로그 친화적인 한 줄 문자열로 만든다."""
    if not diagnostics.is_windows:
        return "non-Windows platform"
    if diagnostics.is_elevated is True:
        return "elevated process; administrator app interaction should be possible"
    if diagnostics.is_elevated is False:
        return (
            "not elevated; Windows may block capture/injection for administrator apps "
            "(integrity/UIPI mismatch)"
        )
    return "elevation unknown; administrator app interaction may be restricted"


def log_windows_interaction_diagnostics(diagnostics=None):
    """현재 권한 상태를 운영 로그로 남긴다."""
    diagnostics = diagnostics or detect_windows_interaction_diagnostics()
    message = format_windows_interaction_diagnostics(diagnostics)
    if diagnostics.is_windows and diagnostics.is_elevated is False:
        logging.warning("[PRIVILEGE] %s", message)
    else:
        logging.info("[PRIVILEGE] %s", message)


def is_probable_access_denied(exc):
    """권한 불일치로 볼 수 있는 접근 거부 오류인지 추정한다."""
    text = str(exc).lower()
    if "access is denied" in text or "access denied" in text:
        return True
    if getattr(exc, "winerror", None) == 5:
        return True
    if getattr(exc, "errno", None) == 13:
        return True
    return False


def log_possible_admin_interaction_warning(exc):
    """접근 거부가 보이면 관리자 권한 불일치 가능성을 경고한다."""
    if not is_probable_access_denied(exc):
        return
    logging.warning(
        "[PRIVILEGE] input interaction may be blocked by Windows integrity/UIPI mismatch; "
        "run multi-controller with the same administrator level as the target app"
    )
