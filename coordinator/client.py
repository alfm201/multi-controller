"""
CoordinatorClient: coordinator 가 아닌 모든 노드에서 실행되는 control plane 클라이언트.

책임:
  - coordinator 노드의 PeerConnection 을 통해 claim/release/heartbeat 전송
  - coordinator 에게서 오는 grant/deny 프레임을 FrameDispatcher 에서 받아 처리
  - grant 시 InputRouter.set_active_target 을 호출해 data plane 을 연결

v1 스텁의 단순화:
  - heartbeat 주기 전송 루프는 아직 없음 (추후 추가)
  - grant 를 받기 전에 claim 호출 결과를 기다리는 동기 API 는 아직 없음
  - 이 클라이언트는 지금 router.active_target 을 갱신만 한다.
    실제 claim 트리거(핫키, GUI) 는 후속 작업.
"""

import logging

from coordinator.protocol import make_claim, make_heartbeat, make_release


class CoordinatorClient:
    def __init__(self, ctx, registry, dispatcher, coordinator_node, router=None):
        self.ctx = ctx
        self.registry = registry
        self.dispatcher = dispatcher
        self.coordinator_node = coordinator_node
        self.router = router

        dispatcher.register_control_handler("ctrl.grant", self._on_grant)
        dispatcher.register_control_handler("ctrl.deny", self._on_deny)

    def start(self):
        logging.info(
            f"[COORDINATOR CLIENT] started (coordinator={self.coordinator_node.node_id})"
        )

    # ------------------------------------------------------------
    # outbound
    # ------------------------------------------------------------
    def _send(self, frame) -> bool:
        conn = self.registry.get(self.coordinator_node.node_id)
        if conn is None:
            logging.info(
                f"[COORDINATOR CLIENT] no conn to coordinator "
                f"{self.coordinator_node.node_id}"
            )
            return False
        return conn.send_frame(frame)

    def claim(self, target_id: str) -> bool:
        return self._send(make_claim(target_id, self.ctx.self_node.node_id))

    def release(self, target_id: str) -> bool:
        return self._send(make_release(target_id, self.ctx.self_node.node_id))

    def heartbeat(self, target_id: str) -> bool:
        return self._send(make_heartbeat(target_id, self.ctx.self_node.node_id))

    # ------------------------------------------------------------
    # inbound
    # ------------------------------------------------------------
    def _on_grant(self, peer_id, frame):
        target_id = frame.get("target_id")
        logging.info(f"[COORDINATOR CLIENT] GRANT target={target_id}")
        if self.router is not None and target_id:
            self.router.set_active_target(target_id)

    def _on_deny(self, peer_id, frame):
        target_id = frame.get("target_id")
        reason = frame.get("reason")
        logging.info(
            f"[COORDINATOR CLIENT] DENY target={target_id} reason={reason}"
        )
