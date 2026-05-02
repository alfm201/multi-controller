"""상태 변화가 생겼을 때만 운영 이벤트 로그를 남기는 감시기."""

from dataclasses import dataclass
import logging
import threading

from app.logging.app_logging import TAG_STATE, tag_message
from control.state.status_projection import build_status_view
from msp_platform.windows.self_detect import get_local_ips


@dataclass(frozen=True)
class RuntimeState:
    """비교 가능한 런타임 핵심 상태."""

    coordinator_id: str | None
    online_peers: tuple[str, ...]
    router_state: str | None
    requested_target: str | None
    active_target: str | None
    authorized_controller: str | None
    monitor_alert: str | None = None


def collect_runtime_state(ctx, registry, coordinator_resolver, router=None, sink=None):
    """현재 런타임 상태를 비교용 객체로 수집한다."""
    view = build_status_view(
        ctx,
        registry,
        coordinator_resolver,
        router=router,
        sink=sink,
    )
    router_state = None if router is None else router.get_target_state()
    requested_target = None
    active_target = None
    if router is not None:
        if hasattr(router, "get_requested_target"):
            requested_target = router.get_requested_target()
        else:
            requested_target = router.get_selected_target()
        if hasattr(router, "get_active_target"):
            active_target = router.get_active_target()
        elif router_state == "active":
            active_target = router.get_selected_target()
    return RuntimeState(
        coordinator_id=view.coordinator_id,
        online_peers=view.online_peers,
        router_state=router_state,
        requested_target=requested_target,
        active_target=active_target,
        authorized_controller=view.authorized_controller,
        monitor_alert=view.monitor_alert,
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
        or previous.requested_target != current.requested_target
        or previous.active_target != current.active_target
    ):
        messages.append(
            "[EVENT ROUTER] "
            f"{previous.router_state}:req={previous.requested_target},active={previous.active_target} -> "
            f"{current.router_state}:req={current.requested_target},active={current.active_target}"
        )

    if previous.authorized_controller != current.authorized_controller:
        messages.append(
            "[EVENT LEASE] "
            f"{previous.authorized_controller} -> {current.authorized_controller}"
        )

    if previous.monitor_alert != current.monitor_alert:
        messages.append(
            "[EVENT MONITOR] "
            f"{previous.monitor_alert or '-'} -> {current.monitor_alert or '-'}"
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
        self_ip_change_callback=None,
    ):
        self.ctx = ctx
        self.registry = registry
        self.coordinator_resolver = coordinator_resolver
        self.router = router
        self.sink = sink
        self.interval_sec = interval_sec
        self.self_ip_change_callback = self_ip_change_callback
        self._stop = threading.Event()
        self._thread = None
        self._previous = None
        self._last_self_ip = str(getattr(ctx.self_node, "ip", "") or "")
        self._last_local_ips = {ip for ip in get_local_ips() if ip}

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
        logging.info(tag_message(TAG_STATE, "watcher started interval=%ss"), self.interval_sec)

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
            self._detect_self_ip_change(current)
            self._previous = current

    def _detect_self_ip_change(self, state: RuntimeState | None = None) -> None:
        local_ips = {ip for ip in get_local_ips() if ip}
        current_ip = _resolve_runtime_self_ip(
            self._last_self_ip,
            local_ips=local_ips,
            previous_local_ips=self._last_local_ips,
        )
        needs_attention = bool(
            self._last_self_ip
            and self._last_self_ip not in local_ips
            and local_ips != self._last_local_ips
        )
        if not current_ip and self._last_self_ip and self._last_self_ip not in local_ips and local_ips != self._last_local_ips:
            logging.warning(
                tag_message(
                    TAG_STATE,
                    "self ip change is ambiguous current=%s local_ips=%s",
                ),
                self._last_self_ip,
                sorted(local_ips),
            )
        self._last_local_ips = local_ips
        if not current_ip:
            if needs_attention:
                callback = self.self_ip_change_callback
                if callable(callback):
                    callback(
                        self._last_self_ip,
                        "",
                        {
                            "local_ips": tuple(sorted(local_ips)),
                            "state": state,
                            "ambiguous": True,
                        },
                    )
            return
        if current_ip == self._last_self_ip:
            return
        previous_ip = self._last_self_ip
        callback = self.self_ip_change_callback
        if callable(callback):
            callback(
                previous_ip,
                current_ip,
                {
                    "local_ips": tuple(sorted(local_ips)),
                    "state": state,
                    "ambiguous": False,
                },
            )
        self._last_self_ip = current_ip


def _resolve_runtime_self_ip(
    current_ip: str,
    *,
    local_ips: set[str],
    previous_local_ips: set[str],
) -> str:
    non_loopback = sorted(ip for ip in local_ips if ip != "127.0.0.1")
    if current_ip == "127.0.0.1" and len(non_loopback) == 1:
        return non_loopback[0]
    if current_ip and current_ip in local_ips:
        return current_ip
    if len(non_loopback) == 1:
        return non_loopback[0]
    previous_non_loopback = {ip for ip in previous_local_ips if ip != "127.0.0.1"}
    new_non_loopback = sorted(ip for ip in non_loopback if ip not in previous_non_loopback)
    if len(new_non_loopback) == 1:
        return new_non_loopback[0]
    if not non_loopback:
        return "127.0.0.1"
    return ""
