"""Qt settings page for runtime options and hotkeys."""

from __future__ import annotations

from dataclasses import replace
import logging
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
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
from runtime.app_update import (
    AUTO_UPDATE_CHECK_INTERVAL_SEC,
    AppUpdateManager,
    format_update_timestamp,
    seconds_until_next_update_check,
)
from runtime.app_version import (
    build_update_status_text,
    check_for_updates,
    get_current_version_label,
)
from runtime.hover_tooltip import HoverTooltip
from runtime.layouts import replace_auto_switch_settings
from runtime.update_domain import (
    UPDATE_ACTION_DOWNLOAD,
    UPDATE_ACTION_INSTALL,
    UPDATE_ACTION_MANUAL_CHECK,
    UPDATE_ACTION_REMOTE_REQUEST,
    UPDATE_ACTION_SCHEDULED_CHECK,
    UPDATE_ACTION_STARTUP_CHECK,
    UPDATE_ORIGIN_AUTO,
    UPDATE_ORIGIN_MANUAL,
    UPDATE_ORIGIN_REMOTE_COMMAND,
    UPDATE_ORIGIN_STARTUP,
    UPDATE_STAGE_CHECKING,
    UPDATE_STAGE_DOWNLOADING,
    UPDATE_STAGE_DOWNLOADED,
    UPDATE_STAGE_FAILED,
    UPDATE_STAGE_INSTALLING,
    UPDATE_STAGE_NO_UPDATE,
    UPDATE_STAGE_UPDATE_AVAILABLE,
    UPDATE_TARGET_SELF,
    build_update_notice_payload,
    make_remote_update_status_payload,
    new_update_session_id,
)


