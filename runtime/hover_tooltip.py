"""Lightweight tooltip widget that follows pointer movement."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLabel

from runtime.gui_style import PALETTE


class HoverTooltip:
    def __init__(self, parent=None):
        self._label = QLabel(None, Qt.ToolTip)
        self._label.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._label.setWordWrap(True)
        self._label.setMargin(6)
        self._label.setStyleSheet(
            f"background: {PALETTE['surface']}; "
            f"color: {PALETTE['text']}; "
            f"border: 1px solid {PALETTE['border']}; "
            "border-radius: 6px;"
        )

    @property
    def widget(self) -> QLabel:
        return self._label

    def show_text(self, text: str, global_pos: QPoint) -> None:
        if not text:
            self.hide()
            return
        self._label.setText(text)
        self._label.adjustSize()
        self._label.move(self._resolved_position(global_pos))
        self._label.show()
        self._label.raise_()

    def hide(self) -> None:
        self._label.hide()

    def _resolved_position(self, global_pos: QPoint) -> QPoint:
        offset_x = 2
        offset_y = 2
        screen = QGuiApplication.screenAt(global_pos)
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return QPoint(
                global_pos.x() - self._label.width() - offset_x,
                global_pos.y() - self._label.height() - offset_y,
            )
        geometry = screen.availableGeometry()
        width = self._label.width()
        height = self._label.height()
        top_y = global_pos.y() - height - offset_y
        default_target = QPoint(
            global_pos.x() - width - offset_x,
            top_y,
        )
        if (
            default_target.x() >= geometry.left()
            and default_target.y() >= geometry.top()
            and default_target.x() + width <= geometry.right()
            and default_target.y() + height <= geometry.bottom()
        ):
            return default_target

        fallback_target = QPoint(
            global_pos.x() - offset_x,
            top_y,
        )
        if fallback_target.y() < geometry.top():
            fallback_target.setY(geometry.top())
        if fallback_target.x() + width > geometry.right():
            fallback_target.setX(geometry.right() - width)
        if fallback_target.x() < geometry.left():
            fallback_target.setX(geometry.left())
        if fallback_target.y() + height > geometry.bottom():
            fallback_target.setY(geometry.bottom() - height)
        return fallback_target
