"""실행 중 config.json을 다시 읽어 런타임 peer 구성을 갱신한다."""

import logging

from runtime.config_loader import load_config
from runtime.context import build_runtime_context


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
    """config 파일을 다시 읽고 peer 관련 런타임 상태를 반영한다."""

    def __init__(self, ctx, dialer=None, router=None, coord_client=None):
        self.ctx = ctx
        self.dialer = dialer
        self.router = router
        self.coord_client = coord_client

    def reload(self):
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
