"""One-shot monitor inventory detection and publishing."""

from __future__ import annotations

import logging
import threading

from runtime.monitor_inventory import detect_monitor_inventory


class MonitorInventoryManager:
    def __init__(self, ctx, coord_client=None, config_reloader=None):
        self.ctx = ctx
        self.coord_client = coord_client
        self.config_reloader = config_reloader
        self._lock = threading.Lock()
        self._refresh_thread = None

    def refresh(self):
        snapshot = detect_monitor_inventory(self.ctx.self_node.node_id)
        if self.config_reloader is not None:
            self.config_reloader.apply_monitor_inventory(snapshot, persist=True)
        else:
            self.ctx.replace_monitor_inventory(snapshot)
        if self.coord_client is not None:
            self.coord_client.publish_monitor_inventory(snapshot)
        logging.info(
            "[MONITOR INVENTORY] node=%s detected=%s",
            snapshot.node_id,
            len(snapshot.monitors),
        )
        return snapshot

    def refresh_async(self, on_complete=None, on_error=None) -> bool:
        with self._lock:
            if self._refresh_thread is not None and self._refresh_thread.is_alive():
                return False
            self._refresh_thread = threading.Thread(
                target=self._refresh_async_worker,
                args=(on_complete, on_error),
                daemon=True,
                name="monitor-inventory-refresh",
            )
            self._refresh_thread.start()
        return True

    def _refresh_async_worker(self, on_complete, on_error):
        try:
            snapshot = self.refresh()
        except Exception as exc:  # pragma: no cover - defensive callback path
            logging.exception("[MONITOR INVENTORY] async refresh failed")
            if callable(on_error):
                on_error(exc)
            return
        if callable(on_complete):
            on_complete(snapshot)