class StepperSpinBox(QSpinBox):
    BUTTON_COLUMN_WIDTH = 32
    REPEAT_INITIAL_DELAY_MS = 320
    REPEAT_START_INTERVAL_MS = 180
    REPEAT_MIN_INTERVAL_MS = 60
    REPEAT_ACCELERATION = 0.82

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.setAccelerated(True)
        self.setMinimumHeight(32)
        self.setMinimumWidth(240)
        self.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        line_edit = self.lineEdit()
        if line_edit is not None:
            line_edit.setFrame(False)
            line_edit.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            line_edit.setStyleSheet(
                "background: transparent; border: none; padding: 0; margin: 0; font-size: 14px; color: #172033;"
            )
            line_edit.setAttribute(Qt.WA_TranslucentBackground, True)
            line_edit.setAutoFillBackground(False)
        self._step_up_button = QToolButton(self)
        self._step_up_button.setObjectName("spinStepButtonUp")
        self._step_up_button.setText("")
        self._step_up_button.setArrowType(Qt.UpArrow)
        self._step_up_button.setFocusPolicy(Qt.NoFocus)
        self._step_up_button.setCursor(Qt.PointingHandCursor)
        self._step_down_button = QToolButton(self)
        self._step_down_button.setObjectName("spinStepButtonDown")
        self._step_down_button.setText("")
        self._step_down_button.setArrowType(Qt.DownArrow)
        self._step_down_button.setFocusPolicy(Qt.NoFocus)
        self._step_down_button.setCursor(Qt.PointingHandCursor)
        self._repeat_direction = 0
        self._repeat_interval_ms = self.REPEAT_START_INTERVAL_MS
        self._repeat_timer = QTimer(self)
        self._repeat_timer.setSingleShot(True)
        self._repeat_timer.timeout.connect(self._repeat_step)
        self._step_up_button.pressed.connect(lambda: self._start_repeat(1))
        self._step_up_button.released.connect(self._stop_repeat)
        self._step_down_button.pressed.connect(lambda: self._start_repeat(-1))
        self._step_down_button.released.connect(self._stop_repeat)
        self.valueChanged.connect(lambda _value: self._sync_step_buttons())
        self._sync_step_buttons()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._layout_step_buttons()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._layout_step_buttons()
        self._sync_step_buttons()

    def wheelEvent(self, event):  # noqa: N802
        event.ignore()

    def _layout_step_buttons(self) -> None:
        rect = self.rect()
        button_width = self.BUTTON_COLUMN_WIDTH
        button_height = max(1, rect.height() // 2)
        x = max(0, rect.width() - button_width - 1)
        self._step_up_button.setGeometry(x, 1, button_width, button_height)
        self._step_down_button.setGeometry(
            x,
            max(1, rect.height() - button_height - 1),
            button_width,
            button_height,
        )
        line_edit = self.lineEdit()
        if line_edit is not None:
            line_edit.setTextMargins(0, 0, button_width + 6, 0)

    def _sync_step_buttons(self) -> None:
        flags = self.stepEnabled()
        self._step_up_button.setEnabled(bool(flags & QAbstractSpinBox.StepUpEnabled))
        self._step_down_button.setEnabled(bool(flags & QAbstractSpinBox.StepDownEnabled))

    def _start_repeat(self, direction: int) -> None:
        button = self._step_up_button if direction > 0 else self._step_down_button
        if not button.isEnabled():
            return
        self._repeat_direction = direction
        self._repeat_interval_ms = self.REPEAT_START_INTERVAL_MS
        self._step_once(direction)
        self._repeat_timer.start(self.REPEAT_INITIAL_DELAY_MS)

    def _repeat_step(self) -> None:
        if self._repeat_direction == 0:
            return
        self._step_once(self._repeat_direction)
        self._repeat_interval_ms = max(
            self.REPEAT_MIN_INTERVAL_MS,
            int(self._repeat_interval_ms * self.REPEAT_ACCELERATION),
        )
        self._repeat_timer.start(self._repeat_interval_ms)

    def _stop_repeat(self) -> None:
        self._repeat_direction = 0
        self._repeat_timer.stop()

    def _step_once(self, direction: int) -> None:
        if direction > 0:
            self.stepUp()
        else:
            self.stepDown()


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
    versionCheckFinished = Signal(object)
    updateInstallFinished = Signal(object)
    updateNoticeChanged = Signal(object)
    remoteUpdateStatusChanged = Signal(object)
    AUTO_UPDATE_CHECK_INTERVAL_MS = AUTO_UPDATE_CHECK_INTERVAL_SEC * 1000
    AUTO_UPDATE_CHECK_START_DELAY_MS = 1500
    STARTUP_UPDATE_CHECK_DELAY_MS = 1500

    def __init__(
        self,
        ctx,
        config_reloader=None,
        *,
        version_provider=None,
        update_checker=None,
        update_installer=None,
        request_quit=None,
        ui_mode: str = "gui",
        parent=None,
    ):
        super().__init__(parent)
        self.ctx = ctx
        self.config_reloader = config_reloader
        self.setFocusPolicy(Qt.StrongFocus)
        self._version_provider = get_current_version_label if version_provider is None else version_provider
        self._update_checker = check_for_updates if update_checker is None else update_checker
        self._update_installer = AppUpdateManager() if update_installer is None else update_installer
        self._request_quit = request_quit
        self._ui_mode = ui_mode
        self._version_check_running = False
        self._update_install_running = False
        self._latest_update_result = None
        self._update_notice_payload = {"visible": False}
        self._pending_remote_requester_id = None
        self._pending_remote_session_id = None
        self._startup_check_scheduled = False
        self._startup_check_completed = False
        self._is_disposed = False
        self._startup_check_timer = QTimer(self)
        self._startup_check_timer.setSingleShot(True)
        self._startup_check_timer.timeout.connect(self._start_startup_update_check)
        self._auto_check_timer = QTimer(self)
        self._auto_check_timer.setSingleShot(True)
        self._auto_check_timer.timeout.connect(lambda: self._start_version_check(trigger="auto"))
        self.versionCheckFinished.connect(self._apply_version_check_result)
        self.updateInstallFinished.connect(self._apply_update_install_result)
        self.updateNoticeChanged.connect(self._apply_update_notice_payload)
        self._build()
        self.refresh()

    def closeEvent(self, event):  # noqa: N802
        self._is_disposed = True
        self._startup_check_timer.stop()
        self._auto_check_timer.stop()
        super().closeEvent(event)

    def refresh(self) -> None:
        self._current_version_value.setText(self._version_provider())
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

        update_settings = self.ctx.settings.updates
        self._auto_update_checkbox.blockSignals(True)
        self._auto_update_checkbox.setChecked(update_settings.auto_check_enabled)
        self._auto_update_checkbox.blockSignals(False)
        self._schedule_startup_update_check()
        self._sync_auto_update_schedule(trigger_initial=self._latest_update_result is None)

        hotkeys = self.ctx.settings.hotkeys
        self._previous_hotkey.setText(hotkeys.previous_target)
        self._next_hotkey.setText(hotkeys.next_target)
        self._toggle_auto_switch_hotkey.setText(hotkeys.toggle_auto_switch)
        self._quit_hotkey.setText(hotkeys.quit_app)
        self._sync_update_action_state()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        self._content_scroll = QScrollArea()
        self._content_scroll.setObjectName("settingsContentScroll")
        self._content_scroll.setWidgetResizable(True)
        self._content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._content_scroll.setFrameShape(QFrame.NoFrame)
        self._content_scroll.setStyleSheet(
            "QScrollArea#settingsContentScroll { border: none; background: transparent; }"
        )

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)
        content_layout.addWidget(self._build_version_panel())
        content_layout.addWidget(self._build_auto_switch_panel())
        content_layout.addWidget(self._build_backup_panel())
        content_layout.addWidget(self._build_log_panel())
        content_layout.addWidget(self._build_hotkey_panel())
        content_layout.addStretch(1)
        self._content_scroll.setWidget(content)
        root.addWidget(self._content_scroll, 1)

        self._footer = QFrame()
        footer_layout = QHBoxLayout(self._footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(0)
        self._footer_bar = QFrame()
        self._footer_bar.setObjectName("panelAlt")
        self._footer_bar.setMinimumWidth(420)
        footer_bar_layout = QHBoxLayout(self._footer_bar)
        footer_bar_layout.setContentsMargins(12, 10, 12, 10)
        footer_bar_layout.setSpacing(12)
        footer_bar_layout.addStretch(1)

        self._reset_button = QPushButton("기본값으로 되돌리기")
        self._reset_button.clicked.connect(self._reset_defaults)
        self._save_button = QPushButton("설정 저장")
        self._save_button.setObjectName("primary")
        self._save_button.clicked.connect(self._save)
        footer_bar_layout.addWidget(self._reset_button)
        footer_bar_layout.addWidget(self._save_button)
        footer_layout.addWidget(self._footer_bar, 1)
        root.addWidget(self._footer)

    def _build_version_panel(self) -> QFrame:
        panel = self._create_panel("버전")
        layout = panel.layout()

        current_row = QHBoxLayout()
        current_label = QLabel("현재 버전")
        current_label.setObjectName("subtle")
        self._current_version_value = QLabel("")
        current_row.addWidget(current_label)
        current_row.addWidget(self._current_version_value)
        current_row.addStretch(1)
        layout.addLayout(current_row)

        auto_update_row = QWidget()
        auto_update_layout = QHBoxLayout(auto_update_row)
        auto_update_layout.setContentsMargins(0, 0, 0, 0)
        auto_update_layout.setSpacing(6)
        self._auto_update_checkbox = QCheckBox("자동 업데이트 확인")
        self._auto_update_checkbox.toggled.connect(self._on_auto_update_toggled)
        auto_update_layout.addWidget(self._auto_update_checkbox)
        auto_update_layout.addWidget(HelpDot("앱이 주기적으로 업데이트를 확인합니다."))
        auto_update_layout.addStretch(1)
        layout.addWidget(auto_update_row)

        buttons = QHBoxLayout()
        self._version_check_button = QPushButton("업데이트 확인")
        self._version_check_button.setFocusPolicy(Qt.NoFocus)
        self._version_check_button.clicked.connect(lambda: self._start_version_check(trigger="manual"))
        self._install_update_button = QPushButton("업데이트 설치")
        self._install_update_button.setObjectName("primary")
        self._install_update_button.setFocusPolicy(Qt.NoFocus)
        self._install_update_button.clicked.connect(self._install_update)
        self._install_update_button.hide()
        buttons.addWidget(self._version_check_button)
        buttons.addWidget(self._install_update_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self._update_notice = QFrame()
        self._update_notice.setObjectName("panelAlt")
        notice_layout = QVBoxLayout(self._update_notice)
        notice_layout.setContentsMargins(10, 10, 10, 10)
        notice_layout.setSpacing(4)
        self._update_notice_title = QLabel("새로운 업데이트가 있습니다!")
        self._update_notice_title.setObjectName("heading")
        self._update_notice_title.setStyleSheet("font-size: 14px; background: transparent;")
        self._update_notice_detail = QLabel("")
        self._update_notice_detail.setObjectName("subtle")
        self._update_notice_detail.setWordWrap(True)
        self._update_notice_detail.setStyleSheet("background: transparent;")
        notice_layout.addWidget(self._update_notice_title)
        notice_layout.addWidget(self._update_notice_detail)
        self._update_notice.hide()
        layout.addWidget(self._update_notice)

        return panel

    def _build_auto_switch_panel(self) -> QFrame:
        panel = self._create_panel("자동 전환")
        form = self._create_form(panel)

        self._cooldown_ms = StepperSpinBox()
        self._cooldown_ms.setRange(0, 5000)
        self._return_guard_ms = StepperSpinBox()
        self._return_guard_ms.setRange(0, 5000)

        self._add_row(
            form,
            0,
            "연속 전환 방지(ms)",
            self._cooldown_ms,
            "한 번 전환된 직후에 다른 경계를 지나도 바로 다시 전환되지 않도록 잡아 두는 시간입니다.",
        )
        self._add_row(
            form,
            1,
            "되돌아감 방지(ms)",
            self._return_guard_ms,
            "방금 넘은 경계 근처에서 마우스가 바로 반대로 튀지 않게 막는 시간입니다.",
        )
        return panel

    def _build_backup_panel(self) -> QFrame:
        panel = self._create_panel("설정 백업 보관")
        form = self._create_form(panel)

        self._backup_min_count = StepperSpinBox()
        self._backup_min_count.setRange(1, 1000)
        self._backup_max_age_days = StepperSpinBox()
        self._backup_max_age_days.setRange(1, 3650)

        self._add_row(
            form,
            0,
            "최소 유지 개수",
            self._backup_min_count,
            "최근 백업은 날짜와 상관없이 이 개수만큼 항상 남겨 둡니다.",
        )
        self._add_row(
            form,
            1,
            "최대 보관 일수",
            self._backup_max_age_days,
            "최소 유지 개수를 넘는 오래된 백업은 이 기준에 따라 정리합니다.",
        )
        return panel

    def _build_log_panel(self) -> QFrame:
        panel = self._create_panel("로그 보관")
        form = self._create_form(panel)

        self._log_retention_days = StepperSpinBox()
        self._log_retention_days.setRange(1, 3650)
        self._log_max_total_size_mb = StepperSpinBox()
        self._log_max_total_size_mb.setRange(1, 10240)
        self._log_max_total_size_mb.setSuffix(" MB")

        self._add_row(
            form,
            0,
            "보관 기간(일)",
            self._log_retention_days,
            "현재 날짜 기준으로 오래된 로그를 정리할 때 사용하는 기간입니다.",
        )
        self._add_row(
            form,
            1,
            "최대 총 용량",
            self._log_max_total_size_mb,
            "로그 폴더 전체 용량이 이 한도를 넘기면 오래된 로그부터 정리합니다.",
        )
        return panel

    def _build_hotkey_panel(self) -> QFrame:
        panel = self._create_panel("단축키")
        form = self._create_form(panel)

        self._previous_hotkey = QLineEdit()
        self._next_hotkey = QLineEdit()
        self._toggle_auto_switch_hotkey = QLineEdit()
        self._quit_hotkey = QLineEdit()

        self._add_row(
            form,
            0,
            "이전 PC",
            self._previous_hotkey,
            "기본값은 Ctrl+Alt+Q이며 현재보다 앞선 순서의 PC로 전환합니다.",
        )
        self._add_row(
            form,
            1,
            "다음 PC",
            self._next_hotkey,
            "기본값은 Ctrl+Alt+E이며 현재보다 다음 순서의 PC로 전환합니다.",
        )
        self._add_row(
            form,
            2,
            "자동 전환 켜기/끄기",
            self._toggle_auto_switch_hotkey,
            "기본값은 Ctrl+Alt+R이며 화면 경계 자동 전환을 켜거나 끕니다.",
        )
        self._add_row(
            form,
            3,
            "앱 종료",
            self._quit_hotkey,
            "기본값은 Ctrl+Alt+Esc이며 트레이까지 포함해 앱을 종료합니다.",
        )
        return panel

    def _create_panel(self, title_text: str) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        title = QLabel(title_text)
        title.setObjectName("heading")
        title.setStyleSheet("font-size: 16px;")
        layout.addWidget(title)
        return panel

    def _create_form(self, panel: QFrame) -> QGridLayout:
        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        panel.layout().addLayout(form)
        return form

    def _add_row(self, layout: QGridLayout, row: int, label: str, field, help_text: str) -> None:
        left = QWidget()
        left_layout = QHBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        text = QLabel(label)
        text.setMinimumWidth(120)
        left_layout.addWidget(text)
        left_layout.addWidget(HelpDot(help_text))
        left_layout.addStretch(1)
        layout.addWidget(left, row, 0)
        if hasattr(field, "setMinimumWidth"):
            field.setMinimumWidth(240)
        layout.addWidget(field, row, 1)

    def _on_auto_update_toggled(self, checked: bool) -> None:
        self._sync_auto_update_schedule(trigger_initial=checked)

    def _sync_auto_update_schedule(self, *, trigger_initial: bool) -> None:
        self._auto_check_timer.stop()
        if not self._auto_update_checkbox.isChecked():
            return
        remaining_ms = int(
            seconds_until_next_update_check(self.ctx.settings.updates.last_checked_at) * 1000
        )
        if trigger_initial and remaining_ms <= 0:
            delay_ms = self.AUTO_UPDATE_CHECK_START_DELAY_MS
        else:
            delay_ms = max(remaining_ms, 0)
        self._auto_check_timer.start(delay_ms)

    def _schedule_startup_update_check(self) -> None:
        if self._startup_check_scheduled or self._startup_check_completed:
            return
        self._startup_check_scheduled = True
        self._startup_check_timer.start(self.STARTUP_UPDATE_CHECK_DELAY_MS)

    def _start_startup_update_check(self) -> None:
        self._startup_check_scheduled = False
        if self._startup_check_completed:
            return
        if self._version_check_running or self._update_install_running:
            self._schedule_startup_update_check()
            return
        self._startup_check_completed = True
        self._start_version_check(trigger="startup")

    def _start_version_check(self, *, trigger: str) -> None:
        if self._version_check_running or self._update_install_running:
            return
        self._version_check_running = True
        self._sync_update_action_state()
        threading.Thread(
            target=self._run_version_check,
            kwargs={"trigger": trigger},
            daemon=True,
            name="version-check",
        ).start()

    def _run_version_check(self, *, trigger: str) -> None:
        try:
            result = self._update_checker()
            payload = {"result": result, "trigger": trigger, "error": None}
        except Exception as exc:
            payload = {"result": None, "trigger": trigger, "error": str(exc)}
        if self._is_disposed:
            return
        try:
            self.versionCheckFinished.emit(payload)
        except RuntimeError:
            return

    def _apply_version_check_result(self, payload: dict) -> None:
        self._version_check_running = False
        self._sync_update_action_state()
        trigger = payload.get("trigger", "manual")
        error_text = payload.get("error")
        result = payload.get("result")
        self._record_update_check_timestamp()
        self._sync_auto_update_schedule(trigger_initial=False)

        if error_text:
            self._set_update_notice(None)
            if trigger == "manual":
                self.messageRequested.emit(f"버전 확인 실패: {error_text}", "warning")
            if trigger in {"remote_visible", "remote_background"}:
                self._emit_remote_update_status(UPDATE_STAGE_FAILED, error_text)
            return

        self._latest_update_result = result
        text, tone = build_update_status_text(result)
        self._set_update_notice(result, trigger=trigger)
        if trigger == "manual" and result is not None and result.status != "update_available":
            self.messageRequested.emit(text, tone)
        if trigger in {"remote_visible", "remote_background"} and result is not None and result.status != "update_available":
            self._emit_remote_update_status(
                UPDATE_STAGE_NO_UPDATE,
                text,
                current_version=result.current_version,
                latest_version=result.latest_version,
            )
        if trigger in {"remote_visible", "remote_background"} and result is not None and result.status == "update_available":
            self._start_update_install(trigger=trigger)

    def _set_update_notice(self, result, *, trigger: str | None = None) -> None:
        if result is None or result.status != "update_available":
            self._publish_update_notice({"visible": False})
            self._sync_update_action_state()
            return
        self._publish_update_notice(
            build_update_notice_payload(
                stage=UPDATE_STAGE_UPDATE_AVAILABLE,
                current_version=result.current_version,
                target_version=result.latest_version,
                tag_name=result.latest_tag_name,
                action=self._update_action_for_trigger(trigger or "manual"),
                origin=self._update_origin_for_trigger(trigger or "manual"),
                target_kind=UPDATE_TARGET_SELF,
                button_enabled=not self._update_install_running,
            )
        )
        self._sync_update_action_state()

    def _publish_update_notice(self, payload: dict) -> None:
        self._update_notice_payload = dict(payload)
        self.updateNoticeChanged.emit(dict(self._update_notice_payload))

    def _apply_update_notice_payload(self, payload) -> None:
        notice = {"visible": False} if payload is None else dict(payload)
        self._update_notice_payload = notice
        visible = bool(notice.get("visible"))
        self._update_notice.setVisible(visible)
        self._install_update_button.setVisible(bool(notice.get("button_visible", visible)))
        self._install_update_button.setEnabled(bool(notice.get("button_enabled", True)))
        self._install_update_button.setText(notice.get("button_text", "업데이트 설치"))
        if not visible:
            self._update_notice_title.setText("새로운 업데이트가 있습니다!")
            self._update_notice_detail.setText("")
            return
        self._update_notice_title.setText(notice.get("title", "새로운 업데이트가 있습니다!"))
        self._update_notice_detail.setText(notice.get("detail", ""))

    def _set_update_progress_notice(self, title: str, detail: str = "") -> None:
        payload = build_update_notice_payload(
            stage=UPDATE_STAGE_INSTALLING if detail else UPDATE_STAGE_DOWNLOADING,
            current_version="" if self._latest_update_result is None else self._latest_update_result.current_version,
            target_version="" if self._latest_update_result is None else self._latest_update_result.latest_version,
            tag_name="" if self._latest_update_result is None else self._latest_update_result.latest_tag_name,
            action=UPDATE_ACTION_INSTALL if detail else UPDATE_ACTION_DOWNLOAD,
            origin=UPDATE_ORIGIN_MANUAL,
            target_kind=UPDATE_TARGET_SELF,
            detail=detail or title,
            button_enabled=False,
        )
        payload["title"] = title
        self._publish_update_notice(payload)

    def _report_update_download_progress(
        self,
        progress: int | None,
        downloaded_bytes: int,
        total_bytes: int | None,
    ) -> None:
        if progress is not None:
            detail = f"설치 파일 다운로드 중... {progress}%"
        elif total_bytes:
            detail = f"설치 파일 다운로드 중... {downloaded_bytes:,} / {total_bytes:,} bytes"
        else:
            detail = f"설치 파일 다운로드 중... {downloaded_bytes:,} bytes"
        self._set_update_progress_notice("업데이트를 설치하는 중입니다...", detail)

    def _build_update_ready_notice(self, *, auto_trigger: bool) -> dict:
        return build_update_notice_payload(
            stage=UPDATE_STAGE_DOWNLOADED,
            current_version="" if self._latest_update_result is None else self._latest_update_result.current_version,
            target_version="" if self._latest_update_result is None else self._latest_update_result.latest_version,
            tag_name="" if self._latest_update_result is None else self._latest_update_result.latest_tag_name,
            action=UPDATE_ACTION_INSTALL,
            origin=UPDATE_ORIGIN_AUTO if auto_trigger else UPDATE_ORIGIN_MANUAL,
            target_kind=UPDATE_TARGET_SELF,
            auto_trigger=auto_trigger,
            button_enabled=False,
        )

    def _install_update(self) -> None:
        self._start_update_install(trigger="manual")

    def _start_update_install(self, *, trigger: str) -> None:
        if self._update_install_running:
            return
        result = self._latest_update_result
        if result is None:
            self.messageRequested.emit("먼저 업데이트 확인을 진행해 주세요.", "warning")
            return
        if result.status != "update_available":
            self.messageRequested.emit("현재 설치할 새 업데이트가 없습니다.", "warning")
            return
        if not getattr(result, "installer_url", None):
            self.messageRequested.emit("업데이트 설치 파일을 찾을 수 없습니다.", "warning")
            return
        self._update_install_running = True
        self._sync_update_action_state()
        if trigger in {"remote_visible", "remote_background"}:
            self._emit_remote_update_status(
                UPDATE_STAGE_DOWNLOADING,
                "",
                current_version=result.current_version,
                latest_version=result.latest_version,
            )
        self._set_update_progress_notice(
            "업데이트를 설치하는 중입니다...",
            "설치 파일 다운로드를 준비하는 중입니다...",
        )
        threading.Thread(
            target=self._run_update_install,
            kwargs={"result": result, "trigger": trigger},
            daemon=True,
            name=f"update-install-{trigger}",
        ).start()

    def _run_update_install(self, *, result, trigger: str) -> None:
        try:
            kwargs = {
                "relaunch_mode": self._relaunch_mode_for_trigger(trigger),
                "progress_callback": self._report_update_download_progress,
                "remote_update_requester_id": self._pending_remote_requester_id,
                "remote_update_target_id": getattr(getattr(self.ctx, "self_node", None), "node_id", ""),
                "remote_update_session_id": self._pending_remote_session_id,
                "remote_update_current_version": getattr(result, "current_version", ""),
                "remote_update_latest_version": getattr(result, "latest_version", ""),
            }
            try:
                prepared = self._update_installer.prepare_update(result, **kwargs)
            except TypeError:
                kwargs.pop("progress_callback", None)
                try:
                    prepared = self._update_installer.prepare_update(result, **kwargs)
                except TypeError:
                    kwargs.pop("remote_update_requester_id", None)
                    kwargs.pop("remote_update_target_id", None)
                    kwargs.pop("remote_update_session_id", None)
                    kwargs.pop("remote_update_current_version", None)
                    kwargs.pop("remote_update_latest_version", None)
                    prepared = self._update_installer.prepare_update(result, **kwargs)
            payload = {"prepared": prepared, "trigger": trigger, "error": None}
        except Exception as exc:
            payload = {"prepared": None, "trigger": trigger, "error": str(exc)}
        self.updateInstallFinished.emit(payload)

    def _apply_update_install_result(self, payload: dict) -> None:
        self._update_install_running = False
        self._sync_update_action_state()
        trigger = payload.get("trigger", "manual")
        error_text = payload.get("error")
        if error_text:
            self._set_update_notice(self._latest_update_result, trigger=trigger)
            if trigger in {"remote_visible", "remote_background"}:
                self._emit_remote_update_status(
                    UPDATE_STAGE_FAILED,
                    error_text,
                    current_version=(
                        "" if self._latest_update_result is None else self._latest_update_result.current_version
                    ),
                    latest_version=(
                        "" if self._latest_update_result is None else self._latest_update_result.latest_version
                    ),
                )
            return

        if trigger in {"remote_visible", "remote_background"}:
            self._emit_remote_update_status(
                UPDATE_STAGE_INSTALLING,
                "",
                current_version=(
                    "" if self._latest_update_result is None else self._latest_update_result.current_version
                ),
                latest_version=(
                    "" if self._latest_update_result is None else self._latest_update_result.latest_version
                ),
            )
            self._pending_remote_requester_id = None
            self._pending_remote_session_id = None
        self._publish_update_notice(self._build_update_ready_notice(auto_trigger=trigger in {"auto", "remote_background"}))
        if callable(self._request_quit):
            if trigger in {"remote_visible", "remote_background"}:
                QTimer.singleShot(350, self._request_quit)
            else:
                self._request_quit()
            return

    def _sync_update_action_state(self) -> None:
        busy = self._version_check_running or self._update_install_running
        self._version_check_button.setEnabled(not busy)
        self._install_update_button.setEnabled(
            not busy and self._latest_update_result is not None and self._latest_update_result.status == "update_available"
        )

    def _record_update_check_timestamp(self) -> None:
        timestamp = format_update_timestamp()
        next_updates = replace(self.ctx.settings.updates, last_checked_at=timestamp)
        next_settings = replace(self.ctx.settings, updates=next_updates)
        if hasattr(self.ctx, "replace_settings"):
            self.ctx.replace_settings(next_settings)
        else:
            self.ctx.settings = next_settings
        if self.config_reloader is None:
            return
        try:
            self.config_reloader.save_settings(next_settings)
        except Exception as exc:
            logging.warning("[UPDATE] failed to persist update check timestamp: %s", exc)

    def _relaunch_mode_for_trigger(self, trigger: str) -> str:
        if trigger in {"auto", "remote_background"}:
            return "tray"
        if trigger == "remote_visible":
            return "gui"
        return "preserve"

    def start_remote_update(self, *, background: bool, requester_id: str | None = None) -> None:
        if self._version_check_running or self._update_install_running:
            return
        self._pending_remote_requester_id = str(requester_id or "").strip() or None
        self._pending_remote_session_id = (
            None if self._pending_remote_requester_id is None else new_update_session_id()
        )
        trigger = "remote_background" if background else "remote_visible"
        if self._pending_remote_requester_id:
            self._emit_remote_update_status(
                UPDATE_STAGE_CHECKING,
                "",
                current_version=self._version_provider(),
                latest_version=(
                    "" if self._latest_update_result is None else self._latest_update_result.latest_version
                ),
            )
        if self._latest_update_result is not None and self._latest_update_result.status == "update_available":
            self._start_update_install(trigger=trigger)
            return
        self._start_version_check(trigger=trigger)

    def _emit_remote_update_status(
        self,
        status: str,
        detail: str = "",
        *,
        current_version: str = "",
        latest_version: str = "",
    ) -> None:
        requester_id = self._pending_remote_requester_id
        if not requester_id:
            return
        self.remoteUpdateStatusChanged.emit(
            make_remote_update_status_payload(
                target_id=self.ctx.self_node.node_id,
                requester_id=requester_id,
                status=status,
                detail=str(detail or ""),
                session_id=self._pending_remote_session_id,
                current_version=current_version,
                latest_version=latest_version,
                action=UPDATE_ACTION_REMOTE_REQUEST,
                origin=UPDATE_ORIGIN_REMOTE_COMMAND,
            )
        )
        if status in {UPDATE_STAGE_FAILED, UPDATE_STAGE_NO_UPDATE}:
            self._pending_remote_requester_id = None
            self._pending_remote_session_id = None

    @staticmethod
    def _update_origin_for_trigger(trigger: str) -> str:
        if trigger == "auto":
            return UPDATE_ORIGIN_AUTO
        if trigger == "startup":
            return UPDATE_ORIGIN_STARTUP
        if trigger in {"remote_visible", "remote_background"}:
            return UPDATE_ORIGIN_REMOTE_COMMAND
        return UPDATE_ORIGIN_MANUAL

    @staticmethod
    def _update_action_for_trigger(trigger: str) -> str:
        if trigger == "auto":
            return UPDATE_ACTION_SCHEDULED_CHECK
        if trigger == "startup":
            return UPDATE_ACTION_STARTUP_CHECK
        if trigger in {"remote_visible", "remote_background"}:
            return UPDATE_ACTION_REMOTE_REQUEST
        return UPDATE_ACTION_MANUAL_CHECK

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
        self._auto_update_checkbox.setChecked(defaults.updates.auto_check_enabled)
        if self.ctx.layout is not None:
            auto_switch = self.ctx.layout.auto_switch
            self._cooldown_ms.setValue(auto_switch.cooldown_ms)
            self._return_guard_ms.setValue(auto_switch.return_guard_ms)
        self.messageRequested.emit("기본값으로 되돌렸습니다. 저장하면 반영됩니다.", "neutral")

    def _save(self) -> None:
        if self.config_reloader is None or self.ctx.layout is None:
            self.messageRequested.emit("설정을 저장할 수 있는 경로가 아직 준비되지 않았습니다.", "warning")
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
            updates = replace(
                self.ctx.settings.updates,
                auto_check_enabled=self._auto_update_checkbox.isChecked(),
            )
            next_layout = replace_auto_switch_settings(
                self.ctx.layout,
                enabled=True,
                cooldown_ms=self._cooldown_ms.value(),
                return_guard_ms=self._return_guard_ms.value(),
            )
            settings = AppSettings(hotkeys=hotkeys, backups=backups, logs=logs, updates=updates)
            self.config_reloader.save_layout_and_settings(next_layout, settings)
        except Exception as exc:
            self.messageRequested.emit(f"설정 저장에 실패했습니다: {exc}", "warning")
            return

        self.messageRequested.emit(
            "설정을 저장했습니다. 단축키 변경은 다음 실행부터 적용됩니다.",
            "success",
        )
