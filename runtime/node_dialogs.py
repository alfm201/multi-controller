"""Qt widgets for GUI-driven node management."""

from __future__ import annotations

import threading

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QHeaderView,
    QSizePolicy,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionButton,
    QStyleOptionViewItem,
)

from runtime.config_loader import DEFAULT_LISTEN_PORT
from runtime.group_join import request_group_join_state
from runtime.scroll_utils import attach_horizontal_scroll_interaction

CHECK_STATE_ROLE = Qt.UserRole + 1
NODE_ID_ROLE = Qt.UserRole + 2


def _node_to_payload(node) -> dict:
    return {
        "name": node.node_id,
        "ip": node.ip,
        "port": DEFAULT_LISTEN_PORT,
        "note": getattr(node, "note", "") or "",
    }


class NodeEditorDialog(QDialog):
    def __init__(self, *, title: str, payload: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(360, 0)
        self._payload = None
        self._build(payload or {})

    def payload(self) -> dict:
        if self._payload is None:
            raise RuntimeError("payload is unavailable before the dialog is accepted")
        return dict(self._payload)

    def accept(self) -> None:  # noqa: D401
        try:
            self._payload = self._collect_payload()
        except ValueError as exc:
            QMessageBox.warning(self, "입력 확인", str(exc))
            return
        super().accept()

    def _build(self, payload: dict) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        intro = QLabel("노드 이름과 IP를 입력해 주세요.")
        intro.setObjectName("subtle")
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)

        form.addWidget(QLabel("이름"), 0, 0)
        self._name = QLineEdit(payload.get("name", ""))
        form.addWidget(self._name, 0, 1)

        form.addWidget(QLabel("IP"), 1, 0)
        self._ip = QLineEdit(payload.get("ip", ""))
        form.addWidget(self._ip, 1, 1)

        form.addWidget(QLabel("비고"), 2, 0)
        self._note = QLineEdit(payload.get("note", ""))
        self._note.setPlaceholderText("선택 사항")
        form.addWidget(self._note, 2, 1)

        port_hint = QLabel(f"포트는 항상 {DEFAULT_LISTEN_PORT}을 사용합니다.")
        port_hint.setObjectName("subtle")
        port_hint.setWordWrap(True)
        form.addWidget(port_hint, 3, 0, 1, 2)
        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("저장")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _collect_payload(self) -> dict:
        name = self._name.text().strip()
        ip = self._ip.text().strip()
        if not name:
            raise ValueError("이름을 입력해 주세요.")
        if not ip:
            raise ValueError("IP를 입력해 주세요.")
        return {
            "name": name,
            "ip": ip,
            "port": DEFAULT_LISTEN_PORT,
            "note": self._note.text().strip(),
        }


class GroupJoinDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("그룹 참여")
        self.setModal(True)
        self.resize(340, 0)
        self._build()

    def target_ip(self) -> str:
        return self._ip.text().strip()

    def accept(self) -> None:  # noqa: D401
        if not self.target_ip():
            QMessageBox.warning(self, "입력 확인", "대상 IP를 입력해 주세요.")
            return
        super().accept()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        intro = QLabel("참여할 노드 그룹에 속한 PC의 IP를 입력해 주세요.")
        intro.setWordWrap(True)
        intro.setObjectName("subtle")
        root.addWidget(intro)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        form.addWidget(QLabel("대상 IP"), 0, 0)
        self._ip = QLineEdit("")
        form.addWidget(self._ip, 0, 1)
        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_button = buttons.button(QDialogButtonBox.Ok)
        cancel_button = buttons.button(QDialogButtonBox.Cancel)
        if ok_button is not None:
            ok_button.setText("참여")
        if cancel_button is not None:
            cancel_button.setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)


class NodeTableWidget(QTableWidget):
    def __init__(self, rows: int, columns: int, parent=None):
        super().__init__(rows, columns, parent)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.ElideRight)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        attach_horizontal_scroll_interaction(self)


class CenteredCheckboxDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index) -> None:
        if index.column() != 0:
            super().paint(painter, option, index)
            return
        style = option.widget.style() if option.widget is not None else QApplication.style()
        item_option = QStyleOptionViewItem(option)
        self.initStyleOption(item_option, index)
        item_option.text = ""
        item_option.icon = None
        style.drawPrimitive(QStyle.PE_PanelItemViewItem, item_option, painter, option.widget)
        check_state = index.data(Qt.CheckStateRole)
        if check_state is None:
            check_state = Qt.Unchecked
        checkbox_option = QStyleOptionButton()
        checkbox_option.state = QStyle.State_Enabled | QStyle.State_Active
        if option.state & QStyle.State_MouseOver:
            checkbox_option.state |= QStyle.State_MouseOver
        if check_state == Qt.Checked:
            checkbox_option.state |= QStyle.State_On
        else:
            checkbox_option.state |= QStyle.State_Off
        indicator_width = style.pixelMetric(QStyle.PM_IndicatorWidth, checkbox_option, option.widget)
        indicator_height = style.pixelMetric(QStyle.PM_IndicatorHeight, checkbox_option, option.widget)
        checkbox_option.rect = QRect(0, 0, indicator_width, indicator_height)
        checkbox_option.rect.moveCenter(option.rect.center())
        style.drawControl(QStyle.CE_CheckBox, checkbox_option, painter, option.widget)


