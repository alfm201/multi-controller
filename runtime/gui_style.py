"""Shared lightweight Qt styling for the runtime GUI."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

PALETTE = {
    "window": "#ffffff",
    "surface": "#ffffff",
    "surface_alt": "#f4f7fb",
    "surface_muted": "#edf2f8",
    "border": "#d0d7e2",
    "text": "#172033",
    "muted": "#5a667c",
    "accent": "#2563eb",
    "accent_soft": "#dbeafe",
    "success": "#166534",
    "success_soft": "#dcfce7",
    "warning": "#92400e",
    "warning_soft": "#fef3c7",
    "danger": "#b91c1c",
    "danger_soft": "#fee2e2",
    "neutral": "#475569",
    "neutral_soft": "#e2e8f0",
}

TONE_MAP = {
    "accent": (PALETTE["accent_soft"], PALETTE["accent"]),
    "success": (PALETTE["success_soft"], PALETTE["success"]),
    "warning": (PALETTE["warning_soft"], PALETTE["warning"]),
    "danger": (PALETTE["danger_soft"], PALETTE["danger"]),
    "neutral": (PALETTE["neutral_soft"], PALETTE["neutral"]),
}


def palette_for_tone(tone: str) -> tuple[str, str]:
    return TONE_MAP.get(tone, TONE_MAP["neutral"])


def tone_qcolors(tone: str) -> tuple[QColor, QColor]:
    background, foreground = palette_for_tone(tone)
    return QColor(background), QColor(foreground)


def apply_gui_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(PALETTE["window"]))
    palette.setColor(QPalette.WindowText, QColor(PALETTE["text"]))
    palette.setColor(QPalette.Base, QColor(PALETTE["surface"]))
    palette.setColor(QPalette.AlternateBase, QColor(PALETTE["surface_alt"]))
    palette.setColor(QPalette.ToolTipBase, QColor(PALETTE["surface"]))
    palette.setColor(QPalette.ToolTipText, QColor(PALETTE["text"]))
    palette.setColor(QPalette.Text, QColor(PALETTE["text"]))
    palette.setColor(QPalette.Button, QColor(PALETTE["surface"]))
    palette.setColor(QPalette.ButtonText, QColor(PALETTE["text"]))
    palette.setColor(QPalette.Highlight, QColor(PALETTE["accent"]))
    palette.setColor(QPalette.HighlightedText, Qt.white)
    app.setPalette(palette)
    app.setStyleSheet(
        f"""
        QWidget {{
            background: {PALETTE["window"]};
            color: {PALETTE["text"]};
            font-size: 13px;
        }}
        QMainWindow, QDialog {{
            background: {PALETTE["window"]};
        }}
        QMenuBar {{
            background: {PALETTE["window"]};
        }}
        QMenuBar::item:selected {{
            background: {PALETTE["surface_alt"]};
            border-radius: 4px;
        }}
        QLabel#heading {{
            font-size: 20px;
            font-weight: 700;
        }}
        QLabel#subtle {{
            color: {PALETTE["muted"]};
        }}
        QLabel#cardTitle {{
            color: {PALETTE["muted"]};
            font-size: 12px;
            font-weight: 600;
        }}
        QLabel#cardValue {{
            font-size: 22px;
            font-weight: 700;
        }}
        QFrame#card, QFrame#panel, QFrame#banner {{
            background: {PALETTE["surface"]};
            border: 1px solid {PALETTE["border"]};
            border-radius: 6px;
        }}
        QFrame#panelAlt {{
            background: {PALETTE["surface_alt"]};
            border: 1px solid {PALETTE["border"]};
            border-radius: 6px;
        }}
        QPushButton {{
            min-height: 34px;
            padding: 0 12px;
            border: 1px solid {PALETTE["border"]};
            border-radius: 6px;
            background: {PALETTE["surface"]};
        }}
        QPushButton:hover {{
            border-color: {PALETTE["accent"]};
        }}
        QPushButton:pressed {{
            background: {PALETTE["surface_alt"]};
        }}
        QPushButton:checked {{
            background: {PALETTE["accent_soft"]};
            border-color: {PALETTE["accent"]};
            color: {PALETTE["accent"]};
            font-weight: 700;
        }}
        QPushButton:disabled {{
            color: #90a0b7;
            background: #eef2f7;
            border-color: #dde3ee;
        }}
        QPushButton#primary {{
            background: {PALETTE["accent"]};
            border-color: {PALETTE["accent"]};
            color: white;
            font-weight: 600;
        }}
        QPushButton#primary:disabled {{
            background: #bcd0fb;
            border-color: #bcd0fb;
            color: white;
        }}
        QPushButton[compactFilter="true"] {{
            min-height: 24px;
            padding: 0 10px;
            border-radius: 12px;
            font-size: 11px;
        }}
        QPushButton#navButton {{
            text-align: left;
            min-height: 38px;
            padding: 0 12px;
            background: transparent;
            border: none;
            border-radius: 6px;
            font-size: 12px;
            color: {PALETTE["muted"]};
        }}
        QPushButton#navButton:checked {{
            background: {PALETTE["surface"]};
            border: 1px solid {PALETTE["border"]};
            font-size: 15px;
            color: {PALETTE["text"]};
        }}
        QLabel#helpDot {{
            border: 1px solid {PALETTE["border"]};
            border-radius: 9px;
            background: {PALETTE["surface_alt"]};
            color: {PALETTE["muted"]};
            font-size: 11px;
            font-weight: 700;
        }}
        QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
            background: {PALETTE["surface"]};
            border: 1px solid {PALETTE["border"]};
            border-radius: 6px;
            min-height: 32px;
            padding: 0 12px;
            font-size: 14px;
            selection-background-color: {PALETTE["accent"]};
            selection-color: white;
        }}
        QSpinBox QLineEdit, QDoubleSpinBox QLineEdit {{
            background: transparent;
            border: none;
            padding: 0;
            margin: 0;
            color: {PALETTE["text"]};
            selection-background-color: {PALETTE["accent"]};
            selection-color: white;
        }}
        QTextEdit {{
            background: {PALETTE["surface"]};
            border: 1px solid {PALETTE["border"]};
            border-radius: 6px;
            padding: 8px 10px;
            font-size: 14px;
            selection-background-color: {PALETTE["accent"]};
            selection-color: white;
        }}
        QListWidget, QTableWidget {{
            background: {PALETTE["surface"]};
            border: 1px solid {PALETTE["border"]};
            border-radius: 6px;
            padding: 0;
            selection-background-color: {PALETTE["accent"]};
            selection-color: white;
        }}
        QSpinBox, QDoubleSpinBox {{
            padding-right: 42px;
        }}
        QToolButton#spinStepButtonUp, QToolButton#spinStepButtonDown {{
            min-width: 32px;
            padding: 0;
            border-left: 1px solid {PALETTE["border"]};
            background: #f3f4f6;
            color: {PALETTE["text"]};
        }}
        QToolButton#spinStepButtonUp {{
            border-top-right-radius: 10px;
            border-bottom: 1px solid {PALETTE["border"]};
        }}
        QToolButton#spinStepButtonDown {{
            border-bottom-right-radius: 10px;
        }}
        QToolButton#spinStepButtonUp:hover, QToolButton#spinStepButtonDown:hover {{
            background: #e5e7eb;
            color: {PALETTE["text"]};
        }}
        QToolButton#spinStepButtonUp:disabled, QToolButton#spinStepButtonDown:disabled {{
            color: #a8b3c6;
            background: #eef2f7;
        }}
        QToolButton#bannerDisclosureButton {{
            min-width: 28px;
            min-height: 28px;
            padding: 0;
            border: none;
            border-radius: 14px;
            background: rgba(148, 163, 184, 0.12);
            font-size: 15px;
            font-weight: 700;
        }}
        QToolButton#bannerDisclosureButton:hover {{
            background: rgba(107, 114, 128, 0.18);
        }}
        QToolButton#bannerDisclosureButton:pressed {{
            background: rgba(107, 114, 128, 0.28);
        }}
        QAbstractScrollArea {{
            background: {PALETTE["surface"]};
            border: 1px solid {PALETTE["border"]};
            border-radius: 6px;
        }}
        QAbstractItemView {{
            background: {PALETTE["surface"]};
            alternate-background-color: {PALETTE["surface"]};
            outline: none;
        }}
        QHeaderView {{
            background: transparent;
            border: none;
        }}
        QHeaderView::section {{
            background: {PALETTE["surface_alt"]};
            border: none;
            border-bottom: 1px solid {PALETTE["border"]};
            border-right: 1px solid {PALETTE["border"]};
            padding: 8px;
            font-weight: 600;
        }}
        QListWidget#compactList::item {{
            padding: 2px 0;
            margin: 0;
        }}
        QListWidget::item, QTableWidget::item {{
            padding: 8px;
        }}
        QListWidget::item:selected, QTableWidget::item:selected {{
            background: {PALETTE["accent_soft"]};
            color: {PALETTE["text"]};
        }}
        QListWidget::item:hover, QTableWidget::item:hover {{
            background: {PALETTE["surface_alt"]};
        }}
        QMenu {{
            background: {PALETTE["surface"]};
            border: 1px solid {PALETTE["border"]};
            padding: 6px;
        }}
        QMenu::item {{
            padding: 8px 16px;
            border-radius: 6px;
            background: transparent;
        }}
        QMenu::item:selected {{
            background: {PALETTE["accent"]};
            color: white;
        }}
        QToolTip {{
            background: {PALETTE["surface"]};
            color: {PALETTE["text"]};
            border: 1px solid {PALETTE["border"]};
            padding: 6px 8px;
        }}
        QScrollBar:vertical {{
            width: 12px;
            background: #f3f4f6;
            margin: 16px 0 16px 0;
            border-radius: 6px;
        }}
        QScrollBar::handle:vertical {{
            min-height: 24px;
            border-radius: 6px;
            background: #9ca3af;
        }}
        QScrollBar:horizontal {{
            height: 12px;
            background: #f3f4f6;
            margin: 0 16px 0 16px;
            border-radius: 6px;
        }}
        QScrollBar::handle:horizontal {{
            min-width: 24px;
            border-radius: 6px;
            background: #9ca3af;
        }}
        QScrollBar::sub-line:vertical, QScrollBar::add-line:vertical {{
            height: 14px;
            background: #e5e7eb;
            border-radius: 6px;
        }}
        QScrollBar::sub-line:horizontal, QScrollBar::add-line:horizontal {{
            width: 14px;
            background: #e5e7eb;
            border-radius: 6px;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
            background: transparent;
        }}
        QToolButton {{
            border: none;
        }}
        """
    )
