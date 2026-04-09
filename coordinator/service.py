"""
CoordinatorService: 선출된 coordinator 노드에서만 실행되는 control plane 서비스.

책임:
  - target 별로 "지금 누가 lease 를 잡고 있는가" 를 기록한다.
  - claim 을 받으면 충돌 체크 후 grant / deny 응답.
  - release 를 받으면 해당 lease 를 제거.
  - heartbeat 는 lease 갱신 (v1 에서는 로깅만).

v1 스텁의 단순화:
  - lease 에 만료가 없다. (timer-based 재점유는 v2)
  - 같은 controller 의 재-claim 은 항상 grant.
  - 다른 controller 가 이미 잡고 있는 target 은 deny.
  - preemption 없음.
"""

import logging
import threading

from coordinator.protocol import make_deny, make_grant


class CoordinatorService:
    def __init__(self, ctx, registry, dispatcher):
        self.ctx = ctx
        self.registry = registry
        self.dispatcher = dispatcher

        self._lock = threading.Lock()
        self._leases = {}  # target_id -> controller_id

        dispatcher.register_control_handler("ctrl.claim", self._on_claim)
        dispatcher.register_control_handler("ctrl.release", self._on_release)
        dispatcher.register_control_handler("ctrl.heartbeat", self._on_heartbeat)

    def start(self):
        logging.info(
            f"[COORDINATOR SERVICE] started on self={self.ctx.self_node.node_id}"
        )

    # ------------------------------------------------------------
    def _reply(self, peer_id, frame):
        conn = self.registry.get(peer_id)
        if conn is None:
            logging.info(f"[COORDINATOR] no conn to reply to {peer_id}")
            return
        conn.send_frame(frame)

    def _on_claim(self, peer_id, frame):
        target_id = frame.get("target_id")
        controller_id = frame.get("controller_id") or peer_id
        if not target_id:
            return

        with self._lock:
            holder = self._leases.get(target_id)
            if holder is None or holder == controller_id:
                self._leases[target_id] = controller_id
                granted = True
            else:
                granted = False

        if granted:
            logging.info(
                f"[COORDINATOR] GRANT target={target_id} to {controller_id}"
            )
            self._reply(peer_id, make_grant(target_id, controller_id))
        else:
            logging.info(
                f"[COORDINATOR] DENY target={target_id} to {controller_id} "
                f"(held by {holder})"
            )
            self._reply(
                peer_id, make_deny(target_id, controller_id, "held_by_other")
            )

    def _on_release(self, peer_id, frame):
        target_id = frame.get("target_id")
        controller_id = frame.get("controller_id") or peer_id
        if not target_id:
            return

        with self._lock:
            if self._leases.get(target_id) == controller_id:
                del self._leases[target_id]
                released = True
            else:
                released = False

        if released:
            logging.info(
                f"[COORDINATOR] RELEASED target={target_id} by {controller_id}"
            )

    def _on_heartbeat(self, peer_id, frame):
        # v1: noop. v2 에서 lease 만료 타이머 리셋 용도로 사용.
        pass
