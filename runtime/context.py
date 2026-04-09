"""
런타임 전반에서 공유하는 불변 컨텍스트.

- NodeInfo: config.nodes[] 한 항목의 타입드 뷰. name 이 곧 node_id 다.
- RuntimeContext: self_node, nodes, coordinator candidates, config_path 를 모아둔 값 객체.

모든 네트워크/라우팅/코디네이터 모듈은 이 RuntimeContext 하나만 참조한다.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from runtime.self_detect import detect_self_node


@dataclass(frozen=True)
class NodeInfo:
    name: str
    ip: str
    port: int
    roles: tuple

    @property
    def node_id(self) -> str:
        return self.name

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def label(self) -> str:
        return f"{self.name}({self.ip}:{self.port})"

    @classmethod
    def from_dict(cls, d: dict, default_roles=None) -> "NodeInfo":
        fallback = default_roles if default_roles is not None else ("controller", "target")
        roles = tuple(d.get("roles") or fallback)
        return cls(
            name=d["name"],
            ip=d["ip"],
            port=int(d["port"]),
            roles=roles,
        )


@dataclass
class RuntimeContext:
    self_node: NodeInfo
    nodes: List[NodeInfo]
    coordinator_candidates: List[str] = field(default_factory=list)
    config_path: Optional[Path] = None

    @property
    def peers(self) -> List[NodeInfo]:
        return [n for n in self.nodes if n.node_id != self.self_node.node_id]

    def get_node(self, node_id: str) -> Optional[NodeInfo]:
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        return None


def build_runtime_context(config: dict, override_name: Optional[str], config_path: Any) -> RuntimeContext:
    raw_nodes = config["nodes"]
    self_dict = detect_self_node(raw_nodes, override_name=override_name)

    default_roles = config.get("default_roles")
    nodes = [NodeInfo.from_dict(n, default_roles=default_roles) for n in raw_nodes]
    self_node = next(n for n in nodes if n.name == self_dict["name"])

    coord = config.get("coordinator") or {}
    candidates = list(coord.get("candidates") or [])

    return RuntimeContext(
        self_node=self_node,
        nodes=nodes,
        coordinator_candidates=candidates,
        config_path=Path(config_path) if config_path else None,
    )
