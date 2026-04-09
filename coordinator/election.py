"""Coordinator election based on currently online members."""


def online_node_ids(ctx, registry):
    online = {ctx.self_node.node_id}
    for node_id, conn in registry.all():
        if conn is not None and not conn.closed and ctx.get_node(node_id) is not None:
            online.add(node_id)
    return sorted(online)


def pick_coordinator(ctx, registry):
    node_ids = online_node_ids(ctx, registry)
    if not node_ids:
        return ctx.self_node
    return ctx.get_node(node_ids[0]) or ctx.self_node


def is_self_coordinator(ctx, registry) -> bool:
    picked = pick_coordinator(ctx, registry)
    return picked is not None and picked.node_id == ctx.self_node.node_id
