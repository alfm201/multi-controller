"""
연결 토폴로지 필터.

should_connect(self_roles, peer_roles) -> bool

어떤 두 역할 조합 사이에 TCP 연결이 필요한지 판단한다.
양측 모두 같은 함수를 호출하면 같은 답을 낸다 (symmetric).

정의된 에지:
  controller ↔ target      : controller 가 입력을 target 으로 전송
  controller ↔ coordinator : controller 가 ctrl.* 메시지를 coordinator 로 전송
                              (coordinator 는 같은 소켓으로 grant/deny 를 역방향으로 보냄)

target ↔ coordinator 에지는 없다. target 은 control plane 에 참여하지 않는다.
controller ↔ controller 에지는 없다. 두 controller 가 서로 입력을 보낼 이유가 없다.
"""

_CONNECTION_EDGES = frozenset({
    frozenset(("controller", "target")),
    frozenset(("controller", "coordinator")),
    frozenset(("target", "coordinator")),
})


def should_connect(self_roles, peer_roles) -> bool:
    """
    self_roles 와 peer_roles 사이에 최소 하나의 유효한 에지가 있으면 True.

    두 노드가 모두 이 함수를 호출하면 항상 같은 결론에 도달하므로
    한 쪽만 dial 해도 양방향 연결이 성립된다.
    """
    for a in self_roles:
        for b in peer_roles:
            if frozenset((a, b)) in _CONNECTION_EDGES:
                return True
    return False
