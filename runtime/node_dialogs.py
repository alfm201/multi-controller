"""Dialogs for GUI-driven node management."""

from __future__ import annotations


class NodeManagerDialog:
    """Small CRUD dialog for node records."""

    def __init__(self, parent, ctx, save_nodes, on_message=None):
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self._ttk = ttk
        self.ctx = ctx
        self._save_nodes = save_nodes
        self._on_message = on_message or (lambda _message, _tone="neutral": None)
        self._selected_name = None

        self.window = tk.Toplevel(parent)
        self.window.title("노드 관리")
        self.window.geometry("700x420")
        self.window.minsize(640, 380)

        frame = ttk.Frame(self.window, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=2)
        frame.columnconfigure(1, weight=3)
        frame.rowconfigure(1, weight=1)

        ttk.Label(
            frame,
            text="노드 추가/수정/삭제를 GUI에서 바로 처리합니다. 기존 노드 이름 변경은 이번 단계에서 지원하지 않습니다.",
            wraplength=640,
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
            "status": tk.StringVar(value="왼쪽에서 노드를 선택하거나 새 노드를 추가하세요."),
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
        ttk.Label(
            editor,
            textvariable=self._vars["status"],
            wraplength=320,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))

        actions = ttk.Frame(editor)
        actions.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        ttk.Button(actions, text="새 노드", command=self._new_node).pack(side="left")
        ttk.Button(actions, text="저장", command=self._save).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="삭제", command=self._delete).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="닫기", command=self.close).pack(side="right")

        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self._refresh_list()

    def close(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.destroy()

    def refresh(self):
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
            label = f"{node.node_id}  ({node.ip}:{node.port})"
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
        self._selected_name = node.node_id
        self._vars["name"].set(node.node_id)
        self._vars["ip"].set(node.ip)
        self._vars["port"].set(str(node.port))
        is_self = node.node_id == self.ctx.self_node.node_id
        self._name_entry.state(["disabled"])
        self._ip_entry.state(["disabled"] if is_self else ["!disabled"])
        self._port_entry.state(["disabled"] if is_self else ["!disabled"])
        self._vars["status"].set(
            "내 PC는 실행 중 이름/IP/포트 변경을 지원하지 않습니다."
            if is_self
            else "기존 노드는 IP와 포트만 수정할 수 있습니다."
        )

    def _new_node(self):
        self._selected_name = None
        self._vars["name"].set("")
        self._vars["ip"].set("")
        self._vars["port"].set("")
        self._name_entry.state(["!disabled"])
        self._ip_entry.state(["!disabled"])
        self._port_entry.state(["!disabled"])
        self._vars["status"].set("새 노드 이름, IP, 포트를 입력한 뒤 저장하세요.")

    def _save(self):
        name = self._vars["name"].get().strip()
        ip = self._vars["ip"].get().strip()
        port_text = self._vars["port"].get().strip()
        if not name or not ip or not port_text:
            self._vars["status"].set("이름, IP, 포트를 모두 입력하세요.")
            return
        try:
            port = int(port_text)
        except ValueError:
            self._vars["status"].set("포트는 정수여야 합니다.")
            return
        if port <= 0:
            self._vars["status"].set("포트는 1 이상이어야 합니다.")
            return

        nodes = [
            {"name": node.node_id, "ip": node.ip, "port": node.port}
            for node in self.ctx.nodes
        ]
        if self._selected_name is None:
            if any(node["name"] == name for node in nodes):
                self._vars["status"].set("같은 이름의 노드가 이미 있습니다.")
                return
            nodes.append({"name": name, "ip": ip, "port": port})
        else:
            if self._selected_name == self.ctx.self_node.node_id:
                self._vars["status"].set("내 PC는 여기서 수정할 수 없습니다.")
                return
            for node in nodes:
                if node["name"] == self._selected_name:
                    node["ip"] = ip
                    node["port"] = port
                    break

        try:
            self._save_nodes(nodes)
        except Exception as exc:
            self._vars["status"].set(f"저장 실패: {exc}")
            return
        self._vars["status"].set("노드 변경을 저장했습니다.")
        self._on_message("노드 설정을 저장했습니다.", "accent")
        self.refresh()

    def _delete(self):
        from tkinter import messagebox

        if self._selected_name is None:
            self._vars["status"].set("삭제할 노드를 먼저 선택하세요.")
            return
        if self._selected_name == self.ctx.self_node.node_id:
            self._vars["status"].set("내 PC는 삭제할 수 없습니다.")
            return
        if not messagebox.askyesno("노드 삭제", f"{self._selected_name} 노드를 삭제할까요?"):
            return
        nodes = [
            {"name": node.node_id, "ip": node.ip, "port": node.port}
            for node in self.ctx.nodes
            if node.node_id != self._selected_name
        ]
        try:
            self._save_nodes(nodes)
        except Exception as exc:
            self._vars["status"].set(f"삭제 실패: {exc}")
            return
        removed = self._selected_name
        self._selected_name = None
        self._vars["status"].set(f"{removed} 노드를 삭제했습니다.")
        self._on_message(f"{removed} 노드를 삭제했습니다.", "warning")
        self._new_node()
        self.refresh()
