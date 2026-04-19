"""Lightweight in-app toast notification used for tray actions."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, QTimer, Qt
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from app.meta.identity import APP_DISPLAY_NAME
from app.ui.gui_style import PALETTE


class _ToastWindow(QFrame):
    DISMISS_DRAG_DISTANCE = 96
    SHOW_OFFSET_Y = 12
    HIDE_OFFSET_X = 72

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.BypassWindowManagerHint
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("toastHost")
        self.setMinimumWidth(320)
        self.setMaximumWidth(380)

        self._anchor_pos = QPoint(0, 0)
        self._drag_origin: QPoint | None = None
        self._start_pos: QPoint | None = None
        self._dragging = False
        self._hiding = False

        self.setStyleSheet(
            f"""
            QFrame#toastHost {{
                background: transparent;
                border: none;
            }}
            QFrame#toastCard {{
                background: rgb(249, 251, 255);
                border: 1px solid rgb(168, 178, 194);
                border-radius: 10px;
            }}
            QLabel#toastTitle {{
                background: transparent;
                color: {PALETTE["text"]};
                font-size: 14px;
                font-weight: 700;
            }}
            QLabel#toastBody {{
                background: transparent;
                color: {PALETTE["text"]};
                font-size: 14px;
                line-height: 1.35;
            }}
            QFrame#toastDivider {{
                background: rgb(214, 220, 229);
                min-height: 1px;
                max-height: 1px;
                border: none;
            }}
            QPushButton#toastClose {{
                min-width: 28px;
                max-width: 28px;
                min-height: 28px;
                max-height: 28px;
                padding: 0;
                border: none;
                border-radius: 14px;
                background: transparent;
                color: {PALETTE["muted"]};
                font-size: 15px;
                font-weight: 700;
            }}
            QPushButton#toastClose:hover {{
                background: qradialgradient(
                    cx: 0.5, cy: 0.5, radius: 1.1,
                    fx: 0.5, fy: 0.5,
                    stop: 0 rgba(253, 228, 228, 255),
                    stop: 0.45 rgba(253, 236, 236, 245),
                    stop: 0.78 rgba(249, 244, 247, 232),
                    stop: 1 rgba(249, 251, 255, 255)
                );
                color: rgb(194, 65, 65);
            }}
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._card = QFrame()
        self._card.setObjectName("toastCard")
        layout.addWidget(self._card)

        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)

        self.title_label = QLabel(APP_DISPLAY_NAME)
        self.title_label.setObjectName("toastTitle")
        header.addWidget(self.title_label, 1)

        self.close_button = QPushButton("x")
        self.close_button.setObjectName("toastClose")
        self.close_button.clicked.connect(self.hide_animated)
        header.addWidget(self.close_button, 0, Qt.AlignTop)
        card_layout.addLayout(header)

        divider = QFrame()
        divider.setObjectName("toastDivider")
        card_layout.addWidget(divider)

        self.body_label = QLabel("")
        self.body_label.setObjectName("toastBody")
        self.body_label.setWordWrap(True)
        self.body_label.setTextInteractionFlags(Qt.NoTextInteraction)
        card_layout.addWidget(self.body_label)

        for widget in (self, self._card, self.title_label, self.body_label):
            widget.installEventFilter(self)

        self._show_animation = QPropertyAnimation(self, b"pos", self)
        self._show_animation.setDuration(150)
        self._show_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._hide_animation = QPropertyAnimation(self, b"pos", self)
        self._hide_animation.setDuration(140)
        self._hide_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hide_animation.finished.connect(self._on_hide_finished)

    def show_toast(self, title: str, message: str) -> None:
        self._show_animation.stop()
        self._hide_animation.stop()
        self._hiding = False
        self.title_label.setText(title or APP_DISPLAY_NAME)
        self.body_label.setText(message)
        self.adjustSize()
        self._anchor_pos = self._resolved_position()
        start_pos = self._anchor_pos + QPoint(0, self.SHOW_OFFSET_Y)
        self.move(start_pos)
        self.show()
        self.raise_()
        self._show_animation.setStartValue(start_pos)
        self._show_animation.setEndValue(self._anchor_pos)
        self._show_animation.start()

    def hide_animated(self) -> None:
        if not self.isVisible():
            self.hide()
            return
        self._show_animation.stop()
        self._hide_animation.stop()
        self._hiding = True
        self._hide_animation.setStartValue(self.pos())
        self._hide_animation.setEndValue(self.pos() + QPoint(self.HIDE_OFFSET_X, 0))
        self._hide_animation.start()

    def hide(self) -> None:  # type: ignore[override]
        self._show_animation.stop()
        self._hide_animation.stop()
        self._hiding = False
        self._dragging = False
        self._drag_origin = None
        self._start_pos = None
        try:
            self.releaseMouse()
        except Exception:
            pass
        super().hide()
        self.move(self._anchor_pos)

    def eventFilter(self, watched: QObject, event):
        event_type = event.type()
        if event_type == QEvent.Type.MouseButtonPress and event.button() == Qt.LeftButton:
            self._show_animation.stop()
            self._hide_animation.stop()
            self._dragging = True
            self._drag_origin = event.globalPosition().toPoint()
            self._start_pos = self.pos()
            self.grabMouse()
            return True
        if (
            event_type == QEvent.Type.MouseMove
            and self._dragging
            and self._drag_origin is not None
            and self._start_pos is not None
        ):
            delta = event.globalPosition().toPoint() - self._drag_origin
            self.move(self._start_pos + QPoint(delta.x(), 0))
            return True
        if event_type == QEvent.Type.MouseButtonRelease and self._dragging:
            self._dragging = False
            try:
                self.releaseMouse()
            except Exception:
                pass
            delta_x = 0
            if self._drag_origin is not None:
                delta_x = event.globalPosition().toPoint().x() - self._drag_origin.x()
            self._drag_origin = None
            self._start_pos = None
            if abs(delta_x) >= self.DISMISS_DRAG_DISTANCE:
                self.hide_animated()
            else:
                self._show_animation.stop()
                self._hide_animation.stop()
                self._show_animation.setStartValue(self.pos())
                self._show_animation.setEndValue(self._anchor_pos)
                self._show_animation.start()
            return True
        return super().eventFilter(watched, event)

    def _on_hide_finished(self) -> None:
        if self._hiding:
            self.hide()

    def _resolved_position(self) -> QPoint:
        cursor_pos = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor_pos)
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return QPoint(32, 32)
        geometry = screen.availableGeometry()
        margin = 18
        return QPoint(
            geometry.right() - self.width() - margin,
            geometry.bottom() - self.height() - margin,
        )


class ToastNotification:
    def __init__(self):
        self._window = _ToastWindow()
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._window.hide_animated)

    def show_message(
        self,
        message: str,
        *,
        title: str = APP_DISPLAY_NAME,
        timeout_ms: int = 3200,
    ) -> None:
        if not message:
            self.hide()
            return
        self._window.show_toast(title, message)
        self._timer.start(timeout_ms)

    def hide(self) -> None:
        self._timer.stop()
        self._window.hide_animated()
