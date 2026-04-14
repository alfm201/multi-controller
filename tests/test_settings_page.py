from types import SimpleNamespace

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QAbstractSpinBox

from runtime.app_settings import AppSettings, UpdateCheckSettings
from runtime.app_version import UpdateCheckResult
from runtime.gui_style import apply_gui_theme
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
    assert all(field.minimumHeight() >= 40 for field in spin_boxes)


def test_gui_theme_defines_custom_spinbox_button_layout():
    app = QApplication.instance()
    apply_gui_theme(app)

    stylesheet = app.styleSheet()

    assert "QToolButton#spinStepButtonUp" in stylesheet
    assert "QToolButton#spinStepButtonDown" in stylesheet
    assert "min-width: 30px;" in stylesheet
    assert "padding-right: 42px;" in stylesheet


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

    assert "v0.3.18" in page._version_check_status.text()
    assert "https://example.com/release/v0.3.18" in page._version_check_status.text()
    assert page._update_notice.isHidden() is False
    assert page._install_update_button.isHidden() is False
    assert messages[-1][1] == "accent"


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
    assert "업데이트를 준비했습니다" in page._status.text()


def test_settings_page_auto_check_triggers_background_tray_update(qtbot):
    ctx = SimpleNamespace(settings=AppSettings(), layout=None)
    installer = FakeUpdateInstaller()
    quits = []
    page = SettingsPage(
        ctx,
        update_installer=installer,
        request_quit=lambda: quits.append("quit"),
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
    qtbot.waitUntil(lambda: bool(installer.calls))
    qtbot.waitUntil(lambda: quits == ["quit"])

    assert installer.calls[0][1] == "tray"
    assert quits == ["quit"]


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
    assert notices[-1]["title"] == "새로운 업데이트가 있습니다!"
    assert "v0.3.18" in notices[-1]["detail"]


def test_settings_page_keeps_actions_visible_outside_scroll(qtbot):
    ctx = SimpleNamespace(settings=AppSettings(), layout=None)
    page = SettingsPage(ctx)
    qtbot.addWidget(page)

    assert page._content_scroll.widget() is not None
    assert page._footer.isHidden() is False
    assert page._reset_button.parent() is page._footer_bar
    assert page._save_button.parent() is page._footer_bar
