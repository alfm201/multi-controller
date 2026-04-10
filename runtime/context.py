"""런타임 전반에서 공유하는 노드/설정 컨텍스트."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

from runtime.self_detect import detect_self_node


@dataclass(frozen=True)
class NodeInfo:
    """config.nodes의 한 항목을 런타임에서 쓰기 좋은 형태로 정리한 객체."""

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
    def from_dict(cls, data: dict, default_roles=None) -> "NodeInfo":
        fallback = default_roles if default_roles is not None else ("controller", "target")
        roles = tuple(data.get("roles") or fallback)
        return cls(
            name=data["name"],
            ip=data["ip"],
            port=int(data["port"]),
            roles=roles,
        )


@dataclass
class RuntimeContext:
    """현재 노드와 전체 노드 목록, 설정 파일 경로를 묶어 둔 컨텍스트."""

    self_node: NodeInfo
    nodes: List[NodeInfo]
    config_path: Optional[Path] = None

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


def build_runtime_context(
    config: dict,
    override_name: Optional[str],
    config_path: Any,
) -> RuntimeContext:
    """config와 self 탐지 결과를 바탕으로 RuntimeContext를 만든다."""
    raw_nodes = config["nodes"]
    self_dict = detect_self_node(raw_nodes, override_name=override_name)

    default_roles = config.get("default_roles")
    nodes = [NodeInfo.from_dict(node, default_roles=default_roles) for node in raw_nodes]
    self_node = next(node for node in nodes if node.name == self_dict["name"])

    return RuntimeContext(
        self_node=self_node,
        nodes=nodes,
        config_path=Path(config_path) if config_path else None,
    )

