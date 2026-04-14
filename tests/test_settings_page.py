from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QAbstractSpinBox

from runtime.app_settings import AppSettings
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
