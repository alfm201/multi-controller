"""
Hotkey 매칭 + target 순환 — pure logic.

capture/input_capture.py 가 pynput listener 안에서 이 모듈의 함수들을 호출한다.
pynput 에 직접 의존하지 않고, 이미 core/events._normalize_key 로 뽑아낸
문자열만 다룬다. 덕분에 테스트에서는 pynput 없이 키 문자열 시퀀스만
넘겨도 동작을 검증할 수 있다.

두 개의 객체만 노출한다:
  - HotkeyMatcher:   "지금까지 눌린 키들" 을 추적해서 특정 조합이 완성되면
                     callback 을 호출한다. trigger 키의 press/release 는
                     소비됐다고 표시해 capture 측이 이벤트 큐로 내보내지
                     않게 한다.
  - TargetCycler:    RuntimeContext + InputRouter + (선택적) CoordinatorClient
                     를 받아서, 호출될 때마다 target 을 peers 중 target 역할
                     노드들 사이에서 다음 것으로 전환한다.

설계 메모
  - Modifier 는 "같은 의미의 키 여러 개" 를 받는다. 예: Ctrl 은 `Key.ctrl`,
    `Key.ctrl_l`, `Key.ctrl_r` 중 어느 하나만 눌려 있어도 매치된다.
    pynput 이 OS 별로 세 가지 중 무엇을 돌려주는지 일정하지 않기 때문.
  - 트리거 키는 하나만. match 조건은 "트리거가 press 되는 순간 모든 modifier
    그룹이 하나 이상 눌려 있는가" 다.
  - trigger press 가 match 되면 내부 flag `_consumed_trigger` 를 켜서, 이후
    같은 trigger 의 release 도 소비된다. modifier 자체는 수동적으로 추적만
    하고 소비하지 않는다 (사용자가 길게 누른 Ctrl/Shift 가 그대로 흐르는 편이
    덜 이상하기 때문).
"""

import logging
from typing import Callable, Iterable, List, Optional


class HotkeyMatcher:
    """
    하나의 단축키 조합을 감시한다.

    Args:
        modifier_groups: 각 원소가 동의어 그룹인 리스트.
                         예) [("Key.ctrl","Key.ctrl_l","Key.ctrl_r"),
                              ("Key.shift","Key.shift_l","Key.shift_r")]
        trigger:         매칭의 실제 발화 키. 예) "Key.tab"
        callback:        매치 순간 호출할 0-인자 함수.
        name:            로그용 이름. 선택.
    """

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
        self._trigger = trigger
        self._callback = callback
        self._name = name or f"hotkey[{trigger}]"
        self._down: set = set()
        self._consumed_trigger = False

    def on_press(self, key_str: Optional[str]) -> bool:
        """
        key_str 이 press 되었을 때 호출. match 가 성립하면 True 를 돌리고
        capture 측은 해당 press 이벤트를 드롭해야 한다.
        """
        if not key_str:
            return False
        self._down.add(key_str)
        if key_str != self._trigger:
            return False
        if not self._all_modifiers_down():
            return False
        self._consumed_trigger = True
        logging.info(f"[HOTKEY] {self._name} matched")
        try:
            self._callback()
        except Exception as e:
            # callback 실패로 listener 스레드가 죽는 걸 막는다.
            logging.warning(f"[HOTKEY] {self._name} callback raised: {e}")
        return True

    def on_release(self, key_str: Optional[str]) -> bool:
        """
        key_str 이 release 되었을 때 호출. 해당 키가 trigger 이고 직전에
        consume 됐다면 True 를 돌려 capture 측이 release 도 드롭하게 한다.
        """
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
    """
    peers 중 target 역할을 가진 노드들 사이에서 active target 을 순환시킨다.
    호출 시:
      1. 현재 active target 의 다음 대상을 계산.
      2. (coord_client 가 있으면) claim 을 먼저 보낸다 — best effort.
      3. router.set_active_target(next) 호출 — 로컬 data plane 즉시 전환.
         coord grant 가 돌아오면 동일 id 에 대해 set_active_target 이 한 번
         더 호출되지만 router 쪽이 idempotent 하므로 무해.

    Args:
        ctx:          RuntimeContext
        router:       InputRouter
        coord_client: CoordinatorClient | None (있으면 claim 도 보냄)
    """

    def __init__(self, ctx, router, coord_client=None):
        self.ctx = ctx
        self.router = router
        self.coord_client = coord_client

    def targets(self) -> List[str]:
        return [
            n.node_id for n in self.ctx.peers if n.has_role("target")
        ]

    def cycle(self) -> Optional[str]:
        """
        다음 target 으로 전환한다. 사용 가능한 target 이 없으면 None 을 돌리고
        아무 것도 하지 않는다. 성공 시 전환한 target id 를 돌린다.
        """
        targets = self.targets()
        if not targets:
            logging.info("[HOTKEY CYCLE] no target-role peers available")
            return None

        current = self.router.get_active_target()
        if current in targets:
            idx = (targets.index(current) + 1) % len(targets)
        else:
            idx = 0
        next_id = targets[idx]

        if next_id == current:
            # 유일한 target 이 이미 활성 상태
            return next_id

        logging.info(f"[HOTKEY CYCLE] {current} -> {next_id}")
        if self.coord_client is not None:
            # best effort. 실패(연결 없음)는 경고 로그로 남기되 로컬 전환은 계속한다.
            try:
                self.coord_client.claim(next_id)
            except Exception as e:
                logging.warning(f"[HOTKEY CYCLE] claim failed: {e}")
        self.router.set_active_target(next_id)
        return next_id
