from types import SimpleNamespace

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QAbstractSpinBox

from runtime.app_settings import AppSettings, UpdateCheckSettings
from runtime.app_version import UpdateCheckResult
from runtime.gui_style import apply_gui_theme
from runtime.http_utils import WindowsNativeRequestError
from runtime.settings_page import SettingsPage, StepperSpinBox


class FakeUpdateInstaller:
    def __init__(self):
        self.calls = []

    def prepare_update(self, result, *, relaunch_mode):
        self.calls.append((result, relaunch_mode))
        return SimpleNamespace(
            installer_path="installer.exe",
            manifest_path="manifest.json",
            launcher_pid=1234,
            relaunch_mode=relaunch_mode,
        )


def test_settings_page_spin_boxes_show_up_down_arrows(qtbot):
    ctx = SimpleNamespace(settings=AppSettings(), layout=None)
    page = SettingsPage(ctx)
    qtbot.addWidget(page)

    spin_boxes = [
        page._cooldown_ms,
        page._return_guard_ms,
        page._backup_min_count,
        page._backup_max_age_days,
        page._log_retention_days,
        page._log_max_total_size_mb,
    ]

    assert all(isinstance(field, StepperSpinBox) for field in spin_boxes)
    assert all(field.buttonSymbols() == QAbstractSpinBox.NoButtons for field in spin_boxes)
    assert all(field._step_up_button.arrowType() == Qt.UpArrow for field in spin_boxes)
    assert all(field._step_down_button.arrowType() == Qt.DownArrow for field in spin_boxes)
    assert all(field.minimumHeight() >= 32 for field in spin_boxes)
    assert all(field.minimumWidth() >= 240 for field in spin_boxes)


def test_gui_theme_defines_custom_spinbox_button_layout():
    app = QApplication.instance()
    apply_gui_theme(app)

    stylesheet = app.styleSheet()

    assert "QToolButton#spinStepButtonUp" in stylesheet
    assert "QToolButton#spinStepButtonDown" in stylesheet
    assert "min-width: 32px;" in stylesheet
    assert "padding-right: 42px;" in stylesheet
    assert "min-height: 32px;" in stylesheet
    assert "font-size: 14px;" in stylesheet


def test_settings_page_spin_boxes_ignore_mouse_wheel(qtbot):
    ctx = SimpleNamespace(settings=AppSettings(), layout=None)
    page = SettingsPage(ctx)
    qtbot.addWidget(page)
    page.show()

    field = page._cooldown_ms
    field.setValue(10)
    pos = field.rect().center()
    global_pos = field.mapToGlobal(pos)
    event = QWheelEvent(
        QPointF(pos),
        QPointF(global_pos),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.ScrollUpdate,
        False,
    )

    QApplication.sendEvent(field, event)

    assert field.value() == 10
    assert event.isAccepted() is False


def test_settings_page_shows_current_version_label(qtbot):
    ctx = SimpleNamespace(settings=AppSettings(), layout=None)
    page = SettingsPage(ctx, version_provider=lambda: "v9.9.9")
    qtbot.addWidget(page)

    assert page._current_version_value.text() == "v9.9.9"


def test_settings_page_reflects_auto_update_preference(qtbot):
    ctx = SimpleNamespace(
        settings=AppSettings(
            updates=UpdateCheckSettings(
                auto_check_enabled=True,
                last_checked_at="2026-04-15T00:00:00Z",
            )
        ),
        layout=None,
    )
    page = SettingsPage(ctx)
    qtbot.addWidget(page)

    assert page._auto_update_checkbox.isChecked() is True
    assert page._auto_check_timer.isActive() is True


def test_settings_page_schedules_startup_update_check_even_when_auto_check_is_disabled(qtbot):
    ctx = SimpleNamespace(settings=AppSettings(), layout=None)
    page = SettingsPage(ctx)
    qtbot.addWidget(page)

    assert page._startup_check_scheduled is True
    assert page._startup_check_completed is False


