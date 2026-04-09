"""
coordinator 선출.

v1: 순수 정적 priority.
  - config.coordinator.candidates 가 우선순위 순서 리스트.
  - 리스트에서 nodes 에 실제로 존재하는 첫 번째 항목이 coordinator 다.
  - 모든 노드가 같은 config 를 공유하므로 선출 결과는 결정적이다.
  - self 가 그 노드면 CoordinatorService 를 띄우고, 아니면 CoordinatorClient 를 띄운다.

v2 (향후): liveness-aware.
  - 최상위 후보가 일정 시간 연결되지 않으면 그 다음 후보로 넘어간다.
  - 상위 후보가 복귀하면 다시 그쪽으로 하락.
  - 단, data plane 은 coordinator 와 무관하므로 election flap 이 입력 전송을
    끊지 않는다는 전제는 유지한다.
"""


def pick_coordinator(ctx):
    for name in ctx.coordinator_candidates:
        node = ctx.get_node(name)
        if node is not None:
            return node
    return None


def is_self_coordinator(ctx) -> bool:
    picked = pick_coordinator(ctx)
    return picked is not None and picked.node_id == ctx.self_node.node_id
