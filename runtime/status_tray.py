"""시스템 tray 기반의 간단한 운영 UI."""

import logging
import threading
from dataclasses import dataclass

from runtime.status_view import build_status_view


@dataclass(frozen=True)
class TrayTargetAction:
    """tray 메뉴에 표시할 target 전환 항목."""

    node_id: str
    label: str
    enabled: bool
    selected: bool


def build_tray_title(view):
    """현재 상태를 짧은 tray tooltip 문자열로 만든다."""
    coordinator = view.coordinator_id or "-"
    target = view.selected_target or "-"
    state = view.router_state or "-"
    return (
        f"multi-controller [{view.self_id}] "
        f"coord={coordinator} target={target} state={state}"
    )


def build_tray_target_actions(view):
    """tray target 메뉴에 필요한 읽기 전용 항목 목록을 만든다."""
    actions = []
    for target in view.targets:
        parts = [target.node_id]
        parts.append("online" if target.online else "offline")
        if target.selected:
            parts.append(target.state or "selected")
        actions.append(
            TrayTargetAction(
                node_id=target.node_id,
                label=" | ".join(parts),
                enabled=target.online,
                selected=target.selected,
            )
        )
    return tuple(actions)


class StatusTray:
    """pystray 기반 tray 아이콘과 동적 메뉴를 제공한다."""

    def __init__(
        self,
        ctx,
        registry,
        coordinator_resolver,
        router=None,
        sink=None,
        coord_client=None,
        config_reloader=None,
        refresh_sec=1.0,
    ):
        self.ctx = ctx
        self.registry = registry
        self.coordinator_resolver = coordinator_resolver
        self.router = router
        self.sink = sink
        self.coord_client = coord_client
        self.config_reloader = config_reloader
        self.refresh_sec = refresh_sec

        self._icon = None
        self._menu_cls = None
        self._item_cls = None
        self._separator = None
        self._stop = threading.Event()
        self._refresh_thread = None
        self._on_exit = None

    def run(self, on_exit):
        """tray 아이콘 메인 루프를 시작한다."""
        try:
            import pystray
            from PIL import Image, ImageDraw
        except Exception as exc:
            raise RuntimeError(
                "tray support requires 'pystray' and 'Pillow' to be installed"
            ) from exc

        self._on_exit = on_exit
        self._menu_cls = pystray.Menu
        self._item_cls = pystray.MenuItem
        self._separator = pystray.Menu.SEPARATOR

        self._icon = pystray.Icon(
            "multi-controller",
            self._create_icon_image(Image, ImageDraw),
            build_tray_title(self._build_view()),
            menu=self._build_menu(),
        )

        self._stop.clear()
        self._refresh_thread = threading.Thread(
            target=self._refresh_loop,
            daemon=True,
            name="status-tray-refresh",
        )
        self._refresh_thread.start()

        logging.info("[TRAY] started")
        try:
            self._icon.run()
        finally:
            self.stop()

    def stop(self):
        self._stop.set()
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                logging.exception("[TRAY] stop failed")
            self._icon = None
        if self._refresh_thread is not None:
            self._refresh_thread.join(timeout=1.0)
            self._refresh_thread = None

    def _build_view(self):
        return build_status_view(
            self.ctx,
            self.registry,
            self.coordinator_resolver,
            router=self.router,
            sink=self.sink,
        )

    def _refresh_loop(self):
        while not self._stop.wait(self.refresh_sec):
            self._refresh_icon()

    def _refresh_icon(self):
        if self._icon is None:
            return
        view = self._build_view()
        self._icon.title = build_tray_title(view)
        self._icon.menu = self._build_menu(view)
        self._icon.update_menu()

    def _build_menu(self, view=None):
        view = view or self._build_view()
        item = self._item_cls
        menu = self._menu_cls

        target_items = []
        for action in build_tray_target_actions(view):
            target_items.append(
                item(
                    action.label,
                    lambda icon, menu_item, node_id=action.node_id: self._select_target(node_id),
                    enabled=lambda menu_item, enabled=action.enabled: enabled,
                    checked=lambda menu_item, selected=action.selected: selected,
                    radio=True,
                )
            )

        if not target_items:
            target_items.append(item("사용 가능한 target 없음", None, enabled=False))

        summary = (
            f"coord={view.coordinator_id or '-'} | "
            f"target={view.selected_target or '-'} | "
            f"connected={view.connected_peer_count}/{view.total_peer_count}"
        )

        return menu(
            item(summary, None, enabled=False),
            self._separator,
            item("Config Reload", self._reload_config, enabled=self.config_reloader is not None),
            item(
                "선택 해제",
                self._clear_target,
                enabled=view.selected_target is not None and self.coord_client is not None,
            ),
            self._separator,
            item("Target 전환", menu(*target_items)),
            self._separator,
            item("종료", self._quit),
        )

    def _reload_config(self, icon, _item):
        if self.config_reloader is None:
            return
        try:
            self.config_reloader.reload()
        except Exception:
            logging.exception("[TRAY] config reload failed")
        self._refresh_icon()

    def _clear_target(self, icon, _item):
        if self.coord_client is None:
            return
        self.coord_client.clear_target()
        self._refresh_icon()

    def _select_target(self, node_id):
        if self.coord_client is None:
            return
        self.coord_client.request_target(node_id)
        self._refresh_icon()

    def _quit(self, icon, _item):
        if self._on_exit is not None:
            self._on_exit()
        self.stop()

    def _create_icon_image(self, image_cls, draw_cls):
        image = image_cls.new("RGB", (64, 64), "#f5f1e8")
        draw = draw_cls(image)
        draw.rounded_rectangle((6, 6, 58, 58), radius=12, fill="#1f4d3a")
        draw.rectangle((18, 18, 46, 30), fill="#f5f1e8")
        draw.rectangle((18, 34, 30, 46), fill="#f0b429")
        draw.rectangle((34, 34, 46, 46), fill="#9fd3c7")
        return image
