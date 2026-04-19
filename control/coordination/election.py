"""현재 온라인 멤버를 기준으로 coordinator를 고르는 로직."""

from __future__ import annotations

import time

DEFAULT_COORDINATOR_PRIORITY = 0
DEFAULT_HEALTH_GRACE_SEC = 1.0
DEFAULT_HANDOFF_HOLD_DOWN_SEC = 3.0
LAST_PRIORITY_SORT_VALUE = 1_000_000_000


def coordinator_priority_sort_value(raw_priority) -> int:
    try:
        priority = DEFAULT_COORDINATOR_PRIORITY if raw_priority in (None, "") else int(raw_priority)
    except (TypeError, ValueError):
        return LAST_PRIORITY_SORT_VALUE
    if priority <= 0:
        return LAST_PRIORITY_SORT_VALUE
    return priority


def _coordinator_sort_key(node) -> tuple[int, str]:
    priority = getattr(node, "priority", DEFAULT_COORDINATOR_PRIORITY)
    return coordinator_priority_sort_value(priority), str(node.node_id)


def _online_nodes(ctx, registry, *, excluding_node_id: str | None = None):
    online = {}
    if ctx.self_node.node_id != excluding_node_id:
        online[ctx.self_node.node_id] = ctx.self_node
    for node_id, conn in registry.all():
        if node_id == excluding_node_id:
            continue
        if conn is None or conn.closed:
            continue
        node = ctx.get_node(node_id)
        if node is not None:
            online[node_id] = node
    return list(online.values())


def online_node_ids(ctx, registry):
    """자기 자신과 현재 살아 있는 peer의 node_id를 정렬해서 반환한다."""
    return sorted(node.node_id for node in _online_nodes(ctx, registry))


def pick_coordinator(ctx, registry, *, excluding_node_id: str | None = None):
    """온라인 노드 중 우선순위 숫자가 가장 낮은 노드를 coordinator로 본다."""
    candidates = _online_nodes(ctx, registry, excluding_node_id=excluding_node_id)
    if not candidates:
        return ctx.self_node
    return min(candidates, key=_coordinator_sort_key)


class CoordinatorElection:
    """우선순위, health, hold-down을 반영해 coordinator를 선발한다."""

    def __init__(
        self,
        ctx,
        registry,
        *,
        health_grace_sec: float = DEFAULT_HEALTH_GRACE_SEC,
        hold_down_sec: float = DEFAULT_HANDOFF_HOLD_DOWN_SEC,
    ) -> None:
        self.ctx = ctx
        self.registry = registry
        self.health_grace_sec = max(0.0, float(health_grace_sec))
        self.hold_down_sec = max(0.0, float(hold_down_sec))
        now = time.monotonic()
        self._online_since: dict[str, float] = {ctx.self_node.node_id: now}
        self._current_coordinator_id: str | None = None
        self._challenger_id: str | None = None
        self._challenger_since = 0.0
        if hasattr(registry, "add_listener"):
            registry.add_listener(self._on_registry_event)
        self._sync_online_tracking(now)

    def pick(self):
        now = time.monotonic()
        self._sync_online_tracking(now)
        candidates = _online_nodes(self.ctx, self.registry)
        if not candidates:
            self._set_current(self.ctx.self_node.node_id)
            return self.ctx.self_node

        online_by_id = {node.node_id: node for node in candidates}
        current = online_by_id.get(str(self._current_coordinator_id or ""))
        best_online = min(candidates, key=_coordinator_sort_key)
        healthy_candidates = [
            node
            for node in candidates
            if self._is_healthy(node.node_id, now)
        ]
        best_healthy = (
            min(healthy_candidates, key=_coordinator_sort_key)
            if healthy_candidates
            else None
        )

        if current is None:
            chosen = best_healthy or best_online
            self._set_current(chosen.node_id)
            return chosen

        preferred = best_healthy or current
        if _coordinator_sort_key(current) <= _coordinator_sort_key(preferred):
            self._clear_challenger()
            return current

        if self.hold_down_sec <= 0:
            self._set_current(preferred.node_id)
            return preferred

        if self._challenger_id != preferred.node_id:
            self._challenger_id = preferred.node_id
            self._challenger_since = self._online_since.get(preferred.node_id, now)
            return current

        if (now - self._challenger_since) < self.hold_down_sec:
            return current

        self._set_current(preferred.node_id)
        return preferred

    def _on_registry_event(self, event: str, node_id: str) -> None:
        now = time.monotonic()
        if event == "bound":
            if node_id != self.ctx.self_node.node_id and self.ctx.get_node(node_id) is not None:
                self._online_since.setdefault(node_id, now)
            return
        if event == "unbound":
            self._online_since.pop(node_id, None)
            if self._challenger_id == node_id:
                self._clear_challenger()

    def _sync_online_tracking(self, now: float) -> None:
        online_ids = {node.node_id for node in _online_nodes(self.ctx, self.registry)}
        online_ids.add(self.ctx.self_node.node_id)
        for node_id in online_ids:
            self._online_since.setdefault(node_id, now)
        for node_id in list(self._online_since):
            if node_id == self.ctx.self_node.node_id:
                continue
            if node_id not in online_ids:
                self._online_since.pop(node_id, None)

    def _is_healthy(self, node_id: str, now: float) -> bool:
        if node_id == self.ctx.self_node.node_id:
            return True
        since = self._online_since.get(node_id)
        if since is None:
            return False
        return (now - since) >= self.health_grace_sec

    def _clear_challenger(self) -> None:
        self._challenger_id = None
        self._challenger_since = 0.0

    def _set_current(self, node_id: str) -> None:
        self._current_coordinator_id = node_id
        self._clear_challenger()


def is_self_coordinator(ctx, registry) -> bool:
    """현재 노드가 coordinator로 선출되었는지 확인한다."""
    picked = pick_coordinator(ctx, registry)
    return picked is not None and picked.node_id == ctx.self_node.node_id
