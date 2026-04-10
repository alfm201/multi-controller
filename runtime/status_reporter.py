"""주기적으로 현재 런타임 상태를 로그로 남기는 도구."""

import logging
import threading


def build_status_snapshot(ctx, registry, coordinator_resolver, router=None, sink=None):
    """현재 노드의 핵심 상태를 한 줄 문자열로 만든다."""
    coordinator = coordinator_resolver()
    coordinator_id = None if coordinator is None else coordinator.node_id
    online_peers = sorted(
        node_id for node_id, conn in registry.all() if conn is not None and not conn.closed
    )

    parts = [
        f"self={ctx.self_node.node_id}",
        f"coordinator={coordinator_id}",
        f"online={online_peers}",
    ]

    if router is not None:
        parts.append(f"router_state={router.get_target_state()}")
        parts.append(f"selected_target={router.get_selected_target()}")

    if sink is not None:
        parts.append(f"authorized_controller={sink.get_authorized_controller()}")

    return " | ".join(parts)


class StatusReporter:
    """정해진 간격마다 상태 스냅샷을 기록하는 백그라운드 스레드."""

    def __init__(self, ctx, registry, coordinator_resolver, router=None, sink=None, interval_sec=10.0):
        self.ctx = ctx
        self.registry = registry
        self.coordinator_resolver = coordinator_resolver
        self.router = router
        self.sink = sink
        self.interval_sec = interval_sec
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self.interval_sec <= 0 or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="status-reporter",
        )
        self._thread.start()
        logging.info("[STATUS] reporter started interval=%ss", self.interval_sec)

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _run(self):
        while not self._stop.wait(self.interval_sec):
            logging.info(
                "[STATUS] %s",
                build_status_snapshot(
                    self.ctx,
                    self.registry,
                    self.coordinator_resolver,
                    router=self.router,
                    sink=self.sink,
                ),
            )
