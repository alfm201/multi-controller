"""One-shot monitor inventory detection and publishing."""

from __future__ import annotations

import logging

from runtime.monitor_inventory import detect_monitor_inventory


class MonitorInventoryManager:
    def __init__(self, ctx, coord_client=None, config_reloader=None):
        self.ctx = ctx
        self.coord_client = coord_client
        self.config_reloader = config_reloader

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
