"""Helpers for horizontal scrolling interactions in Qt scroll areas."""

from __future__ import annotations

from PySide6.QtCore import QObject, QEvent, Qt


class HorizontalScrollInteraction(QObject):
    def __init__(self, scroll_area, parent=None):
        super().__init__(parent or scroll_area)
        self._scroll_area = scroll_area

    def eventFilter(self, watched, event):  # noqa: N802
        try:
            scrollbar = self._scroll_area.horizontalScrollBar()
        except RuntimeError:
            return False
        if scrollbar.maximum() <= scrollbar.minimum():
            return False
        event_type = event.type()
        if event_type == QEvent.Type.Wheel and event.modifiers() & Qt.ShiftModifier:
            delta = event.angleDelta().y() or event.angleDelta().x()
            if delta:
                scrollbar.setValue(scrollbar.value() - delta)
                event.accept()
                return True
        return False


def attach_horizontal_scroll_interaction(scroll_area) -> None:
    existing = getattr(scroll_area, "_horizontal_scroll_interaction", None)
    if existing is not None:
        return
    interaction = HorizontalScrollInteraction(scroll_area)
    scroll_area.installEventFilter(interaction)
    viewport_getter = getattr(scroll_area, "viewport", None)
    if callable(viewport_getter):
        viewport = viewport_getter()
        if viewport is not None and viewport is not scroll_area:
            viewport.installEventFilter(interaction)
    scroll_area._horizontal_scroll_interaction = interaction
