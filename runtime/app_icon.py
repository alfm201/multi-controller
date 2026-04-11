"""Shared application icon helpers for Qt windows and tray."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap


def build_app_icon(size: int = 64) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    radius = max(int(size * 0.16), 8)
    outer = int(size * 0.09)
    body = size - outer * 2

    painter.setBrush(QColor("#2563eb"))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(outer, outer, body, body, radius, radius)

    inset = int(size * 0.18)
    monitor_w = size - inset * 2
    monitor_h = int(size * 0.22)
    painter.setBrush(QColor("#f8fbff"))
    painter.drawRoundedRect(inset, inset, monitor_w, monitor_h, 6, 6)

    tile_y = inset + monitor_h + int(size * 0.07)
    tile = int(size * 0.19)
    gap = int(size * 0.07)

    painter.setBrush(QColor("#f59e0b"))
    painter.drawRoundedRect(inset, tile_y, tile, tile, 5, 5)
    painter.setBrush(QColor("#dbeafe"))
    painter.drawRoundedRect(inset + tile + gap, tile_y, tile, tile, 5, 5)
    painter.setBrush(QColor("#bfdbfe"))
    painter.drawRoundedRect(inset + (tile + gap) * 2, tile_y, tile, tile, 5, 5)

    painter.end()
    return QIcon(pixmap)