def test_settings_page_checks_latest_version_in_background(qtbot):
    ctx = SimpleNamespace(settings=AppSettings(), layout=None)
    messages = []
    page = SettingsPage(
        ctx,
        version_provider=lambda: "v0.3.17",
        update_checker=lambda: UpdateCheckResult(
            current_version="0.3.17",
            latest_version="0.3.18",
            latest_tag_name="v0.3.18",
            release_url="https://example.com/release/v0.3.18",
            installer_url="https://example.com/download/MultiScreenPass-Setup-0.3.18.exe",
            status="update_available",
        ),
    )
    page.messageRequested.connect(lambda text, tone: messages.append((text, tone)))
    qtbot.addWidget(page)

    qtbot.mouseClick(page._version_check_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: page._version_check_button.isEnabled())

    assert page._update_notice.isHidden() is False
    assert page._install_update_button.isHidden() is False
    assert messages == []


def test_update_check_does_not_focus_cooldown_input(qtbot):
    ctx = SimpleNamespace(settings=AppSettings(), layout=None)
    page = SettingsPage(
        ctx,
        update_checker=lambda: UpdateCheckResult(
            current_version="0.3.20",
            latest_version="0.3.20",
            latest_tag_name="v0.3.20",
            release_url="https://example.com/release/v0.3.20",
            installer_url=None,
            status="up_to_date",
        ),
    )
    qtbot.addWidget(page)
    page.show()
    page.setFocus()

    qtbot.mouseClick(page._version_check_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: page._version_check_button.isEnabled())

    assert page._cooldown_ms.hasFocus() is False
    assert page._cooldown_ms.lineEdit().hasFocus() is False


def test_settings_page_surfaces_native_transport_failure_without_status_zero(qtbot):
    ctx = SimpleNamespace(settings=AppSettings(), layout=None)
    messages = []

    def failing_update_checker():
        raise OSError("Windows native request did not expose an HTTP status code.")

    page = SettingsPage(
        ctx,
        update_checker=failing_update_checker,
    )
    page.messageRequested.connect(lambda text, tone: messages.append((text, tone)))
    qtbot.addWidget(page)

    qtbot.mouseClick(page._version_check_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: page._version_check_button.isEnabled())

    assert messages == [("버전 확인 실패: Windows native request did not expose an HTTP status code.", "warning")]


def test_settings_page_preserves_native_failure_metadata_in_version_check_payload(qtbot):
    ctx = SimpleNamespace(settings=AppSettings(), layout=None)
    payloads = []

    def failing_update_checker():
        raise WindowsNativeRequestError(
            "Windows native request returned invalid HTTP status: 0",
            failure_kind="invalid_http_status",
            status_code=0,
        )

    page = SettingsPage(
        ctx,
        update_checker=failing_update_checker,
    )
    page.versionCheckFinished.connect(lambda payload: payloads.append(payload))
    qtbot.addWidget(page)

    page._run_version_check(trigger="manual")

    assert payloads[-1]["error"] == "Windows native request returned invalid HTTP status: 0"
    assert payloads[-1]["error_kind"] == "invalid_http_status"
    assert payloads[-1]["status_code"] == 0


def test_settings_page_logs_version_check_failure_metadata(qtbot, caplog):
    ctx = SimpleNamespace(settings=AppSettings(), layout=None)

    def failing_update_checker():
        raise WindowsNativeRequestError(
            "Windows native request returned invalid HTTP status: 0",
            failure_kind="invalid_http_status",
            status_code=0,
        )

    page = SettingsPage(
        ctx,
        update_checker=failing_update_checker,
    )
    qtbot.addWidget(page)

    with caplog.at_level("WARNING"):
        page._run_version_check(trigger="manual")

    assert "version check failed trigger=manual kind=invalid_http_status status=0" in caplog.text
    assert "Windows native request returned invalid HTTP status: 0" in caplog.text


