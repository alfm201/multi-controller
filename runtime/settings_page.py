"""Qt settings page for runtime options and hotkeys."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from runtime.app_settings import (
    AppHotkeySettings,
    AppSettings,
    BackupRetentionSettings,
    LogRetentionSettings,
    normalize_hotkey_string,
    validate_backup_retention_settings,
    validate_hotkey_settings,
    validate_log_retention_settings,
)
from runtime.hover_tooltip import HoverTooltip
from runtime.layouts import replace_auto_switch_settings


class HelpDot(QLabel):
    def __init__(self, tooltip: str, parent=None):
        super().__init__("?", parent)
        self.setObjectName("helpDot")
        self.setAlignment(Qt.AlignCenter)
        self.setFixedSize(18, 18)
        self.setMouseTracking(True)
        self._tooltip_text = tooltip
        self.setToolTip("")
        self._hover_tooltip = HoverTooltip(self)

    def enterEvent(self, event):  # noqa: N802
        self._show_tooltip(
            event.position().toPoint() if hasattr(event, "position") else self.rect().center()
        )
        super().enterEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        self._show_tooltip(event.position().toPoint())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._hover_tooltip.hide()
        super().leaveEvent(event)

    def _show_tooltip(self, local_pos) -> None:
        self._hover_tooltip.show_text(self._tooltip_text, self.mapToGlobal(local_pos))


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

        root.addWidget(self._build_auto_switch_panel())
        root.addWidget(self._build_backup_panel())
        root.addWidget(self._build_log_panel())
        root.addWidget(self._build_hotkey_panel())

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

    def _build_auto_switch_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        title = QLabel("자동 전환")
        title.setObjectName("heading")
        title.setStyleSheet("font-size: 16px;")
        layout.addWidget(title)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)

        self._cooldown_ms = QSpinBox()
        self._cooldown_ms.setRange(0, 5000)
        self._return_guard_ms = QSpinBox()
        self._return_guard_ms.setRange(0, 5000)
        self._configure_spin_box(self._cooldown_ms)
        self._configure_spin_box(self._return_guard_ms)

        self._add_row(
            form,
            0,
            "연속 전환 방지(ms)",
            self._cooldown_ms,
            "한 번 전환된 직후 다른 경계를 연속으로 넘어도 바로 또 전환되지 않게 쉬는 시간입니다.",
        )
        self._add_row(
            form,
            1,
            "되돌아감 방지(ms)",
            self._return_guard_ms,
            "방금 넘어온 경계 근처에서 바로 반대로 되돌아가는 흔들림을 막는 시간입니다.",
        )
        layout.addLayout(form)
        return panel

    def _build_backup_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        title = QLabel("백업 보관")
        title.setObjectName("heading")
        title.setStyleSheet("font-size: 16px;")
        layout.addWidget(title)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)

        self._backup_min_count = QSpinBox()
        self._backup_min_count.setRange(1, 1000)
        self._backup_max_age_days = QSpinBox()
        self._backup_max_age_days.setRange(1, 3650)
        self._configure_spin_box(self._backup_min_count)
        self._configure_spin_box(self._backup_max_age_days)

        self._add_row(
            form,
            0,
            "최소 유지 개수",
            self._backup_min_count,
            "최근 백업은 날짜와 관계없이 이 개수만큼 항상 남겨둡니다.",
        )
        self._add_row(
            form,
            1,
            "최대 보관 일수",
            self._backup_max_age_days,
            "최소 유지 개수를 넘는 오래된 백업 중에서 이 일수를 지난 항목은 정리합니다.",
        )
        layout.addLayout(form)
        return panel

    def _build_log_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        title = QLabel("로그 보관")
        title.setObjectName("heading")
        title.setStyleSheet("font-size: 16px;")
        layout.addWidget(title)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)

        self._log_retention_days = QSpinBox()
        self._log_retention_days.setRange(1, 3650)
        self._log_max_total_size_mb = QSpinBox()
        self._log_max_total_size_mb.setRange(1, 10240)
        self._log_max_total_size_mb.setSuffix(" MB")
        self._configure_spin_box(self._log_retention_days)
        self._configure_spin_box(self._log_max_total_size_mb)

        self._add_row(
            form,
            0,
            "보관 기간(일)",
            self._log_retention_days,
            "현재 날짜 기준으로 이 일수를 지난 로그는 정리합니다. 전날 로그는 압축 보관됩니다.",
        )
        self._add_row(
            form,
            1,
            "최대 총 용량",
            self._log_max_total_size_mb,
            "압축된 과거 로그까지 포함한 logs 폴더의 총 용량 상한입니다.",
        )
        layout.addLayout(form)
        return panel

    def _build_hotkey_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        title = QLabel("핫키")
        title.setObjectName("heading")
        title.setStyleSheet("font-size: 16px;")
        layout.addWidget(title)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)

        self._previous_hotkey = QLineEdit()
        self._next_hotkey = QLineEdit()
        self._toggle_auto_switch_hotkey = QLineEdit()
        self._quit_hotkey = QLineEdit()

        self._add_row(
            form,
            0,
            "이전 PC",
            self._previous_hotkey,
            "기본값은 Ctrl+Alt+Q입니다. 현재 선택보다 앞선 순서의 온라인 PC로 전환합니다.",
        )
        self._add_row(
            form,
            1,
            "다음 PC",
            self._next_hotkey,
            "기본값은 Ctrl+Alt+E입니다. 현재 선택보다 다음 순서의 온라인 PC로 전환합니다.",
        )
        self._add_row(
            form,
            2,
            "자동 전환 켜기/끄기",
            self._toggle_auto_switch_hotkey,
            "기본값은 Ctrl+Alt+R입니다. 화면 경계 자동 전환을 켜거나 끕니다.",
        )
        self._add_row(
            form,
            3,
            "앱 종료",
            self._quit_hotkey,
            "기본값은 Ctrl+Alt+Esc입니다. 창과 트레이를 함께 종료합니다.",
        )
        layout.addLayout(form)
        return panel

    def _add_row(self, layout: QGridLayout, row: int, label: str, field, help_text: str) -> None:
        left = QWidget()
        left_layout = QHBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        text = QLabel(label)
        text.setMinimumWidth(110)
        left_layout.addWidget(text)
        left_layout.addWidget(HelpDot(help_text))
        left_layout.addStretch(1)
        layout.addWidget(left, row, 0)
        layout.addWidget(field, row, 1)

    def _configure_spin_box(self, field: QSpinBox) -> None:
        field.setButtonSymbols(QAbstractSpinBox.UpDownArrows)
        field.setAccelerated(True)

    def refresh(self) -> None:
        layout = self.ctx.layout
        if layout is not None:
            auto_switch = layout.auto_switch
            self._cooldown_ms.setValue(auto_switch.cooldown_ms)
            self._return_guard_ms.setValue(auto_switch.return_guard_ms)

        backup_settings = self.ctx.settings.backups
        self._backup_min_count.setValue(backup_settings.min_count)
        self._backup_max_age_days.setValue(backup_settings.max_age_days)

        log_settings = self.ctx.settings.logs
        self._log_retention_days.setValue(log_settings.retention_days)
        self._log_max_total_size_mb.setValue(log_settings.max_total_size_mb)

        hotkeys = self.ctx.settings.hotkeys
        self._previous_hotkey.setText(hotkeys.previous_target)
        self._next_hotkey.setText(hotkeys.next_target)
        self._toggle_auto_switch_hotkey.setText(hotkeys.toggle_auto_switch)
        self._quit_hotkey.setText(hotkeys.quit_app)
        self._status.setText("")

    def _reset_defaults(self) -> None:
        defaults = AppSettings()
        self._previous_hotkey.setText(defaults.hotkeys.previous_target)
        self._next_hotkey.setText(defaults.hotkeys.next_target)
        self._toggle_auto_switch_hotkey.setText(defaults.hotkeys.toggle_auto_switch)
        self._quit_hotkey.setText(defaults.hotkeys.quit_app)
        self._backup_min_count.setValue(defaults.backups.min_count)
        self._backup_max_age_days.setValue(defaults.backups.max_age_days)
        self._log_retention_days.setValue(defaults.logs.retention_days)
        self._log_max_total_size_mb.setValue(defaults.logs.max_total_size_mb)
        if self.ctx.layout is not None:
            auto_switch = self.ctx.layout.auto_switch
            self._cooldown_ms.setValue(auto_switch.cooldown_ms)
            self._return_guard_ms.setValue(auto_switch.return_guard_ms)
        self._status.setText("기본값으로 되돌렸습니다. 저장하면 반영됩니다.")

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
            backups = validate_backup_retention_settings(
                BackupRetentionSettings(
                    min_count=self._backup_min_count.value(),
                    max_age_days=self._backup_max_age_days.value(),
                )
            )
            logs = validate_log_retention_settings(
                LogRetentionSettings(
                    retention_days=self._log_retention_days.value(),
                    max_total_size_mb=self._log_max_total_size_mb.value(),
                )
            )
            next_layout = replace_auto_switch_settings(
                self.ctx.layout,
                enabled=True,
                cooldown_ms=self._cooldown_ms.value(),
                return_guard_ms=self._return_guard_ms.value(),
            )
            settings = AppSettings(hotkeys=hotkeys, backups=backups, logs=logs)
            self.config_reloader.save_layout_and_settings(next_layout, settings)
        except Exception as exc:
            self._status.setText(f"설정 저장 실패: {exc}")
            return

        self._status.setText("설정을 저장했습니다. 핫키 변경은 다음 실행부터 적용됩니다.")
        self.messageRequested.emit(
            "설정을 저장했습니다. 핫키 변경은 다음 실행부터 적용됩니다.",
            "success",
        )
