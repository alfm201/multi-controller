"""현재 온라인 멤버를 기준으로 coordinator를 고르는 로직."""


def online_node_ids(ctx, registry):
    """자기 자신과 현재 살아 있는 peer의 node_id를 정렬해서 반환한다."""
    online = {ctx.self_node.node_id}
    for node_id, conn in registry.all():
        if conn is not None and not conn.closed and ctx.get_node(node_id) is not None:
            online.add(node_id)
    return sorted(online)


def pick_coordinator(ctx, registry):
    """온라인 노드 중 node_id가 가장 작은 노드를 coordinator로 본다."""
    node_ids = online_node_ids(ctx, registry)
    if not node_ids:
        return ctx.self_node
    return ctx.get_node(node_ids[0]) or ctx.self_node


def is_self_coordinator(ctx, registry) -> bool:
    """현재 노드가 coordinator로 선출되었는지 확인한다."""
    picked = pick_coordinator(ctx, registry)
    return picked is not None and picked.node_id == ctx.self_node.node_id