def test_settings_page_prepares_update_install_and_requests_quit(qtbot):
    ctx = SimpleNamespace(settings=AppSettings(), layout=None)
    installer = FakeUpdateInstaller()
    quits = []
    page = SettingsPage(
        ctx,
        update_installer=installer,
        request_quit=lambda: quits.append("quit"),
    )
    qtbot.addWidget(page)
    page._latest_update_result = UpdateCheckResult(
        current_version="0.3.17",
        latest_version="0.3.18",
        latest_tag_name="v0.3.18",
        release_url="https://example.com/release/v0.3.18",
        installer_url="https://example.com/download/MultiScreenPass-Setup-0.3.18.exe",
        status="update_available",
    )
    page._set_update_notice(page._latest_update_result)

    qtbot.mouseClick(page._install_update_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: bool(installer.calls))
    qtbot.waitUntil(lambda: quits == ["quit"])

    assert installer.calls[0][1] == "preserve"
    assert quits == ["quit"]
    assert page._update_notice_payload["title"] == "업데이트 v0.3.18 설치 준비가 완료되었습니다."


def test_settings_page_auto_check_only_emits_update_notice(qtbot):
    ctx = SimpleNamespace(settings=AppSettings(), layout=None)
    installer = FakeUpdateInstaller()
    page = SettingsPage(
        ctx,
        update_installer=installer,
    )
    qtbot.addWidget(page)

    result = UpdateCheckResult(
        current_version="0.3.17",
        latest_version="0.3.18",
        latest_tag_name="v0.3.18",
        release_url="https://example.com/release/v0.3.18",
        installer_url="https://example.com/download/MultiScreenPass-Setup-0.3.18.exe",
        status="update_available",
    )

    page._apply_version_check_result({"result": result, "trigger": "auto", "error": None})

    assert installer.calls == []
    assert page._update_notice_payload["visible"] is True
    assert page._update_notice_payload["button_text"] == "업데이트 설치"


def test_settings_page_emits_update_notice_payload(qtbot):
    ctx = SimpleNamespace(settings=AppSettings(), layout=None)
    notices = []
    page = SettingsPage(ctx)
    page.updateNoticeChanged.connect(notices.append)
    qtbot.addWidget(page)

    result = UpdateCheckResult(
        current_version="0.3.17",
        latest_version="0.3.18",
        latest_tag_name="v0.3.18",
        release_url="https://example.com/release/v0.3.18",
        installer_url="https://example.com/download/MultiScreenPass-Setup-0.3.18.exe",
        status="update_available",
    )
    page._set_update_notice(result)

    assert notices[-1]["visible"] is True
    assert notices[-1]["stage"] == "update_available"
    assert notices[-1]["title"] == "새 업데이트 v0.3.18이 준비되었습니다!"
    assert notices[-1]["detail"] == "설치 버튼을 눌러 새 버전 준비를 시작할 수 있습니다."


def test_settings_page_update_notice_text_uses_transparent_background(qtbot):
    ctx = SimpleNamespace(settings=AppSettings(), layout=None)
    page = SettingsPage(ctx)
    qtbot.addWidget(page)

    assert "background: transparent" in page._update_notice_title.styleSheet()
    assert "background: transparent" in page._update_notice_detail.styleSheet()


def test_settings_page_keeps_actions_visible_outside_scroll(qtbot):
    ctx = SimpleNamespace(settings=AppSettings(), layout=None)
    page = SettingsPage(ctx)
    qtbot.addWidget(page)

    assert page._content_scroll.widget() is not None
    assert page._footer.isHidden() is False
    assert page._reset_button.parent() is page._footer_bar
    assert page._save_button.parent() is page._footer_bar
    assert page._footer_bar.minimumWidth() >= 420


def test_settings_page_footer_bar_aligns_with_content_width(qtbot):
    ctx = SimpleNamespace(settings=AppSettings(), layout=None)
    page = SettingsPage(ctx)
    qtbot.addWidget(page)
    page.resize(900, 700)
    page.show()
    qtbot.waitExposed(page)

    assert page._footer_bar.geometry().left() == 0
    assert page._footer_bar.width() == page._footer.width()


