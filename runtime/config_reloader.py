"""실행 중 설정을 다시 읽거나 저장하는 유틸리티."""

from __future__ import annotations

import logging
from pathlib import Path
import shutil
import threading
from datetime import datetime, timedelta

from runtime.app_settings import AppSettings, serialize_app_settings
from runtime.config_loader import load_config, related_config_paths, save_config
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

BACKUP_ROOT_MARKER = ".multiscreenpass-backups"
BACKUP_DIR_MARKER = ".multiscreenpass-backup"


def validate_reloadable_self(current_self, new_self):
    """재시작 없이 유지 가능한 self 정보인지 확인한다."""
    if current_self.name != new_self.name:
        raise ValueError("config reload cannot change self node name")
    if current_self.ip != new_self.ip:
        raise ValueError("config reload cannot change self node ip")
    if current_self.port != new_self.port:
        raise ValueError("config reload cannot change self node port")


class RuntimeConfigReloader:
    """config 파일을 다시 읽고 peer 구성과 레이아웃 상태를 반영한다."""

    LAYOUT_SAVE_DEBOUNCE_SEC = 0.4
    BACKUP_PRUNE_INTERVAL_SEC = 24 * 60 * 60

    def __init__(self, ctx, dialer=None, router=None, coord_client=None):
        self.ctx = ctx
        self.dialer = dialer
        self.router = router
        self.coord_client = coord_client

        self._lock = threading.Lock()
        self._pending_layout = None
        self._pending_layout_version = 0
        self._save_timer = None
        self._backup_prune_lock = threading.Lock()
        self._backup_prune_stop = threading.Event()
        self._backup_prune_thread = None

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


    def save_nodes(
        self,
        node_payloads: list[dict],
        *,
        rename_map: dict[str, str] | None = None,
        apply_runtime: bool = True,
    ):
        """Persist node CRUD changes and reconcile layout/monitor sections."""
        self.flush_pending_layout()
        self.backup_current_config(label="nodes")
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
        if apply_runtime:
            self._apply_config_snapshot(config, resolved_path, refresh_peers=True)
        else:
            self.ctx.config_path = resolved_path
        logging.info("[CONFIG] saved nodes path=%s", resolved_path)
        return self.ctx

    def save_settings(self, settings: AppSettings):
        """Persist application-level settings and reflect them in runtime context."""
        config, resolved_path = self._load_current_config()
        config["settings"] = serialize_app_settings(settings)
        save_config(config, resolved_path)
        self.ctx.replace_settings(settings)
        self.ctx.config_path = resolved_path
        self.prune_backups(settings=settings)
        logging.info("[CONFIG] saved settings path=%s", resolved_path)
        return self.ctx

    def backup_current_config(self, *, label: str = "config") -> Path:
        config_path = self.ctx.config_path
        if config_path is None:
            raise ValueError("config path is unavailable")
        paths = related_config_paths(config_path)
        backup_root = paths["config"].parent / "backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        self._ensure_backup_root_marker(backup_root)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        destination = backup_root / f"{stamp}-{label}"
        destination.mkdir(parents=True, exist_ok=False)
        (destination / BACKUP_DIR_MARKER).write_text("Multi Screen Pass backup\n", encoding="utf-8")
        for source in paths.values():
            if source.exists():
                shutil.copy2(source, destination / source.name)
        self.prune_backups()
        logging.info("[CONFIG] backup created path=%s", destination)
        return destination

    def get_latest_backup_path(self) -> Path | None:
        config_path = self.ctx.config_path
        if config_path is None:
            return None
        backup_root = related_config_paths(config_path)["config"].parent / "backups"
        candidates = self._managed_backup_directories(backup_root)
        if not candidates:
            return None
        return sorted(candidates)[-1]

    def restore_latest_backup(self) -> tuple[Path, bool, str]:
        latest = self.get_latest_backup_path()
        if latest is None:
            raise FileNotFoundError("복구할 백업이 없습니다.")
        backup_config_path = latest / "config.json"
        if not backup_config_path.exists():
            raise FileNotFoundError(f"백업 config.json이 없습니다: {latest}")
        config, _resolved_backup = load_config(backup_config_path)
        current_path = self.ctx.config_path
        if current_path is None:
            raise ValueError("config path is unavailable")
        save_config(config, current_path)
        try:
            self._apply_config_snapshot(config, current_path, refresh_peers=True)
        except ValueError as exc:
            self.ctx.config_path = current_path
            logging.info("[CONFIG] restored backup path=%s restart_required=%s", latest, exc)
            return latest, False, str(exc)
        logging.info("[CONFIG] restored backup path=%s", latest)
        return latest, True, "직전 백업을 현재 실행에 바로 반영했습니다."

    def prune_backups(self, *, settings: AppSettings | None = None) -> list[Path]:
        config_path = self.ctx.config_path
        if config_path is None:
            return []
        backup_root = related_config_paths(config_path)["config"].parent / "backups"
        candidates = self._managed_backup_directories(backup_root)
        if not candidates:
            return []

        retention = (settings or self.ctx.settings).backups
        protected = set(
            sorted(
                candidates,
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )[: retention.min_count]
        )
        cutoff = datetime.now() - timedelta(days=retention.max_age_days)
        removed: list[Path] = []
        for path in candidates:
            if path in protected:
                continue
            modified = datetime.fromtimestamp(path.stat().st_mtime)
            if modified > cutoff:
                continue
            shutil.rmtree(path, ignore_errors=True)
            removed.append(path)
        if removed:
            logging.info(
                "[CONFIG] pruned backups count=%s paths=%s",
                len(removed),
                [str(path) for path in removed],
            )
        return removed

    def start_periodic_backup_pruning(self, *, interval_sec: float | None = None) -> bool:
        prune_interval = (
            self.BACKUP_PRUNE_INTERVAL_SEC if interval_sec is None else max(1.0, float(interval_sec))
        )
        with self._backup_prune_lock:
            if self._backup_prune_thread is not None and self._backup_prune_thread.is_alive():
                return False
            self._backup_prune_stop = threading.Event()
            self._backup_prune_thread = threading.Thread(
                target=self._backup_prune_worker,
                args=(prune_interval, self._backup_prune_stop),
                daemon=True,
                name="backup-pruner",
            )
            worker = self._backup_prune_thread

        self._run_periodic_backup_prune(reason="startup")
        worker.start()
        return True

    def stop_periodic_backup_pruning(self) -> bool:
        with self._backup_prune_lock:
            thread = self._backup_prune_thread
            stop_event = self._backup_prune_stop
            self._backup_prune_thread = None
        if thread is None:
            return False
        stop_event.set()
        thread.join(timeout=1.0)
        return True

    def _managed_backup_directories(self, backup_root: Path) -> list[Path]:
        if not backup_root.exists():
            return []
        if not self._is_managed_backup_root(backup_root):
            logging.info("[CONFIG] skip backup pruning for unmanaged root path=%s", backup_root)
            return []
        return [
            path
            for path in backup_root.iterdir()
            if path.is_dir() and (path / BACKUP_DIR_MARKER).is_file()
        ]

    def _is_managed_backup_root(self, backup_root: Path) -> bool:
        return (backup_root / BACKUP_ROOT_MARKER).is_file()

    def _ensure_backup_root_marker(self, backup_root: Path) -> None:
        marker = backup_root / BACKUP_ROOT_MARKER
        if not marker.exists():
            marker.write_text("Managed by Multi Screen Pass\n", encoding="utf-8")

    def _backup_prune_worker(self, interval_sec: float, stop_event: threading.Event) -> None:
        while not stop_event.wait(interval_sec):
            self._run_periodic_backup_prune(reason="periodic")

        with self._backup_prune_lock:
            if self._backup_prune_stop is stop_event:
                self._backup_prune_thread = None

    def _run_periodic_backup_prune(self, *, reason: str) -> None:
        try:
            removed = self.prune_backups()
        except Exception:
            logging.exception("[CONFIG] backup prune failed reason=%s", reason)
            return
        logging.debug(
            "[CONFIG] backup prune completed reason=%s removed=%s",
            reason,
            len(removed),
        )

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
        self.ctx.replace_settings(next_ctx.settings)
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
        if target is not None and target.node_id != self.ctx.self_node.node_id:
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
