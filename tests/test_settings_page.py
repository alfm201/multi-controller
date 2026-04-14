from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QAbstractSpinBox

from runtime.app_settings import AppSettings
from runtime.app_version import UpdateCheckResult
from runtime.gui_style import apply_gui_theme
from runtime.settings_page import SettingsPage


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

    assert all(field.buttonSymbols() == QAbstractSpinBox.UpDownArrows for field in spin_boxes)


def test_gui_theme_does_not_override_spinbox_arrow_glyphs():
    app = QApplication.instance()
    apply_gui_theme(app)

    stylesheet = app.styleSheet()

    assert "QSpinBox::up-arrow" not in stylesheet
    assert "QSpinBox::down-arrow" not in stylesheet


def test_settings_page_shows_current_version_label(qtbot):
    ctx = SimpleNamespace(settings=AppSettings(), layout=None)
    page = SettingsPage(ctx, version_provider=lambda: "v9.9.9")
    qtbot.addWidget(page)

    assert page._current_version_value.text() == "v9.9.9"


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
            status="update_available",
        ),
    )
    page.messageRequested.connect(lambda text, tone: messages.append((text, tone)))
    qtbot.addWidget(page)

    qtbot.mouseClick(page._version_check_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: page._version_check_button.isEnabled())

    assert "v0.3.18" in page._version_check_status.text()
    assert "https://example.com/release/v0.3.18" in page._version_check_status.text()
    assert messages[-1][1] == "accent"