class NodeManagerPage(QWidget):
    messageRequested = Signal(str, str)
    groupJoinSucceeded = Signal(object, str)
    groupJoinFailed = Signal(str, str)

    def __init__(
        self,
        ctx,
        save_nodes,
        restore_nodes=None,
        latest_backup=None,
        coord_client=None,
        parent=None,
    ):
        super().__init__(parent)
        self.ctx = ctx
        self._save_nodes = save_nodes
        self._restore_nodes = restore_nodes
        self._latest_backup = latest_backup or (lambda: None)
        self._coord_client = coord_client
        self._last_status_text = ""
        self._group_join_in_progress = False
        self.groupJoinSucceeded.connect(self._handle_group_join_payload)
        self.groupJoinFailed.connect(self._handle_group_join_failure)
        self._build()
        self.refresh()

    def refresh(self) -> None:
        checked_ids = set(self._checked_node_ids())
        display_nodes = self._display_nodes()
        self._table.blockSignals(True)
        self._table.setRowCount(len(display_nodes))
        for row, node in enumerate(display_nodes):
            check_item = self._table.item(row, 0)
            if check_item is None:
                check_item = QTableWidgetItem()
                check_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                self._table.setItem(row, 0, check_item)
            check_item.setData(NODE_ID_ROLE, node.node_id)
            check_item.setCheckState(Qt.Checked if node.node_id in checked_ids else Qt.Unchecked)
            check_item.setText("")
            check_item.setTextAlignment(Qt.AlignCenter)

            self._set_text_item(row, 1, node.node_id, node.node_id)
            self._set_text_item(row, 2, node.ip, node.node_id)
            self._set_text_item(row, 3, getattr(node, "note", "") or "", node.node_id)
        self._table.blockSignals(False)
        self._table.resizeColumnsToContents()
        self._table.resizeRowsToContents()
        self._update_action_state()

    def _display_nodes(self) -> list:
        self_id = self.ctx.self_node.node_id
        return sorted(
            self.ctx.nodes,
            key=lambda node: (0 if node.node_id == self_id else 1, node.node_id.lower()),
        )

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        intro = QLabel("체크박스로 노드를 선택해 수정하거나 삭제할 수 있습니다. 새 노드 추가와 편집은 별도 창에서 진행됩니다.")
        intro.setWordWrap(True)
        intro.setObjectName("subtle")
        root.addWidget(intro)

        panel = QFrame()
        panel.setObjectName("panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(12, 12, 12, 12)
        panel_layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("노드 목록")
        title.setObjectName("heading")
        title.setStyleSheet("font-size: 16px;")
        self._selection_summary = QLabel("")
        self._selection_summary.setObjectName("subtle")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._selection_summary)
        panel_layout.addLayout(header)

        self._table = NodeTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(("선택", "이름", "IP", "비고"))
        self._table.verticalHeader().hide()
        self._table.setSelectionMode(QAbstractItemView.NoSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setFocusPolicy(Qt.NoFocus)
        self._table.setItemDelegateForColumn(0, CenteredCheckboxDelegate(self._table))
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        header_view = self._table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header_view.setStretchLastSection(False)
        panel_layout.addWidget(self._table, 1)

        actions = QHBoxLayout()
        self._new_button = QPushButton("새 노드")
        self._new_button.clicked.connect(self._create_node)
        self._join_button = QPushButton("그룹 참여")
        self._join_button.clicked.connect(self._join_group)
        self._edit_button = QPushButton("수정")
        self._edit_button.clicked.connect(self._edit_selected)
        self._delete_button = QPushButton("삭제")
        self._delete_button.clicked.connect(self._delete_selected)
        self._restore_button = QPushButton("직전 상태 복구")
        self._restore_button.clicked.connect(self._restore_latest_backup)
        actions.addWidget(self._new_button)
        actions.addWidget(self._join_button)
        actions.addWidget(self._edit_button)
        actions.addWidget(self._delete_button)
        actions.addStretch(1)
        actions.addWidget(self._restore_button)
        panel_layout.addLayout(actions)

        root.addWidget(panel, 1)

    def _set_text_item(self, row: int, column: int, text: str, node_id: str) -> None:
        item = self._table.item(row, column)
        if item is None:
            item = QTableWidgetItem()
            self._table.setItem(row, column, item)
        item.setText(text)
        item.setData(NODE_ID_ROLE, node_id)
        item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)

    def _set_status(self, text: str) -> None:
        self._last_status_text = text

    def _checked_node_ids(self) -> list[str]:
        checked = []
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item is None or item.checkState() != Qt.Checked:
                continue
            node_id = item.data(NODE_ID_ROLE)
            if node_id:
                checked.append(str(node_id))
        return checked

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        self._update_action_state()

    def _on_cell_clicked(self, row: int, column: int) -> None:
        if column not in {0, 1, 2}:
            return
        self._toggle_row_checked(row)

    def _on_cell_double_clicked(self, row: int, column: int) -> None:
        if column not in {1, 2, 3}:
            return
        node_id = self._node_id_for_row(row)
        if node_id:
            self._edit_node_by_id(node_id)

    def _update_action_state(self) -> None:
        checked = self._checked_node_ids()
        count = len(checked)
        self._selection_summary.setText("선택 없음" if count == 0 else f"{count}개 선택")
        self._edit_button.setEnabled(count > 0)
        self._delete_button.setEnabled(count > 0)
        self._restore_button.setEnabled(callable(self._restore_nodes))
        self._join_button.setEnabled(not self._group_join_in_progress)

    def _node_id_for_row(self, row: int) -> str | None:
        item = self._table.item(row, 0)
        if item is None:
            return None
        node_id = item.data(NODE_ID_ROLE)
        return None if not node_id else str(node_id)

    def _toggle_row_checked(self, row: int) -> None:
        item = self._table.item(row, 0)
        if item is None:
            return
        next_state = Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked
        self._table.blockSignals(True)
        item.setCheckState(next_state)
        self._table.blockSignals(False)
        self._update_action_state()

    def _open_node_editor(self, *, title: str, payload: dict | None = None) -> dict | None:
        dialog = NodeEditorDialog(title=title, payload=payload, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return None
        return dialog.payload()

    def _create_node(self) -> None:
        payload = self._open_node_editor(title="새 노드 추가")
        if payload is None:
            return
        self._save_payload(selected_name=None, payload=payload)

    def _edit_selected(self) -> None:
        checked = self._checked_node_ids()
        if len(checked) != 1:
            self._show_quiet_notice(
                "수정 안내",
                "노드 수정은 체크박스로 하나의 노드만 선택했을 때 사용할 수 있습니다.",
            )
            return
        self._edit_node_by_id(checked[0])

    def _edit_node_by_id(self, node_id: str) -> None:
        node = self.ctx.get_node(node_id)
        if node is None:
            self._set_status("선택한 노드를 찾을 수 없습니다.")
            return
        payload = self._open_node_editor(
            title=f"{node.node_id} 수정",
            payload=_node_to_payload(node),
        )
        if payload is None:
            return
        self._save_payload(selected_name=node.node_id, payload=payload)

    def _save_payload(self, *, selected_name: str | None, payload: dict) -> None:
        try:
            requires_restart, impact_text = self._describe_save_impact(selected_name, payload)
            if impact_text == "변경된 내용이 없습니다.":
                self._set_status(impact_text)
                return
            nodes, rename_map = self._build_nodes_payload(selected_name, payload)
            self._save_nodes(nodes, rename_map=rename_map, apply_runtime=not requires_restart)
        except Exception as exc:
            self._set_status(f"노드 저장에 실패했습니다: {exc}")
            return

        self._table.blockSignals(True)
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item is not None:
                item.setCheckState(Qt.Unchecked)
        self._table.blockSignals(False)

        if requires_restart:
            self._set_status("현재 PC 변경은 앱을 다시 시작한 뒤 반영됩니다.")
            self.messageRequested.emit("현재 PC 정보 변경은 앱 다시 시작 후 반영됩니다.", "warning")
            QMessageBox.information(
                self,
                "재시작 필요",
                impact_text + "\n\n설정 파일에는 저장되었고, 프로그램을 다시 시작하면 변경 내용이 반영됩니다.",
            )
            self._update_action_state()
            return

        self._sync_nodes_via_coordinator(
            nodes,
            rename_map=rename_map,
            allow_runtime_sync=not requires_restart,
        )

        self._set_status("노드 목록을 저장했습니다.")
        self.messageRequested.emit("노드 목록을 저장했습니다.", "success")
        self.refresh()

    def _build_nodes_payload(
        self,
        selected_name: str | None,
        payload: dict,
    ) -> tuple[list[dict], dict[str, str]]:
        nodes = [_node_to_payload(node) for node in self.ctx.nodes]
        rename_map: dict[str, str] = {}
        if selected_name is None:
            if any(node["name"] == payload["name"] for node in nodes):
                raise ValueError("같은 이름의 노드가 이미 있습니다.")
            nodes.append(payload)
            return nodes, rename_map

        for node in nodes:
            if node["name"] == payload["name"] and node["name"] != selected_name:
                raise ValueError("같은 이름의 노드가 이미 있습니다.")

        updated = False
        for node in nodes:
            if node["name"] != selected_name:
                continue
            if payload["name"] != selected_name:
                rename_map[selected_name] = payload["name"]
            node.update(payload)
            updated = True
            break
        if not updated:
            raise ValueError(f"{selected_name} 노드를 찾을 수 없습니다.")
        return nodes, rename_map

    def _describe_save_impact(self, selected_name: str | None, payload: dict) -> tuple[bool, str]:
        if selected_name is None:
            return False, "새 노드를 추가합니다."
        current = self.ctx.get_node(selected_name)
        if current is None:
            raise ValueError(f"{selected_name} 노드를 찾을 수 없습니다.")
        changed = []
        if payload["name"] != current.node_id:
            changed.append("이름")
        if payload["ip"] != current.ip:
            changed.append("IP")
        if payload.get("note", "") != getattr(current, "note", ""):
            changed.append("비고")
        if not changed:
            return False, "변경된 내용이 없습니다."
        if current.node_id == self.ctx.self_node.node_id:
            return True, f"내 PC의 {', '.join(changed)} 변경은 앱 다시 시작 후 반영됩니다."
        return False, f"{current.node_id} 노드의 {', '.join(changed)} 변경을 바로 반영합니다."

    def _delete_selected(self) -> None:
        checked = self._checked_node_ids()
        if not checked:
            QMessageBox.information(self, "삭제 안내", "삭제할 노드를 먼저 체크해 주세요.")
            return
        if self.ctx.self_node.node_id in checked:
            QMessageBox.warning(self, "삭제 불가", "내 PC는 삭제할 수 없습니다.")
            return
        label = ", ".join(checked[:4])
        if len(checked) > 4:
            label += f" 외 {len(checked) - 4}개"
        confirmed = QMessageBox.question(
            self,
            "노드 삭제",
            f"선택한 노드를 삭제할까요?\n\n{label}",
        )
        if confirmed != QMessageBox.Yes:
            return

        nodes = [_node_to_payload(node) for node in self.ctx.nodes if node.node_id not in checked]
        try:
            self._save_nodes(nodes, rename_map={}, apply_runtime=True)
        except Exception as exc:
            self._set_status(f"노드 삭제에 실패했습니다: {exc}")
            return

        self._set_status("선택한 노드를 삭제했습니다.")
        self.messageRequested.emit("선택한 노드를 삭제했습니다.", "warning")
        self.refresh()

    def _join_group(self) -> None:
        dialog = GroupJoinDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        target_ip = dialog.target_ip()
        if not target_ip:
            return
        self._group_join_in_progress = True
        self._update_action_state()
        detail = f"{target_ip} PC에 연결해 노드 그룹 참여를 요청하는 중입니다."
        self._set_status(detail)
        self.messageRequested.emit(detail, "accent")
        self._start_group_join_worker(target_ip)
        return

    def _start_group_join_worker(self, target_ip: str) -> None:
        threading.Thread(
            target=self._group_join_worker,
            args=(target_ip,),
            daemon=True,
            name=f"group-join-{target_ip}",
        ).start()

    def _group_join_worker(self, target_ip: str) -> None:
        try:
            payload = request_group_join_state(target_ip, self.ctx.self_node.node_id)
            self.groupJoinSucceeded.emit(payload, target_ip)
        except Exception as exc:
            self.groupJoinFailed.emit(target_ip, str(exc))

    def _handle_group_join_payload(self, payload: object, target_ip: str) -> None:
        self._group_join_in_progress = False
        self._update_action_state()
        payload = payload if isinstance(payload, dict) else {}
        if payload.get("accepted") is False:
            detail = str(payload.get("detail") or "노드 그룹 참여가 거부되었습니다.")
            self._handle_group_join_failure(target_ip, detail)
            return
        nodes = payload.get("nodes") or []
        detail = str(payload.get("detail") or "").strip()
        try:
            self._save_nodes(nodes, rename_map={}, apply_runtime=True)
        except Exception as exc:
            self._handle_group_join_failure(target_ip, str(exc))
            return

        self._sync_nodes_via_coordinator(nodes, rename_map={}, allow_runtime_sync=True)
        if detail:
            self._set_status(detail)
            self.messageRequested.emit(detail, "neutral")
        success_message = "노드 그룹에 참여했습니다."
        self._set_status(success_message)
        self.messageRequested.emit(success_message, "success")
        self.refresh()

    def _handle_group_join_failure(self, target_ip: str, detail: str) -> None:
        self._group_join_in_progress = False
        self._update_action_state()
        message = f"노드 그룹 참여에 실패했습니다: {detail}"
        self._set_status(message)
        self.messageRequested.emit(message, "warning")

    def _restore_latest_backup(self) -> None:
        if not callable(self._restore_nodes):
            self._set_status("복구 기능을 사용할 수 없습니다.")
            return
        latest = self._latest_backup()
        if latest is None:
            self._set_status("복구할 직전 상태가 없습니다.")
            return
        confirmed = QMessageBox.question(
            self,
            "직전 상태 복구",
            f"{latest.name} 백업으로 되돌릴까요?\n현재 노드 목록과 레이아웃 보정 정보가 함께 복구됩니다.",
        )
        if confirmed != QMessageBox.Yes:
            return
        try:
            restored_path, applied_runtime, detail = self._restore_nodes()
        except Exception as exc:
            self._set_status(f"복구에 실패했습니다: {exc}")
            return
        if applied_runtime:
            self._set_status("직전 상태를 복구했고 현재 실행에도 바로 반영했습니다.")
            self.messageRequested.emit(f"직전 상태를 복구했습니다. ({restored_path.name})", "success")
            self.refresh()
            return
        self._set_status("직전 상태를 복구했습니다. 다시 시작 후 반영됩니다.")
        self.messageRequested.emit(
            f"직전 상태를 복구했습니다. 다시 시작 후 반영됩니다. ({restored_path.name})",
            "warning",
        )
        QMessageBox.information(self, "재시작 필요", detail)

    def _show_quiet_notice(self, title: str, text: str) -> int:
        dialog = QMessageBox(self)
        dialog.setWindowTitle(title)
        dialog.setText(text)
        dialog.setIcon(QMessageBox.NoIcon)
        dialog.setStandardButtons(QMessageBox.Ok)
        ok_button = dialog.button(QMessageBox.Ok)
        if ok_button is not None:
            ok_button.setText("확인")
        return dialog.exec()

    def _sync_nodes_via_coordinator(
        self,
        nodes: list[dict],
        *,
        rename_map: dict[str, str],
        allow_runtime_sync: bool,
    ) -> None:
        if not allow_runtime_sync:
            return
        if self._coord_client is None or not hasattr(self._coord_client, "request_node_list_update"):
            return
        sent = self._coord_client.request_node_list_update(nodes, rename_map=rename_map)
        if not sent:
            self.messageRequested.emit("노드 목록 변경을 다른 노드에 전달하지 못했습니다.", "warning")
