"""Pure hotkey logic for target cycling."""

import logging
from typing import Callable, Iterable, List, Optional


class HotkeyMatcher:
    def __init__(
        self,
        modifier_groups: Iterable[Iterable[str]],
        trigger: str,
        callback: Callable[[], None],
        name: Optional[str] = None,
    ):
        self._modifier_groups: List[frozenset] = [
            frozenset(group) for group in modifier_groups
        ]
        self._modifier_keys = frozenset().union(*self._modifier_groups)
        self._trigger = trigger
        self._callback = callback
        self._name = name or f"hotkey[{trigger}]"
        self._down: set = set()
        self._consumed_trigger = False

    def is_modifier_key(self, key_str: Optional[str]) -> bool:
        return bool(key_str) and key_str in self._modifier_keys

    def on_press(self, key_str: Optional[str]) -> bool:
        if not key_str:
            return False
        self._down.add(key_str)
        if key_str != self._trigger:
            return False
        if not self._all_modifiers_down():
            return False
        self._consumed_trigger = True
        logging.info("[HOTKEY] %s matched", self._name)
        try:
            self._callback()
        except Exception as exc:
            logging.warning("[HOTKEY] %s callback raised: %s", self._name, exc)
        return True

    def on_release(self, key_str: Optional[str]) -> bool:
        if not key_str:
            return False
        self._down.discard(key_str)
        if key_str == self._trigger and self._consumed_trigger:
            self._consumed_trigger = False
            return True
        return False

    def _all_modifiers_down(self) -> bool:
        for group in self._modifier_groups:
            if self._down.isdisjoint(group):
                return False
        return True


class TargetCycler:
    def __init__(self, ctx, router, coord_client=None, targets_provider=None):
        self.ctx = ctx
        self.router = router
        self.coord_client = coord_client
        self.targets_provider = targets_provider

    def targets(self) -> List[str]:
        if self.targets_provider is not None:
            return list(self.targets_provider())
        return [n.node_id for n in self.ctx.peers]

    def cycle(self) -> Optional[str]:
        return self.next()

    def next(self) -> Optional[str]:
        return self._step(1)

    def previous(self) -> Optional[str]:
        return self._step(-1)

    def _step(self, offset: int) -> Optional[str]:
        targets = self.targets()
        if not targets:
            logging.info("[HOTKEY CYCLE] no peers available")
            return None

        current = self.router.get_selected_target()
        if current in targets:
            idx = (targets.index(current) + offset) % len(targets)
        else:
            idx = 0 if offset > 0 else len(targets) - 1
        next_id = targets[idx]

        if next_id == current:
            return next_id

        logging.info("[HOTKEY CYCLE] %s -> %s", current, next_id)
        if self.coord_client is not None:
            try:
                self.router.set_pending_target(next_id)
                self.coord_client.request_target(next_id)
            except Exception as exc:
                logging.warning("[HOTKEY CYCLE] request failed: %s", exc)
        else:
            self.router.activate_target(next_id)
        return next_id
