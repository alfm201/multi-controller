"""Qt settings page for runtime options and hotkeys."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from runtime.app_settings import (
    AppHotkeySettings,
    AppSettings,
    normalize_hotkey_string,
    validate_hotkey_settings,
)
from runtime.layouts import replace_auto_switch_settings


class HelpDot(QLabel):
    def __init__(self, tooltip: str, parent=None):
        super().__init__("?", parent)
        self.setObjectName("helpDot")
        self.setAlignment(Qt.AlignCenter)
        self.setFixedSize(18, 18)
        self.setToolTip(tooltip)
        self.setToolTipDuration(60000)

    def enterEvent(self, event):  # noqa: N802
        QToolTip.showText(self.mapToGlobal(self.rect().bottomRight()), self.toolTip(), self)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        QToolTip.hideText()
        super().leaveEvent(event)


class SettingsPage(QWidget):
    messageRequested = Signal(str, str)

    def __init__(self, ctx, config_reloader=None, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.config_reloader = config_reloader
        self._build()
        self.refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        intro = QLabel(
            "자주 바꾸는 옵션을 여기서 관리합니다. "
            "핫키 변경은 다음 실행부터 적용됩니다."
        )
        intro.setWordWrap(True)
        intro.setObjectName("subtle")
        root.addWidget(intro)

        options_panel = QFrame()
        options_panel.setObjectName("panel")
        options_layout = QVBoxLayout(options_panel)
        title = QLabel("자동 전환 설정")
        title.setObjectName("heading")
        title.setStyleSheet("font-size: 16px;")
        options_layout.addWidget(title)
        options_layout.addWidget(
            QLabel("자동 전환은 항상 켜진 상태로 동작하며, 여기서는 보호 시간만 조절합니다.")
        )

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        self._cooldown_ms = QSpinBox()
        self._cooldown_ms.setRange(0, 5000)
        self._return_guard_ms = QSpinBox()
        self._return_guard_ms.setRange(0, 5000)
        self._add_row(
            form,
            0,
            "전환 대기(ms)",
            self._cooldown_ms,
            "한 번 전환된 뒤 바로 다시 전환되지 않도록 잠깐 쉬는 시간입니다.",
        )
        self._add_row(
            form,
            1,
            "복귀 보호(ms)",
            self._return_guard_ms,
            "방금 넘어간 직후 원래 PC로 바로 되돌아오는 흔들림을 줄입니다.",
        )
        options_layout.addLayout(form)
        root.addWidget(options_panel)

        hotkey_panel = QFrame()
        hotkey_panel.setObjectName("panel")
        hotkey_layout = QVBoxLayout(hotkey_panel)
        hotkey_title = QLabel("핫키")
        hotkey_title.setObjectName("heading")
        hotkey_title.setStyleSheet("font-size: 16px;")
        hotkey_layout.addWidget(hotkey_title)
        hotkey_form = QGridLayout()
        hotkey_form.setHorizontalSpacing(10)
        hotkey_form.setVerticalSpacing(10)
        self._previous_hotkey = QLineEdit()
        self._next_hotkey = QLineEdit()
        self._toggle_auto_switch_hotkey = QLineEdit()
        self._quit_hotkey = QLineEdit()
        self._add_row(
            hotkey_form,
            0,
            "이전 PC",
            self._previous_hotkey,
            "기본값은 Ctrl+Alt+Q입니다. 현재 선택보다 이전 순서의 온라인 PC로 전환합니다.",
        )
        self._add_row(
            hotkey_form,
            1,
            "다음 PC",
            self._next_hotkey,
            "기본값은 Ctrl+Alt+E입니다. 현재 선택보다 다음 순서의 온라인 PC로 전환합니다.",
        )
        self._add_row(
            hotkey_form,
            2,
            "자동 전환 켜기/끄기",
            self._toggle_auto_switch_hotkey,
            "기본값은 Ctrl+Alt+Z입니다. 화면 경계 자동 전환을 켜거나 끕니다.",
        )
        self._add_row(
            hotkey_form,
            3,
            "앱 종료",
            self._quit_hotkey,
            "기본값은 Ctrl+Alt+Esc입니다. 창과 트레이를 함께 종료합니다.",
        )
        hotkey_layout.addLayout(hotkey_form)
        root.addWidget(hotkey_panel)

        self._status = QLabel("")
        self._status.setObjectName("subtle")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        actions = QHBoxLayout()
        self._reset_button = QPushButton("기본값으로 되돌리기")
        self._reset_button.clicked.connect(self._reset_defaults)
        self._save_button = QPushButton("설정 저장")
        self._save_button.setObjectName("primary")
        self._save_button.clicked.connect(self._save)
        actions.addStretch(1)
        actions.addWidget(self._reset_button)
        actions.addWidget(self._save_button)
        root.addLayout(actions)

    def _add_row(self, layout: QGridLayout, row: int, label: str, field, help_text: str) -> None:
        left = QWidget()
        left_layout = QHBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        text = QLabel(label)
        text.setMinimumWidth(90)
        left_layout.addWidget(text)
        left_layout.addWidget(HelpDot(help_text))
        left_layout.addStretch(1)
        layout.addWidget(left, row, 0)
        layout.addWidget(field, row, 1)

    def refresh(self) -> None:
        layout = self.ctx.layout
        if layout is not None:
            auto_switch = layout.auto_switch
            self._cooldown_ms.setValue(auto_switch.cooldown_ms)
            self._return_guard_ms.setValue(auto_switch.return_guard_ms)
        hotkeys = self.ctx.settings.hotkeys
        self._previous_hotkey.setText(hotkeys.previous_target)
        self._next_hotkey.setText(hotkeys.next_target)
        self._toggle_auto_switch_hotkey.setText(hotkeys.toggle_auto_switch)
        self._quit_hotkey.setText(hotkeys.quit_app)
        self._status.setText("")

    def _reset_defaults(self) -> None:
        self._previous_hotkey.setText(AppSettings().hotkeys.previous_target)
        self._next_hotkey.setText(AppSettings().hotkeys.next_target)
        self._toggle_auto_switch_hotkey.setText(AppSettings().hotkeys.toggle_auto_switch)
        self._quit_hotkey.setText(AppSettings().hotkeys.quit_app)
        if self.ctx.layout is not None:
            auto_switch = self.ctx.layout.auto_switch
            self._cooldown_ms.setValue(auto_switch.cooldown_ms)
            self._return_guard_ms.setValue(auto_switch.return_guard_ms)
        self._status.setText("기본 핫키와 현재 자동 전환 보호 시간으로 되돌렸습니다. 저장해야 반영됩니다.")

    def _save(self) -> None:
        if self.config_reloader is None or self.ctx.layout is None:
            self._status.setText("설정을 저장할 수 있는 경로가 아직 준비되지 않았습니다.")
            return
        try:
            hotkeys = validate_hotkey_settings(
                AppHotkeySettings(
                    previous_target=normalize_hotkey_string(self._previous_hotkey.text()),
                    next_target=normalize_hotkey_string(self._next_hotkey.text()),
                    toggle_auto_switch=normalize_hotkey_string(
                        self._toggle_auto_switch_hotkey.text()
                    ),
                    quit_app=normalize_hotkey_string(self._quit_hotkey.text()),
                )
            )
            next_layout = replace_auto_switch_settings(
                self.ctx.layout,
                enabled=True,
                cooldown_ms=self._cooldown_ms.value(),
                return_guard_ms=self._return_guard_ms.value(),
            )
            self.config_reloader.apply_layout(next_layout, persist=True, debounce_persist=False)
            self.ctx.replace_layout(next_layout)
            settings = AppSettings(hotkeys=hotkeys)
            self.config_reloader.save_settings(settings)
        except Exception as exc:
            self._status.setText(f"설정 저장 실패: {exc}")
            return

        self._status.setText("설정을 저장했습니다. 핫키 변경은 다음 실행부터 적용됩니다.")
        self.messageRequested.emit(
            "설정을 저장했습니다. 핫키 변경은 다음 실행부터 적용됩니다.",
            "success",
        )
