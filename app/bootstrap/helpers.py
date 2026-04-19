"""런타임 시작 단계에서 공용으로 쓰는 보조 함수들."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path

from transport.peer.peer_reject import describe_peer_reject_reason
from control.routing.display_state import DisplayStateTracker
from app.logging.app_error_handler import install_unhandled_exception_handler
from app.meta.identity import APP_EXECUTABLE_NAME
from app.logging.app_logging import TAG_HOTKEY, TAG_STARTUP, tag_message
from app.config.config_loader import default_config_path, related_config_paths
from model.display.display import get_primary_screen_bounds, get_virtual_screen_bounds


def install_cursor_cleanup_hooks(*cleanup_actions, log_path=None):
    install_unhandled_exception_handler(
        app_name="Multi Screen Pass",
        cleanup_actions=cleanup_actions,
        log_path=log_path,
        delegate_previous=not getattr(sys, "frozen", False),
    )


def host_cursor_parking_point(ctx):
    snapshot = ctx.get_monitor_inventory(ctx.self_node.node_id)
    if snapshot is not None and snapshot.monitors:
        primary = next((item for item in snapshot.monitors if item.is_primary), None)
        chosen = primary or snapshot.ordered()[0]
        width = max(int(chosen.bounds.width), 1)
        height = max(int(chosen.bounds.height), 1)
        return (
            int(chosen.bounds.left) + max(width - 1, 0) // 2,
            int(chosen.bounds.top) + max(height - 1, 0) // 2,
        )
    bounds = get_primary_screen_bounds()
    return (
        int(bounds.left) + max(int(bounds.width) - 1, 0) // 2,
        int(bounds.top) + max(int(bounds.height) - 1, 0) // 2,
    )


def target_primary_display_id(ctx, target_id: str) -> str | None:
    layout = ctx.layout
    if layout is None:
        return None
    node = layout.get_node(target_id)
    if node is None:
        return None
    snapshot = ctx.get_monitor_inventory(target_id)
    if snapshot is not None and snapshot.monitors:
        primary = next((item for item in snapshot.monitors if item.is_primary), None)
        chosen = primary or snapshot.ordered()[0]
        return chosen.monitor_id
    logical = node.monitors().logical
    if logical:
        return logical[0].display_id
    physical = node.monitors().physical
    if physical:
        return physical[0].display_id
    return None


def build_target_primary_center_anchor(ctx, target_id: str):
    layout = ctx.layout
    if layout is None or target_id == ctx.self_node.node_id:
        return None
    node = layout.get_node(target_id)
    if node is None:
        return None
    display_id = target_primary_display_id(ctx, target_id)
    if not display_id:
        return None
    tracker = DisplayStateTracker(ctx)
    bounds = tracker.node_screen_bounds(target_id, node, get_virtual_screen_bounds())
    return tracker.build_display_center_event(node, display_id, bounds)


def park_local_cursor_for_active_target(local_cursor, ctx):
    x, y = host_cursor_parking_point(ctx)
    moved = local_cursor.move(x, y)
    cleared = local_cursor.clear_clip()
    return bool(moved and cleared)


def restore_local_cursor_after_target_exit(router, local_cursor, ctx):
    cleared = local_cursor.clear_clip()
    anchor_event = None
    if hasattr(router, "consume_local_return_anchor_event"):
        anchor_event = router.consume_local_return_anchor_event()
    if anchor_event is not None and "x" in anchor_event and "y" in anchor_event:
        moved = local_cursor.move(int(anchor_event["x"]), int(anchor_event["y"]))
        return bool(cleared and moved)
    x, y = host_cursor_parking_point(ctx)
    moved = local_cursor.move(x, y)
    return bool(cleared and moved)


def runtime_log_dir(config_path: Path | None) -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if local_appdata:
        return Path(local_appdata) / APP_EXECUTABLE_NAME / "logs"
    if config_path is None:
        config_path = default_config_path(None)
    config_dir = related_config_paths(config_path)["config"].parent
    if config_dir.name.lower() == "config":
        return config_dir.parent / "logs"
    return config_dir / "logs"


def format_peer_reject_notice(ctx, node_id: str, reason: str, detail: str = "") -> str:
    label = str(node_id or "").strip() or "상대 노드"
    if ctx is not None and hasattr(ctx, "get_node"):
        node = ctx.get_node(label)
        if node is not None:
            if hasattr(node, "display_label") and callable(node.display_label):
                label = node.display_label()
            else:
                name = str(getattr(node, "name", "") or label).strip() or label
                ip = str(getattr(node, "ip", "") or "").strip()
                label = f"{name}({ip})" if ip else name
    reason_text = describe_peer_reject_reason(reason, detail)
    return f"{label} 노드가 연결을 거부했습니다. 사유: {reason_text}"


def install_capture_hotkey_fallbacks(capture, matcher_cls, specs, *, registered_global_hotkeys=()):
    for spec in specs:
        capture.hotkey_matchers.append(
            matcher_cls(
                modifier_groups=spec["modifier_groups"],
                trigger=spec["trigger"],
                callback=spec["callback"],
                name=spec["matcher_name"],
            )
        )


class AsyncHotkeyAction:
    """Run potentially blocking hotkey work off the input hook thread."""

    def __init__(self, name: str):
        self._name = name
        self._lock = threading.Lock()
        self._busy = False

    def trigger(self, callback, *, on_busy=None) -> bool:
        busy = False
        with self._lock:
            if self._busy:
                busy = True
            else:
                self._busy = True
        if busy:
            logging.info(tag_message(TAG_HOTKEY, "%s ignored because a previous action is still running"), self._name)
            if callable(on_busy):
                on_busy()
            return False

        def worker():
            logging.info(tag_message(TAG_HOTKEY, "%s started async execution"), self._name)
            try:
                callback()
            except Exception:
                logging.exception(tag_message(TAG_HOTKEY, "%s async callback failed"), self._name)
            finally:
                with self._lock:
                    self._busy = False
                logging.info(tag_message(TAG_HOTKEY, "%s finished async execution"), self._name)

        threading.Thread(
            target=worker,
            daemon=True,
            name=f"hotkey-{self._name}",
        ).start()
        return True


def notify_runtime_message(qt_runtime_app, message: str, tone: str = "neutral") -> None:
    if qt_runtime_app is None:
        return
    if hasattr(qt_runtime_app, "request_notification"):
        qt_runtime_app.request_notification(message, tone)
        return
    has_status = hasattr(qt_runtime_app, "request_status_message")
    has_tray = hasattr(qt_runtime_app, "request_tray_notification")
    if has_status:
        qt_runtime_app.request_status_message(message, tone)
        if not has_tray:
            return
        try:
            qt_runtime_app.request_tray_notification(message, record_history=False)
        except TypeError:
            return
        return
    if has_tray:
        qt_runtime_app.request_tray_notification(message)


def start_local_input_services(capture, auto_switcher, global_hotkeys, shutdown_evt) -> None:
    if global_hotkeys is not None and not shutdown_evt.is_set():
        global_hotkeys.start()
        for binding_name in sorted(global_hotkeys.active_binding_names):
            logging.info(tag_message(TAG_HOTKEY, "%s registered as Windows global hotkey"), binding_name)
    if capture is not None and not shutdown_evt.is_set():
        capture.start()
        auto_switcher.sync_self_pointer_state()


def start_local_input_services_async(capture, auto_switcher, global_hotkeys, shutdown_evt):
    def worker():
        started_at = time.perf_counter()
        logging.info(tag_message(TAG_STARTUP, "initializing local input services in background"))
        try:
            start_local_input_services(capture, auto_switcher, global_hotkeys, shutdown_evt)
        except Exception:
            logging.exception(tag_message(TAG_STARTUP, "local input service initialization failed"))
        else:
            logging.info(
                tag_message(TAG_STARTUP, "local input services ready in %.3fs"),
                time.perf_counter() - started_at,
            )

    thread = threading.Thread(
        target=worker,
        daemon=True,
        name="startup-local-input",
    )
    thread.start()
    return thread
