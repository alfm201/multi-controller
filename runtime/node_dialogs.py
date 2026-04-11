"""Qt widgets for GUI-driven node management."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


def _node_to_payload(node) -> dict:
    return {"name": node.node_id, "ip": node.ip, "port": node.port}


class NodeManagerPage(QWidget):
    messageRequested = Signal(str, str)

    def __init__(self, ctx, save_nodes, restore_nodes=None, latest_backup=None, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._save_nodes = save_nodes
        self._restore_nodes = restore_nodes
        self._latest_backup = latest_backup or (lambda: None)
        self._selected_name = None
        self._trace_guard = False
        self._build()
        self.refresh()
        self._new_node()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        intro = QLabel("노드 목록과 연결 정보를 여기서 관리합니다.")
        intro.setWordWrap(True)
        intro.setObjectName("subtle")
        root.addWidget(intro)

        content = QHBoxLayout()
        root.addLayout(content, 1)

        list_panel = QFrame()
        list_panel.setObjectName("panel")
        list_layout = QVBoxLayout(list_panel)
        list_layout.addWidget(QLabel("노드 목록"))
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_select_row)
        list_layout.addWidget(self._list, 1)
        content.addWidget(list_panel, 2)

        editor_panel = QFrame()
        editor_panel.setObjectName("panel")
        form = QGridLayout(editor_panel)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        row = 0
        form.addWidget(QLabel("이름"), row, 0)
        self._name = QLineEdit()
        self._name.textChanged.connect(self._on_form_changed)
        form.addWidget(self._name, row, 1)
        row += 1
        form.addWidget(QLabel("IP"), row, 0)
        self._ip = QLineEdit()
        self._ip.textChanged.connect(self._on_form_changed)
        form.addWidget(self._ip, row, 1)
        row += 1
        form.addWidget(QLabel("포트"), row, 0)
        self._port = QLineEdit()
        self._port.setPlaceholderText("45873")
        self._port.setMinimumWidth(160)
        self._port.textChanged.connect(self._on_form_changed)
        form.addWidget(self._port, row, 1)
        row += 1
        self._impact = QLabel()
        self._impact.setWordWrap(True)
        self._impact.setObjectName("subtle")
        form.addWidget(self._impact, row, 0, 1, 2)
        row += 1
        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setObjectName("subtle")
        form.addWidget(self._status, row, 0, 1, 2)
        row += 1

        actions = QGridLayout()
        actions.setHorizontalSpacing(8)
        actions.setVerticalSpacing(8)
        self._new_button = QPushButton("새 노드")
        self._new_button.clicked.connect(self._new_node)
        self._apply_button = QPushButton("바로 적용")
        self._apply_button.setObjectName("primary")
        self._apply_button.clicked.connect(self._save_immediate)
        self._restart_button = QPushButton("저장 후 재시작")
        self._restart_button.clicked.connect(self._save_for_restart)
        self._delete_button = QPushButton("삭제")
        self._delete_button.clicked.connect(self._delete)
        self._restore_button = QPushButton("직전 저장 복구")
        self._restore_button.clicked.connect(self._restore_latest_backup)
        for widget in (
            self._new_button,
            self._apply_button,
            self._restart_button,
            self._delete_button,
            self._restore_button,
        ):
            widget.setMinimumWidth(0)
            widget.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        actions.addWidget(self._new_button, 0, 0)
        actions.addWidget(self._apply_button, 0, 1)
        actions.addWidget(self._restart_button, 0, 2)
        actions.addWidget(self._delete_button, 1, 0)
        actions.addWidget(self._restore_button, 1, 1, 1, 2)
        actions_container = QWidget()
        actions_container.setLayout(actions)
        form.addWidget(actions_container, row, 0, 1, 2)
        form.setRowStretch(row + 1, 1)
        content.addWidget(editor_panel, 3)

    def refresh(self) -> None:
        if self._has_unsaved_changes():
            return
        current = self._selected_name
        self._list.clear()
        for node in self.ctx.nodes:
            label = f"{node.node_id} ({node.ip}:{node.port})"
            if node.node_id == self.ctx.self_node.node_id:
                label += " [내 PC]"
            self._list.addItem(label)
        if current is None:
            return
        for index, node in enumerate(self.ctx.nodes):
            if node.node_id == current:
                self._list.setCurrentRow(index)
                self._load_node(node.node_id)
                break

    def _set_status(self, text: str) -> None:
        self._status.setText(text)

    def _on_select_row(self, row: int) -> None:
        if row < 0 or row >= len(self.ctx.nodes):
            return
        self._load_node(self.ctx.nodes[row].node_id)

    def _load_node(self, node_id: str) -> None:
        node = self.ctx.get_node(node_id)
        if node is None:
            return
        self._trace_guard = True
        try:
            self._selected_name = node.node_id
            self._name.setText(node.node_id)
            self._ip.setText(node.ip)
            self._port.setText(str(node.port))
        finally:
            self._trace_guard = False
        self._set_status("")
        self._update_impact()

    def _new_node(self) -> None:
        self._trace_guard = True
        try:
            self._selected_name = None
            self._list.clearSelection()
            self._name.setText("")
            self._ip.setText("")
            self._port.setText("")
        finally:
            self._trace_guard = False
        self._set_status("새 노드 이름, IP, 포트를 입력해 주세요.")
        self._update_impact()

    def _on_form_changed(self, *_args) -> None:
        if self._trace_guard:
            return
        self._update_impact()

    def _collect_form(self, *, require_complete: bool) -> dict | None:
        name = self._name.text().strip()
        ip = self._ip.text().strip()
        port_text = self._port.text().strip()
        if not any((name, ip, port_text)) and self._selected_name is None and not require_complete:
            return None
        if not name:
            raise ValueError("이름을 입력해 주세요.")
        if not ip:
            raise ValueError("IP를 입력해 주세요.")
        if not port_text:
            raise ValueError("포트를 입력해 주세요.")
        try:
            port = int(port_text)
        except ValueError as exc:
            raise ValueError("포트는 정수여야 합니다.") from exc
        if port <= 0:
            raise ValueError("포트는 1 이상이어야 합니다.")
        return {"name": name, "ip": ip, "port": port}

    def _set_button_state(self, widget, enabled: bool) -> None:
        widget.setEnabled(enabled)

    def _build_nodes_payload(self, payload: dict) -> tuple[list[dict], dict[str, str]]:
        nodes = [_node_to_payload(node) for node in self.ctx.nodes]
        rename_map = {}
        if self._selected_name is None:
            if any(node["name"] == payload["name"] for node in nodes):
                raise ValueError("같은 이름의 노드가 이미 있습니다.")
            nodes.append(payload)
            return nodes, rename_map
        for node in nodes:
            if node["name"] == payload["name"] and node["name"] != self._selected_name:
                raise ValueError("같은 이름의 노드가 이미 있습니다.")
        updated = False
        for node in nodes:
            if node["name"] != self._selected_name:
                continue
            if payload["name"] != self._selected_name:
                rename_map[self._selected_name] = payload["name"]
            node.update(payload)
            updated = True
            break
        if not updated:
            raise ValueError(f"{self._selected_name} 노드를 찾을 수 없습니다.")
        return nodes, rename_map

    def _describe_save_impact(self, payload: dict) -> tuple[bool, str]:
        if self._selected_name is None:
            return False, "즉시 반영: 새 노드를 추가하고 레이아웃에는 빈 타일을 하나 붙입니다."
        current = self.ctx.get_node(self._selected_name)
        if current is None:
            return False, "즉시 반영: 현재 노드를 다시 불러옵니다."
        changed = []
        if payload["name"] != current.node_id:
            changed.append("이름")
        if payload["ip"] != current.ip:
            changed.append("IP")
        if payload["port"] != current.port:
            changed.append("포트")
        if not changed:
            return False, "변경된 내용이 없습니다."
        if current.node_id == self.ctx.self_node.node_id:
            return True, f"재시작 필요: 내 PC의 {', '.join(changed)} 변경은 저장 후 재시작으로 반영됩니다."
        if payload["name"] != current.node_id:
            return False, "즉시 반영: 노드 이름을 바꾸고 관련 레이아웃/모니터 설정도 함께 옮깁니다."
        return False, "즉시 반영: 연결 대상 목록과 레이아웃을 새 값으로 다시 계산합니다."

    def _update_impact(self) -> None:
        try:
            payload = self._collect_form(require_complete=False)
        except ValueError as exc:
            self._impact.setText(f"입력 필요: {exc}")
            self._set_button_state(self._apply_button, False)
            self._set_button_state(self._restart_button, False)
            self._set_button_state(
                self._delete_button,
                self._selected_name is not None and self._selected_name != self.ctx.self_node.node_id,
            )
            return
        if payload is None:
            self._impact.setText("왼쪽에서 노드를 선택하거나 새 노드를 입력해 주세요.")
            self._set_button_state(self._apply_button, False)
            self._set_button_state(self._restart_button, False)
            self._set_button_state(
                self._delete_button,
                self._selected_name is not None and self._selected_name != self.ctx.self_node.node_id,
            )
            return
        requires_restart, impact_text = self._describe_save_impact(payload)
        self._impact.setText(impact_text)
        self._set_button_state(self._apply_button, not requires_restart)
        self._set_button_state(self._restart_button, requires_restart)
        self._set_button_state(
            self._delete_button,
            self._selected_name is not None and self._selected_name != self.ctx.self_node.node_id,
        )

    def _save_immediate(self) -> None:
        try:
            payload = self._collect_form(require_complete=True)
            if payload is None:
                raise ValueError("저장할 노드 정보가 없습니다.")
            requires_restart, _ = self._describe_save_impact(payload)
            if requires_restart:
                self._set_status("이 변경은 바로 적용할 수 없습니다. '저장 후 재시작'을 사용해 주세요.")
                return
            nodes, rename_map = self._build_nodes_payload(payload)
            self._save_nodes(nodes, rename_map=rename_map, apply_runtime=True)
        except Exception as exc:
            self._set_status(f"저장 실패: {exc}")
            return
        self._selected_name = payload["name"]
        self._set_status("노드 변경을 바로 반영했습니다.")
        self.messageRequested.emit("노드 설정을 바로 반영했습니다.", "accent")
        self.refresh()

    def _save_for_restart(self) -> None:
        try:
            payload = self._collect_form(require_complete=True)
            if payload is None:
                raise ValueError("저장할 노드 정보가 없습니다.")
            requires_restart, impact_text = self._describe_save_impact(payload)
            if not requires_restart:
                self._set_status("이 변경은 바로 적용 가능합니다. '바로 적용'을 사용해 주세요.")
                return
            nodes, rename_map = self._build_nodes_payload(payload)
            self._save_nodes(nodes, rename_map=rename_map, apply_runtime=False)
        except Exception as exc:
            self._set_status(f"저장 실패: {exc}")
            return
        self.messageRequested.emit("현재 실행 중인 내 PC 변경은 저장 후 재시작으로 반영됩니다.", "warning")
        QMessageBox.information(
            self,
            "재시작 필요",
            impact_text + "\n\n현재 실행은 그대로 유지되고, 프로그램을 다시 시작하면 새 설정이 적용됩니다.",
        )

    def _delete(self) -> None:
        if self._selected_name is None:
            self._set_status("삭제할 노드를 먼저 선택해 주세요.")
            return
        if self._selected_name == self.ctx.self_node.node_id:
            self._set_status("내 PC는 삭제할 수 없습니다.")
            return
        confirmed = QMessageBox.question(
            self,
            "노드 삭제",
            f"{self._selected_name} 노드를 삭제할까요?\n레이아웃과 모니터 보정 정보도 함께 정리됩니다.",
        )
        if confirmed != QMessageBox.Yes:
            return
        nodes = [_node_to_payload(node) for node in self.ctx.nodes if node.node_id != self._selected_name]
        try:
            self._save_nodes(nodes, rename_map={}, apply_runtime=True)
        except Exception as exc:
            self._set_status(f"삭제 실패: {exc}")
            return
        removed = self._selected_name
        self._selected_name = None
        self.messageRequested.emit(f"{removed} 노드를 삭제했습니다.", "warning")
        self._new_node()
        self.refresh()

    def _restore_latest_backup(self) -> None:
        if not callable(self._restore_nodes):
            self._set_status("복구 기능을 사용할 수 없습니다.")
            return
        latest = self._latest_backup()
        if latest is None:
            self._set_status("복구할 직전 저장이 없습니다.")
            return
        confirmed = QMessageBox.question(
            self,
            "직전 저장 복구",
            f"{latest.name} 백업으로 되돌릴까요?\n현재 노드 목록과 레이아웃 보정 정보가 함께 복구됩니다.",
        )
        if confirmed != QMessageBox.Yes:
            return
        try:
            restored_path, applied_runtime, detail = self._restore_nodes()
        except Exception as exc:
            self._set_status(f"복구 실패: {exc}")
            return
        if applied_runtime:
            self._set_status("직전 저장을 복구하고 현재 실행에도 바로 반영했습니다.")
            self.messageRequested.emit(f"직전 저장을 복구했습니다. ({restored_path.name})", "success")
            self.refresh()
            return
        self._set_status("직전 저장을 복구했습니다. 재시작 후 반영됩니다.")
        self.messageRequested.emit(
            f"직전 저장을 복구했습니다. 재시작 후 반영됩니다. ({restored_path.name})",
            "warning",
        )
        QMessageBox.information(self, "재시작 필요", detail)

    def _has_unsaved_changes(self) -> bool:
        try:
            payload = self._collect_form(require_complete=False)
        except ValueError:
            return any((self._name.text().strip(), self._ip.text().strip(), self._port.text().strip()))
        if payload is None:
            return False
        if self._selected_name is None:
            return True
        current = self.ctx.get_node(self._selected_name)
        if current is None:
            return False
        return not (
            payload["name"] == current.node_id
            and payload["ip"] == current.ip
            and payload["port"] == current.port
        )
