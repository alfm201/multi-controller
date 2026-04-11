"""Dialogs for GUI-driven node management."""

from __future__ import annotations


class NodeManagerDialog:
    """Small CRUD dialog for node records."""

    def __init__(self, parent, ctx, save_nodes, restore_nodes=None, latest_backup=None, on_message=None):
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self._ttk = ttk
        self.ctx = ctx
        self._save_nodes = save_nodes
        self._restore_nodes = restore_nodes
        self._latest_backup = latest_backup or (lambda: None)
        self._on_message = on_message or (lambda _message, _tone="neutral": None)
        self._selected_name = None
        self._trace_guard = False

        self.window = tk.Toplevel(parent)
        self.window.title("노드 관리")
        self.window.geometry("760x520")
        self.window.minsize(700, 460)

        frame = ttk.Frame(self.window, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=2)
        frame.columnconfigure(1, weight=3)
        frame.rowconfigure(1, weight=1)

        ttk.Label(
            frame,
            text=(
                "노드 추가/수정/삭제를 GUI에서 바로 처리합니다. "
                "다른 PC 변경은 즉시 반영할 수 있고, 현재 실행 중인 내 PC 이름/IP/포트/역할 변경은 "
                "설정 저장 후 재시작이 필요합니다."
            ),
            wraplength=700,
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        list_box = ttk.LabelFrame(frame, text="노드 목록", padding=10)
        list_box.grid(row=1, column=0, sticky="nsew", padx=(0, 10), pady=(10, 0))
        list_box.columnconfigure(0, weight=1)
        list_box.rowconfigure(0, weight=1)
        self._listbox = tk.Listbox(list_box, exportselection=False)
        self._listbox.grid(row=0, column=0, sticky="nsew")
        self._listbox.bind("<<ListboxSelect>>", self._on_select)

        editor = ttk.LabelFrame(frame, text="편집", padding=10)
        editor.grid(row=1, column=1, sticky="nsew", pady=(10, 0))
        editor.columnconfigure(1, weight=1)

        self._vars = {
            "name": tk.StringVar(),
            "ip": tk.StringVar(),
            "port": tk.StringVar(),
            "role_controller": tk.BooleanVar(value=True),
            "role_target": tk.BooleanVar(value=True),
            "impact": tk.StringVar(value="왼쪽에서 노드를 선택하거나 새 노드를 추가해 주세요."),
            "status": tk.StringVar(value=""),
        }

        ttk.Label(editor, text="이름").grid(row=0, column=0, sticky="w", pady=4, padx=(0, 10))
        self._name_entry = ttk.Entry(editor, textvariable=self._vars["name"])
        self._name_entry.grid(row=0, column=1, sticky="ew", pady=4)

        ttk.Label(editor, text="IP").grid(row=1, column=0, sticky="w", pady=4, padx=(0, 10))
        self._ip_entry = ttk.Entry(editor, textvariable=self._vars["ip"])
        self._ip_entry.grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(editor, text="포트").grid(row=2, column=0, sticky="w", pady=4, padx=(0, 10))
        self._port_entry = ttk.Entry(editor, textvariable=self._vars["port"])
        self._port_entry.grid(row=2, column=1, sticky="ew", pady=4)

        ttk.Label(editor, text="역할").grid(row=3, column=0, sticky="nw", pady=4, padx=(0, 10))
        roles = ttk.Frame(editor)
        roles.grid(row=3, column=1, sticky="w", pady=4)
        self._controller_check = ttk.Checkbutton(
            roles,
            text="controller",
            variable=self._vars["role_controller"],
            command=self._update_impact,
        )
        self._controller_check.pack(side="left")
        self._target_check = ttk.Checkbutton(
            roles,
            text="target",
            variable=self._vars["role_target"],
            command=self._update_impact,
        )
        self._target_check.pack(side="left", padx=(10, 0))

        ttk.Label(
            editor,
            textvariable=self._vars["impact"],
            wraplength=380,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(12, 0))
        ttk.Label(
            editor,
            textvariable=self._vars["status"],
            wraplength=380,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))

        actions = ttk.Frame(editor)
        actions.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        ttk.Button(actions, text="새 노드", command=self._new_node).pack(side="left")
        self._apply_button = ttk.Button(actions, text="바로 적용", command=self._save_immediate)
        self._apply_button.pack(side="left", padx=(8, 0))
        self._restart_button = ttk.Button(actions, text="저장 후 재시작", command=self._save_for_restart)
        self._restart_button.pack(side="left", padx=(8, 0))
        self._delete_button = ttk.Button(actions, text="삭제", command=self._delete)
        self._delete_button.pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="닫기", command=self.close).pack(side="right")

        self._restore_button = ttk.Button(actions, text="직전 저장 복구", command=self._restore_latest_backup)
        self._restore_button.pack(side="left", padx=(8, 0))

        for key in ("name", "ip", "port"):
            self._vars[key].trace_add("write", self._on_form_changed)

        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self._refresh_list()
        self._new_node()

    def close(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.destroy()

    def refresh(self):
        if self._has_unsaved_changes():
            return
        current = self._selected_name
        self._refresh_list()
        if current is not None:
            for index, node in enumerate(self.ctx.nodes):
                if node.node_id == current:
                    self._listbox.selection_clear(0, self._tk.END)
                    self._listbox.selection_set(index)
                    self._listbox.activate(index)
                    self._load_node(node.node_id)
                    break

    def _refresh_list(self):
        self._listbox.delete(0, self._tk.END)
        for node in self.ctx.nodes:
            roles = ",".join(node.roles)
            label = f"{node.node_id}  ({node.ip}:{node.port})  [{roles}]"
            if node.node_id == self.ctx.self_node.node_id:
                label += "  [내 PC]"
            self._listbox.insert(self._tk.END, label)

    def _on_select(self, _event):
        selection = self._listbox.curselection()
        if not selection:
            return
        node = self.ctx.nodes[selection[0]]
        self._load_node(node.node_id)

    def _load_node(self, node_id: str):
        node = self.ctx.get_node(node_id)
        if node is None:
            return
        self._trace_guard = True
        try:
            self._selected_name = node.node_id
            self._vars["name"].set(node.node_id)
            self._vars["ip"].set(node.ip)
            self._vars["port"].set(str(node.port))
            self._vars["role_controller"].set("controller" in node.roles)
            self._vars["role_target"].set("target" in node.roles)
        finally:
            self._trace_guard = False
        self._vars["status"].set("")
        self._update_impact()

    def _new_node(self):
        self._trace_guard = True
        try:
            self._selected_name = None
            self._listbox.selection_clear(0, self._tk.END)
            self._vars["name"].set("")
            self._vars["ip"].set("")
            self._vars["port"].set("")
            self._vars["role_controller"].set(True)
            self._vars["role_target"].set(True)
        finally:
            self._trace_guard = False
        self._vars["status"].set("새 노드 이름, IP, 포트를 입력한 뒤 저장해 주세요.")
        self._update_impact()

    def _on_form_changed(self, *_args):
        if self._trace_guard:
            return
        self._update_impact()

    def _update_impact(self):
        try:
            payload = self._collect_form(require_complete=False)
        except ValueError as exc:
            self._vars["impact"].set(f"입력 필요: {exc}")
            self._set_button_state(self._apply_button, False)
            self._set_button_state(self._restart_button, False)
            self._set_button_state(
                self._delete_button,
                self._selected_name is not None and self._selected_name != self.ctx.self_node.node_id,
            )
            return

        if payload is None:
            self._vars["impact"].set("왼쪽에서 노드를 선택하거나 새 노드를 추가해 주세요.")
            self._set_button_state(self._apply_button, False)
            self._set_button_state(self._restart_button, False)
            self._set_button_state(
                self._delete_button,
                self._selected_name is not None and self._selected_name != self.ctx.self_node.node_id,
            )
            return

        requires_restart, impact_text = self._describe_save_impact(payload)
        self._vars["impact"].set(impact_text)
        self._set_button_state(self._apply_button, not requires_restart)
        self._set_button_state(self._restart_button, requires_restart)
        self._set_button_state(
            self._delete_button,
            self._selected_name is not None and self._selected_name != self.ctx.self_node.node_id,
        )

    def _collect_form(self, *, require_complete: bool) -> dict | None:
        name = self._vars["name"].get().strip()
        ip = self._vars["ip"].get().strip()
        port_text = self._vars["port"].get().strip()

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

        roles = []
        if self._vars["role_controller"].get():
            roles.append("controller")
        if self._vars["role_target"].get():
            roles.append("target")
        if not roles:
            raise ValueError("최소 하나의 역할을 선택해 주세요.")

        return {
            "name": name,
            "ip": ip,
            "port": port,
            "roles": roles,
        }

    def _save_immediate(self):
        try:
            payload = self._collect_form(require_complete=True)
            if payload is None:
                raise ValueError("저장할 노드 정보가 없습니다.")
            requires_restart, _impact_text = self._describe_save_impact(payload)
            if requires_restart:
                self._vars["status"].set("이 변경은 바로 적용할 수 없습니다. '저장 후 재시작'을 사용해 주세요.")
                return
            nodes, rename_map = self._build_nodes_payload(payload)
            self._save_nodes(nodes, rename_map=rename_map, apply_runtime=True)
        except Exception as exc:
            self._vars["status"].set(f"저장 실패: {exc}")
            return

        target_name = payload["name"]
        self._selected_name = target_name
        self._vars["status"].set("노드 변경을 바로 반영했습니다.")
        self._on_message("노드 설정을 바로 반영했습니다.", "accent")
        self.refresh()

    def _save_for_restart(self):
        from tkinter import messagebox

        try:
            payload = self._collect_form(require_complete=True)
            if payload is None:
                raise ValueError("저장할 노드 정보가 없습니다.")
            requires_restart, impact_text = self._describe_save_impact(payload)
            if not requires_restart:
                self._vars["status"].set("이 변경은 바로 적용할 수 있습니다. '바로 적용'을 사용해 주세요.")
                return
            nodes, rename_map = self._build_nodes_payload(payload)
            self._save_nodes(nodes, rename_map=rename_map, apply_runtime=False)
        except Exception as exc:
            self._vars["status"].set(f"저장 실패: {exc}")
            return

        self._on_message("현재 실행 중인 내 PC 변경을 저장했습니다. 재시작 후 반영됩니다.", "warning")
        messagebox.showinfo(
            "재시작 필요",
            impact_text + "\n\n현재 실행은 그대로 유지되고, 프로그램을 다시 시작하면 새 설정이 적용됩니다.",
            parent=self.window,
        )
        self.close()

    def _delete(self):
        from tkinter import messagebox

        if self._selected_name is None:
            self._vars["status"].set("삭제할 노드를 먼저 선택해 주세요.")
            return
        if self._selected_name == self.ctx.self_node.node_id:
            self._vars["status"].set("내 PC는 삭제할 수 없습니다.")
            return
        if not messagebox.askyesno(
            "노드 삭제",
            f"{self._selected_name} 노드를 삭제할까요?\n레이아웃과 모니터 보정 정보도 함께 정리됩니다.",
            parent=self.window,
        ):
            return

        nodes = [
            _node_to_payload(node)
            for node in self.ctx.nodes
            if node.node_id != self._selected_name
        ]
        try:
            self._save_nodes(nodes, rename_map={}, apply_runtime=True)
        except Exception as exc:
            self._vars["status"].set(f"삭제 실패: {exc}")
            return

        removed = self._selected_name
        self._selected_name = None
        self._on_message(f"{removed} 노드를 삭제했습니다.", "warning")
        self._new_node()
        self.refresh()

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
            return (
                False,
                "즉시 반영: 새 노드를 추가하고 레이아웃에는 빈 타일을 하나 더 붙입니다.",
            )

        current = self.ctx.get_node(self._selected_name)
        if current is None:
            return (False, "즉시 반영: 현재 노드를 다시 불러옵니다.")

        current_roles = tuple(current.roles)
        next_roles = tuple(payload["roles"])
        changed = []
        if payload["name"] != current.node_id:
            changed.append("이름")
        if payload["ip"] != current.ip:
            changed.append("IP")
        if payload["port"] != current.port:
            changed.append("포트")
        if next_roles != current_roles:
            changed.append("역할")
        if not changed:
            return (False, "변경된 내용이 없습니다.")

        if current.node_id == self.ctx.self_node.node_id:
            detail = ", ".join(changed)
            if payload["name"] != current.node_id:
                return (
                    True,
                    f"재시작 필요: 내 PC의 {detail}이 바뀝니다. 다시 시작할 때 --node-name {payload['name']} 을 사용해야 합니다.",
                )
            return (
                True,
                f"재시작 필요: 내 PC의 {detail}이 바뀝니다. 설정만 저장하고 현재 실행은 유지합니다.",
            )

        if payload["name"] != current.node_id:
            return (
                False,
                "즉시 반영: 노드 이름을 바꾸고 레이아웃/모니터 설정 키도 함께 옮깁니다.",
            )
        return (
            False,
            "즉시 반영: 연결 대상 목록과 레이아웃이 새 값으로 다시 계산됩니다.",
        )

    def _set_button_state(self, widget, enabled: bool):
        if hasattr(widget, "state"):
            widget.state(["!disabled"] if enabled else ["disabled"])

    def _has_unsaved_changes(self) -> bool:
        try:
            payload = self._collect_form(require_complete=False)
        except ValueError:
            return any(
                (
                    self._vars["name"].get().strip(),
                    self._vars["ip"].get().strip(),
                    self._vars["port"].get().strip(),
                )
            )
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
            and tuple(payload["roles"]) == tuple(current.roles)
        )

    def _restore_latest_backup(self):
        from tkinter import messagebox

        if not callable(self._restore_nodes):
            self._vars["status"].set("복구 기능을 사용할 수 없습니다.")
            return
        latest = self._latest_backup()
        if latest is None:
            self._vars["status"].set("복구할 직전 저장이 없습니다.")
            return
        if not messagebox.askyesno(
            "직전 저장 복구",
            f"{latest.name} 백업으로 되돌릴까요?\n현재 노드 목록과 레이아웃 보정 정보가 함께 복구됩니다.",
            parent=self.window,
        ):
            return
        try:
            restored_path, applied_runtime, detail = self._restore_nodes()
        except Exception as exc:
            self._vars["status"].set(f"복구 실패: {exc}")
            return
        if applied_runtime:
            self._vars["status"].set("직전 저장을 복구하고 현재 실행에도 반영했습니다.")
            self._on_message(f"직전 저장을 복구했습니다. ({restored_path.name})", "success")
            self.refresh()
            return
        self._vars["status"].set("직전 저장을 복구했습니다. 재시작 후 반영됩니다.")
        self._on_message(
            f"직전 저장을 복구했습니다. 재시작 후 반영됩니다. ({restored_path.name})",
            "warning",
        )
        messagebox.showinfo(
            "재시작 필요",
            detail + "\n\n복구된 설정은 저장되었고, 프로그램을 다시 시작하면 반영됩니다.",
            parent=self.window,
        )


def _node_to_payload(node) -> dict:
    payload = {"name": node.node_id, "ip": node.ip, "port": node.port}
    if getattr(node, "roles", None):
        payload["roles"] = list(node.roles)
    return payload
