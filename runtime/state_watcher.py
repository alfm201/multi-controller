"""상태 변화가 생겼을 때만 운영 이벤트 로그를 남기는 감시기."""

from dataclasses import dataclass
import logging
import threading


@dataclass(frozen=True)
class RuntimeState:
    """비교 가능한 런타임 핵심 상태."""

    coordinator_id: str | None
    online_peers: tuple[str, ...]
    router_state: str | None
    selected_target: str | None
    authorized_controller: str | None


def collect_runtime_state(ctx, registry, coordinator_resolver, router=None, sink=None):
    """현재 런타임 상태를 비교용 객체로 수집한다."""
    coordinator = coordinator_resolver()
    return RuntimeState(
        coordinator_id=None if coordinator is None else coordinator.node_id,
        online_peers=tuple(
            sorted(node_id for node_id, conn in registry.all() if conn is not None and not conn.closed)
        ),
        router_state=None if router is None else router.get_target_state(),
        selected_target=None if router is None else router.get_selected_target(),
        authorized_controller=None if sink is None else sink.get_authorized_controller(),
    )


def describe_state_changes(previous: RuntimeState | None, current: RuntimeState):
    """이전 상태와 비교해 로그로 남길 메시지 목록을 만든다."""
    if previous is None:
        return []

    messages = []

    if previous.coordinator_id != current.coordinator_id:
        messages.append(
            f"[EVENT COORDINATOR] {previous.coordinator_id} -> {current.coordinator_id}"
        )

    joined = sorted(set(current.online_peers) - set(previous.online_peers))
    left = sorted(set(previous.online_peers) - set(current.online_peers))
    if joined or left:
        messages.append(
            f"[EVENT ONLINE] joined={joined} left={left} now={list(current.online_peers)}"
        )

    if (
        previous.router_state != current.router_state
        or previous.selected_target != current.selected_target
    ):
        messages.append(
            "[EVENT ROUTER] "
            f"{previous.router_state}:{previous.selected_target} -> "
            f"{current.router_state}:{current.selected_target}"
        )

    if previous.authorized_controller != current.authorized_controller:
        messages.append(
            "[EVENT LEASE] "
            f"{previous.authorized_controller} -> {current.authorized_controller}"
        )

    return messages


class StateWatcher:
    """주기적으로 상태 변화를 감시하고 변화가 있을 때만 로그를 남긴다."""

    def __init__(
        self,
        ctx,
        registry,
        coordinator_resolver,
        router=None,
        sink=None,
        interval_sec=1.0,
    ):
        self.ctx = ctx
        self.registry = registry
        self.coordinator_resolver = coordinator_resolver
        self.router = router
        self.sink = sink
        self.interval_sec = interval_sec
        self._stop = threading.Event()
        self._thread = None
        self._previous = None

    def start(self):
        if self.interval_sec <= 0 or self._thread is not None:
            return
        self._previous = collect_runtime_state(
            self.ctx,
            self.registry,
            self.coordinator_resolver,
            router=self.router,
            sink=self.sink,
        )
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="state-watcher",
        )
        self._thread.start()
        logging.info("[STATE WATCHER] started interval=%ss", self.interval_sec)

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _run(self):
        while not self._stop.wait(self.interval_sec):
            current = collect_runtime_state(
                self.ctx,
                self.registry,
                self.coordinator_resolver,
                router=self.router,
                sink=self.sink,
            )
            for message in describe_state_changes(self._previous, current):
                logging.info(message)
            self._previous = current
