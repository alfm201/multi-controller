"""실행 중 설정을 다시 읽거나 저장하는 유틸리티."""

from __future__ import annotations

import logging
import threading

from runtime.config_loader import load_config, save_config
from runtime.context import build_runtime_context
from runtime.layouts import (
    LayoutConfig,
    append_layout_node,
    remove_layout_node,
    rename_layout_node,
    serialize_layout_config,
    serialize_monitor_overrides,
)
from runtime.monitor_inventory import (
    MonitorInventorySnapshot,
    serialize_monitor_inventory_snapshot,
)


def validate_reloadable_self(current_self, new_self):
    """재시작 없이 유지 가능한 self 정보인지 확인한다."""
    if current_self.name != new_self.name:
        raise ValueError("config reload cannot change self node name")
    if current_self.ip != new_self.ip:
        raise ValueError("config reload cannot change self node ip")
    if current_self.port != new_self.port:
        raise ValueError("config reload cannot change self node port")
    if tuple(current_self.roles) != tuple(new_self.roles):
        raise ValueError("config reload cannot change self node roles")


class RuntimeConfigReloader:
    """config 파일을 다시 읽고 peer 구성과 레이아웃 상태를 반영한다."""

    LAYOUT_SAVE_DEBOUNCE_SEC = 0.4

    def __init__(self, ctx, dialer=None, router=None, coord_client=None):
        self.ctx = ctx
        self.dialer = dialer
        self.router = router
        self.coord_client = coord_client

        self._lock = threading.Lock()
        self._pending_layout = None
        self._pending_layout_version = 0
        self._save_timer = None

    def reload(self):
        self.flush_pending_layout()
        config, resolved_path = self._load_current_config()
        self._apply_config_snapshot(config, resolved_path, refresh_peers=True)
        logging.info(
            "[CONFIG] reloaded peers=%s path=%s",
            [node.node_id for node in self.ctx.peers],
            resolved_path,
        )
        return self.ctx

    def save_layout(self, layout: LayoutConfig):
        """레이아웃을 설정 파일에 저장하고 현재 런타임에도 반영한다."""
        return self.apply_layout(layout, persist=True)

    def apply_layout(
        self,
        layout: LayoutConfig,
        persist: bool = True,
        debounce_persist: bool = False,
    ):
        """레이아웃을 현재 런타임에 적용하고 필요하면 설정에도 저장한다."""
        self.ctx.replace_layout(layout)

        if not persist:
            return self.ctx

        if debounce_persist:
            self._schedule_layout_persist(layout)
        else:
            self._persist_layout_immediately(layout)
        return self.ctx

    def apply_monitor_inventory(
        self,
        snapshot: MonitorInventorySnapshot,
        *,
        persist: bool = True,
    ):
        """감지된 모니터 정보를 현재 런타임과 저장 파일에 반영한다."""
        current = dict(self.ctx.monitor_inventories)
        current[snapshot.node_id] = snapshot
        self.ctx.replace_monitor_inventory(snapshot)

        if not persist:
            return self.ctx

        config, resolved_path = self._load_current_config()
        config["monitor_inventory"] = self._serialize_monitor_inventory_nodes(current)
        if self.ctx.layout is not None:
            config["monitor_overrides"] = serialize_monitor_overrides(self.ctx.layout, current)
        save_config(config, resolved_path)
        self._apply_config_snapshot(config, resolved_path, refresh_peers=False)
        logging.info("[CONFIG] saved monitor inventory node=%s path=%s", snapshot.node_id, resolved_path)
        return self.ctx


    def save_nodes(self, node_payloads: list[dict], *, rename_map: dict[str, str] | None = None):
        """Persist node CRUD changes and reconcile layout/monitor sections."""
        self.flush_pending_layout()
        config, resolved_path = self._load_current_config()
        known_before = {node["name"] for node in config.get("nodes", []) if isinstance(node, dict)}
        known_after = {node["name"] for node in node_payloads}
        removed = known_before - known_after
        added = known_after - known_before
        rename_map = {} if rename_map is None else dict(rename_map)

        layout = self.ctx.layout or build_runtime_context(config, self.ctx.self_node.node_id, resolved_path).layout
        if layout is None:
            raise ValueError("layout is unavailable")

        for old_name, new_name in rename_map.items():
            if old_name in removed or new_name not in known_after:
                continue
            layout = rename_layout_node(layout, old_name, new_name)

        for node_id in removed:
            layout = remove_layout_node(layout, node_id)

        for node_id in sorted(added):
            layout = append_layout_node(layout, node_id)

        config["nodes"] = list(node_payloads)
        config["layout"] = serialize_layout_config(layout, include_monitor_maps=False)

        monitor_inventories = {
            node_id: snapshot
            for node_id, snapshot in self.ctx.monitor_inventories.items()
            if node_id in known_after
        }
        config["monitor_inventory"] = self._serialize_monitor_inventory_nodes(monitor_inventories)
        config["monitor_overrides"] = serialize_monitor_overrides(layout, monitor_inventories)
        save_config(config, resolved_path)
        self._apply_config_snapshot(config, resolved_path, refresh_peers=True)
        logging.info("[CONFIG] saved nodes path=%s", resolved_path)
        return self.ctx

    def flush_pending_layout(self) -> bool:
        """대기 중인 레이아웃 저장이 있으면 즉시 flush한다."""
        with self._lock:
            timer = self._save_timer
            layout = self._pending_layout
            self._save_timer = None
            self._pending_layout = None
            self._pending_layout_version += 1
        if timer is not None:
            timer.cancel()
        if layout is None:
            return False
        self._persist_layout(layout)
        return True

    def _load_current_config(self):
        config_path = self.ctx.config_path
        if config_path is None:
            raise ValueError("config path is unavailable")
        return load_config(config_path)

    def _schedule_layout_persist(self, layout: LayoutConfig):
        with self._lock:
            self._pending_layout = layout
            self._pending_layout_version += 1
            version = self._pending_layout_version
            timer = self._save_timer
            self._save_timer = threading.Timer(
                self.LAYOUT_SAVE_DEBOUNCE_SEC,
                self._flush_pending_layout_version,
                args=(version,),
            )
            self._save_timer.daemon = True
            next_timer = self._save_timer

        if timer is not None:
            timer.cancel()
        next_timer.start()

    def _flush_pending_layout_version(self, version: int):
        with self._lock:
            if version != self._pending_layout_version:
                return
            layout = self._pending_layout
            self._pending_layout = None
            self._save_timer = None
        if layout is None:
            return
        self._persist_layout(layout)

    def _persist_layout_immediately(self, layout: LayoutConfig):
        self.flush_pending_layout()
        self._persist_layout(layout)

    def _persist_layout(self, layout: LayoutConfig):
        config, resolved_path = self._load_current_config()
        config["layout"] = serialize_layout_config(layout, include_monitor_maps=False)
        config["monitor_overrides"] = serialize_monitor_overrides(layout, self.ctx.monitor_inventories)
        save_config(config, resolved_path)
        self.ctx.config_path = resolved_path
        logging.info("[CONFIG] saved layout path=%s", resolved_path)

    def _apply_config_snapshot(self, config: dict, resolved_path, *, refresh_peers: bool):
        next_ctx = build_runtime_context(
            config,
            override_name=self.ctx.self_node.node_id,
            config_path=resolved_path,
        )
        validate_reloadable_self(self.ctx.self_node, next_ctx.self_node)

        self.ctx.replace_nodes(next_ctx.nodes)
        self.ctx.replace_layout(next_ctx.layout)
        self.ctx.replace_monitor_inventories(next_ctx.monitor_inventories)
        self.ctx.config_path = resolved_path
        self._reconcile_selected_target()

        if refresh_peers and self.dialer is not None and hasattr(self.dialer, "refresh_peers"):
            self.dialer.refresh_peers()
        return self.ctx

    def _reconcile_selected_target(self):
        if self.router is None:
            return

        target_id = self.router.get_selected_target()
        if not target_id:
            return

        target = self.ctx.get_node(target_id)
        if target is not None and target.has_role("target") and target.node_id != self.ctx.self_node.node_id:
            return

        logging.info("[CONFIG] clearing invalid selected target=%s after reload", target_id)
        if self.coord_client is not None:
            self.coord_client.clear_target()
        else:
            self.router.clear_target(reason="config-reload")

    def _serialize_monitor_inventory_nodes(
        self, monitor_inventories: dict[str, MonitorInventorySnapshot]
    ) -> dict:
        if not monitor_inventories:
            return {}
        return {
            "nodes": {
                node_id: serialize_monitor_inventory_snapshot(snapshot)
                for node_id, snapshot in monitor_inventories.items()
            }
        }