def test_settings_page_emits_remote_update_download_and_install_statuses(qtbot):
    ctx = SimpleNamespace(settings=AppSettings(), layout=None, self_node=SimpleNamespace(node_id="B"))
    installer = FakeUpdateInstaller()
    page = SettingsPage(
        ctx,
        update_installer=installer,
        request_quit=lambda: None,
    )
    notices = []
    page.remoteUpdateStatusChanged.connect(notices.append)
    qtbot.addWidget(page)
    page._latest_update_result = UpdateCheckResult(
        current_version="0.3.17",
        latest_version="0.3.18",
        latest_tag_name="v0.3.18",
        release_url="https://example.com/release/v0.3.18",
        installer_url="https://example.com/download/MultiScreenPass-Setup-0.3.18.exe",
        status="update_available",
    )

    page.start_remote_update(background=False, requester_id="A")
    qtbot.waitUntil(lambda: bool(installer.calls))
    qtbot.waitUntil(lambda: any(item["status"] == "installing" for item in notices))

    assert [item["status"] for item in notices[-3:]] == ["checking", "downloading", "installing"]
    assert notices[-1]["requester_id"] == "A"
    assert notices[-1]["target_id"] == "B"
    assert all(item["target_kind"] == "remote_node" for item in notices)
    assert all(item["action"] == "request" for item in notices)
    assert all(item["origin"] == "remote_command" for item in notices)
    assert all(item["session_id"] == notices[0]["session_id"] for item in notices)
    assert len({item["event_id"] for item in notices}) == len(notices)
    assert notices[1]["current_version"] == "0.3.17"
    assert notices[1]["latest_version"] == "0.3.18"
    assert page._pending_remote_requester_id is None
    assert page._pending_remote_session_id is None
    assert installer.calls[0][1] == "gui"


def test_settings_page_uses_tray_relaunch_mode_for_background_remote_update(qtbot):
    ctx = SimpleNamespace(settings=AppSettings(), layout=None, self_node=SimpleNamespace(node_id="B"))
    installer = FakeUpdateInstaller()
    page = SettingsPage(
        ctx,
        update_installer=installer,
        request_quit=lambda: None,
    )
    qtbot.addWidget(page)
    page._latest_update_result = UpdateCheckResult(
        current_version="0.3.17",
        latest_version="0.3.18",
        latest_tag_name="v0.3.18",
        release_url="https://example.com/release/v0.3.18",
        installer_url="https://example.com/download/MultiScreenPass-Setup-0.3.18.exe",
        status="update_available",
    )

    page.start_remote_update(background=True, requester_id="A")
    qtbot.waitUntil(lambda: bool(installer.calls))

    assert installer.calls[0][1] == "tray"


def test_settings_page_reports_busy_status_for_remote_update_request(qtbot):
    ctx = SimpleNamespace(settings=AppSettings(), layout=None, self_node=SimpleNamespace(node_id="B"))
    page = SettingsPage(ctx)
    notices = []
    page.remoteUpdateStatusChanged.connect(notices.append)
    qtbot.addWidget(page)
    page._version_check_running = True

    page.start_remote_update(background=False, requester_id="A")

    assert len(notices) == 1
    assert notices[0]["status"] == "failed"
    assert notices[0]["detail"] == "이미 업데이트 확인 또는 설치 작업이 진행 중입니다."
    assert notices[0]["requester_id"] == "A"
    assert notices[0]["target_id"] == "B"
    assert page._pending_remote_requester_id is None
    assert page._pending_remote_session_id is None


def test_settings_page_removes_inline_update_help_and_status_labels(qtbot):
    ctx = SimpleNamespace(settings=AppSettings(), layout=None)
    page = SettingsPage(ctx)
    qtbot.addWidget(page)

    assert not hasattr(page, "_version_check_status")
