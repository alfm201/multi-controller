"""주기적으로 현재 런타임 상태를 로그로 남기는 도구."""

import logging
import threading

from app.logging.app_logging import log_detail
from control.state.status_projection import (
    build_connection_summary_text,
    build_primary_status_text,
    build_status_view,
)


def build_status_snapshot(ctx, registry, coordinator_resolver, router=None, sink=None):
    """현재 노드의 핵심 상태를 한 줄 문자열로 만든다."""
    view = build_status_view(
        ctx,
        registry,
        coordinator_resolver,
        router=router,
        sink=sink,
    )

    parts = [
        f"self={view.self_id}",
        f"online={list(view.online_peers)}",
        build_connection_summary_text(view),
        build_primary_status_text(view),
        f"coordinator={view.coordinator_id or '-'}",
    ]

    if view.router_state is not None or view.selected_target is not None:
        parts.append(f"router_state={view.router_state}")
        parts.append(f"selected_target={view.selected_target}")

    if view.authorized_controller is not None:
        parts.append(f"authorized_controller={view.authorized_controller}")

    if view.monitor_alert:
        parts.append(f"monitor={view.monitor_alert}")

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
            log_detail(
                "[STATUS] %s",
                build_status_snapshot(
                    self.ctx,
                    self.registry,
                    self.coordinator_resolver,
                    router=self.router,
                    sink=self.sink,
                ),
            )
