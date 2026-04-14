"""Qt widgets for GUI-driven node management."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
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
)

from runtime.config_loader import DEFAULT_LISTEN_PORT

CHECK_STATE_ROLE = Qt.UserRole + 1
NODE_ID_ROLE = Qt.UserRole + 2


def _node_to_payload(node) -> dict:
    return {"name": node.node_id, "ip": node.ip, "port": DEFAULT_LISTEN_PORT}


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

        port_hint = QLabel(f"포트는 항상 {DEFAULT_LISTEN_PORT}을 사용합니다.")
        port_hint.setObjectName("subtle")
        port_hint.setWordWrap(True)
        form.addWidget(port_hint, 2, 0, 1, 2)
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
        return {"name": name, "ip": ip, "port": DEFAULT_LISTEN_PORT}


class NodeManagerPage(QWidget):
    messageRequested = Signal(str, str)

    def __init__(self, ctx, save_nodes, restore_nodes=None, latest_backup=None, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._save_nodes = save_nodes
        self._restore_nodes = restore_nodes
        self._latest_backup = latest_backup or (lambda: None)
        self._build()
        self.refresh()

    def refresh(self) -> None:
        checked_ids = set(self._checked_node_ids())
        self._table.blockSignals(True)
        self._table.setRowCount(len(self.ctx.nodes))
        for row, node in enumerate(self.ctx.nodes):
            check_item = self._table.item(row, 0)
            if check_item is None:
                check_item = QTableWidgetItem()
                check_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                self._table.setItem(row, 0, check_item)
            check_item.setData(NODE_ID_ROLE, node.node_id)
            check_item.setCheckState(Qt.Checked if node.node_id in checked_ids else Qt.Unchecked)
            check_item.setText("")

            self._set_text_item(row, 1, node.node_id, node.node_id)
            self._set_text_item(row, 2, node.ip, node.node_id)
            self._set_text_item(row, 3, str(node.port), node.node_id)
            self._set_text_item(row, 4, "내 PC" if node.node_id == self.ctx.self_node.node_id else "원격 노드", node.node_id)
        self._table.blockSignals(False)
        self._table.resizeRowsToContents()
        self._update_action_state()

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

        self._table = QTableWidget(0, 5)
        self._table.setMinimumWidth(620)
        self._table.setHorizontalHeaderLabels(("선택", "이름", "IP", "포트", "비고"))
        self._table.verticalHeader().hide()
        self._table.setSelectionMode(QAbstractItemView.NoSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.itemChanged.connect(self._on_item_changed)
        header_view = self._table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.Stretch)
        header_view.setSectionResizeMode(2, QHeaderView.Stretch)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        panel_layout.addWidget(self._table, 1)

        actions = QHBoxLayout()
        self._new_button = QPushButton("새 노드")
        self._new_button.clicked.connect(self._create_node)
        self._edit_button = QPushButton("수정")
        self._edit_button.clicked.connect(self._edit_selected)
        self._delete_button = QPushButton("삭제")
        self._delete_button.clicked.connect(self._delete_selected)
        self._restore_button = QPushButton("직전 상태 복구")
        self._restore_button.clicked.connect(self._restore_latest_backup)
        actions.addWidget(self._new_button)
        actions.addWidget(self._edit_button)
        actions.addWidget(self._delete_button)
        actions.addStretch(1)
        actions.addWidget(self._restore_button)
        panel_layout.addLayout(actions)

        self._status = QLabel("")
        self._status.setObjectName("subtle")
        self._status.setWordWrap(True)
        panel_layout.addWidget(self._status)

        root.addWidget(panel, 1)

    def _set_text_item(self, row: int, column: int, text: str, node_id: str) -> None:
        item = self._table.item(row, column)
        if item is None:
            item = QTableWidgetItem()
            self._table.setItem(row, column, item)
        item.setText(text)
        item.setData(NODE_ID_ROLE, node_id)

    def _set_status(self, text: str) -> None:
        self._status.setText(text)

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

    def _update_action_state(self) -> None:
        checked = self._checked_node_ids()
        count = len(checked)
        self._selection_summary.setText("선택 없음" if count == 0 else f"{count}개 선택")
        self._edit_button.setEnabled(count > 0)
        self._delete_button.setEnabled(count > 0)
        self._restore_button.setEnabled(callable(self._restore_nodes))

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
            QMessageBox.information(
                self,
                "수정 안내",
                "노드 수정은 체크박스로 하나의 노드만 선택했을 때 사용할 수 있습니다.",
            )
            return
        node = self.ctx.get_node(checked[0])
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
