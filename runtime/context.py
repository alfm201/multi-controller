"""런타임 전반에서 공유하는 노드/설정 컨텍스트."""

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, List, Optional

from runtime.app_settings import AppSettings, load_app_settings
from runtime.layouts import LayoutConfig, build_layout_config
from runtime.monitor_inventory import (
    MonitorInventorySnapshot,
    deserialize_monitor_inventory_snapshot,
)
from runtime.self_detect import detect_self_node


@dataclass(frozen=True)
class NodeInfo:
    """config.nodes의 한 항목을 런타임에서 쓰기 좋은 형태로 정리한 객체."""

    name: str
    ip: str
    port: int
    note: str = ""

    @property
    def node_id(self) -> str:
        return self.name

    @property
    def roles(self) -> tuple[str, str]:
        """Legacy compatibility shim: every node can both control and receive input."""
        return ("controller", "target")

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def label(self) -> str:
        suffix = f" / {self.note}" if self.note else ""
        return f"{self.name}({self.ip}:{self.port}){suffix}"

    @classmethod
    def from_dict(cls, data: dict, default_roles=None) -> "NodeInfo":
        return cls(
            name=data["name"],
            ip=data["ip"],
            port=int(data["port"]),
            note=str(data.get("note", "") or "").strip(),
        )


@dataclass
class RuntimeContext:
    """현재 노드와 전체 노드 목록, 설정 파일 경로를 묶어 둔 컨텍스트."""

    self_node: NodeInfo
    nodes: List[NodeInfo]
    config_path: Optional[Path] = None
    layout: Optional[LayoutConfig] = None
    monitor_inventories: dict[str, MonitorInventorySnapshot] = field(default_factory=dict)
    settings: AppSettings = field(default_factory=AppSettings)
    _pending_join_node_ids: set[str] = field(default_factory=set, repr=False)
    _pending_join_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    @property
    def peers(self) -> List[NodeInfo]:
        return [node for node in self.nodes if node.node_id != self.self_node.node_id]

    def get_node(self, node_id: str) -> Optional[NodeInfo]:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        return None

    def replace_nodes(self, nodes: List[NodeInfo]) -> None:
        """자기 자신 정보를 유지한 채 전체 노드 목록을 교체한다."""
        self.nodes = list(nodes)

    def replace_layout(self, layout: LayoutConfig) -> None:
        """현재 런타임 레이아웃을 교체한다."""
        self.layout = layout

    def replace_monitor_inventory(self, snapshot: MonitorInventorySnapshot) -> None:
        """Store the latest detected monitor inventory for a node."""
        if snapshot.node_id:
            self.monitor_inventories[snapshot.node_id] = snapshot

    def replace_monitor_inventories(
        self, snapshots: dict[str, MonitorInventorySnapshot]
    ) -> None:
        self.monitor_inventories = dict(snapshots)

    def get_monitor_inventory(self, node_id: str) -> Optional[MonitorInventorySnapshot]:
        return self.monitor_inventories.get(node_id)

    def replace_settings(self, settings: AppSettings) -> None:
        self.settings = settings

    def set_pending_join_nodes(self, node_ids: Iterable[str]) -> None:
        with self._pending_join_lock:
            self._pending_join_node_ids = {
                normalized
                for raw_node_id in node_ids
                if (normalized := str(raw_node_id).strip())
            }

    def clear_pending_join_nodes(self, node_ids: Iterable[str] | None = None) -> None:
        with self._pending_join_lock:
            if node_ids is None:
                self._pending_join_node_ids.clear()
                return
            for raw_node_id in node_ids:
                normalized = str(raw_node_id).strip()
                if normalized:
                    self._pending_join_node_ids.discard(normalized)

    def is_pending_join_node(self, node_id: str) -> bool:
        normalized = str(node_id).strip()
        if not normalized:
            return False
        with self._pending_join_lock:
            return normalized in self._pending_join_node_ids


def build_runtime_context(
    config: dict,
    override_name: Optional[str],
    config_path: Any,
) -> RuntimeContext:
    """config와 self 탐지 결과를 바탕으로 RuntimeContext를 만든다."""
    raw_nodes = config["nodes"]
    self_dict = detect_self_node(raw_nodes, override_name=override_name)

    nodes = [NodeInfo.from_dict(node) for node in raw_nodes]
    self_node = next(node for node in nodes if node.name == self_dict["name"])

    inventories = _build_monitor_inventory_map(config)

    return RuntimeContext(
        self_node=self_node,
        nodes=nodes,
        config_path=Path(config_path) if config_path else None,
        layout=build_layout_config(config, nodes),
        monitor_inventories=inventories,
        settings=load_app_settings(config),
    )


def _build_monitor_inventory_map(
    config: dict,
) -> dict[str, MonitorInventorySnapshot]:
    raw_nodes = (config.get("monitor_inventory") or {}).get("nodes") or {}
    snapshots = {}
    for node_id, payload in raw_nodes.items():
        if not isinstance(payload, dict):
            continue
        snapshot = deserialize_monitor_inventory_snapshot(payload)
        if snapshot.node_id and snapshot.node_id != node_id:
            continue
        if not snapshot.node_id:
            snapshot = MonitorInventorySnapshot(
                node_id=node_id,
                monitors=snapshot.monitors,
                captured_at=snapshot.captured_at,
            )
        snapshots[node_id] = snapshot
    return snapshots

