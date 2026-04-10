"""실행 중 config.json을 다시 읽거나 레이아웃만 반영하는 유틸리티."""

import logging
import threading

from runtime.config_loader import load_config, save_config
from runtime.context import build_runtime_context
from runtime.layouts import LayoutConfig, serialize_layout_config


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

        config_path = self.ctx.config_path
        if config_path is None:
            raise ValueError("config path is unavailable")

        config, resolved_path = load_config(config_path)
        next_ctx = build_runtime_context(
            config,
            override_name=self.ctx.self_node.node_id,
            config_path=resolved_path,
        )
        validate_reloadable_self(self.ctx.self_node, next_ctx.self_node)

        self.ctx.replace_nodes(next_ctx.nodes)
        self.ctx.replace_layout(next_ctx.layout)
        self.ctx.config_path = resolved_path
        self._reconcile_selected_target()

        if self.dialer is not None and hasattr(self.dialer, "refresh_peers"):
            self.dialer.refresh_peers()

        logging.info(
            "[CONFIG] reloaded peers=%s path=%s",
            [node.node_id for node in self.ctx.peers],
            resolved_path,
        )
        return self.ctx

    def save_layout(self, layout: LayoutConfig):
        """레이아웃을 config에 저장하고 현재 런타임에도 반영한다."""
        return self.apply_layout(layout, persist=True)

    def apply_layout(
        self,
        layout: LayoutConfig,
        persist: bool = True,
        debounce_persist: bool = False,
    ):
        """레이아웃을 현재 런타임에 적용하고 필요하면 config에도 저장한다."""
        self.ctx.replace_layout(layout)

        if not persist:
            return self.ctx

        if debounce_persist:
            self._schedule_layout_persist(layout)
        else:
            self._persist_layout_immediately(layout)
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
        config_path = self.ctx.config_path
        if config_path is None:
            raise ValueError("config path is unavailable")

        config, resolved_path = load_config(config_path)
        config["layout"] = serialize_layout_config(layout)
        save_config(config, resolved_path)
        self.ctx.config_path = resolved_path
        logging.info("[CONFIG] saved layout path=%s", resolved_path)

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
