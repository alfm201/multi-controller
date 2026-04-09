"""
Control plane 메시지 팩토리.

모든 control plane 프레임은 "ctrl." prefix 를 가진다. FrameDispatcher 가
이 prefix 로 input 이벤트와 구분해 routing 한다.

v1 메시지 집합:
  ctrl.claim      controller -> coordinator  : 특정 target 점유 요청
  ctrl.release    controller -> coordinator  : 점유 해제
  ctrl.heartbeat  controller -> coordinator  : 점유 유지 핑
  ctrl.grant      coordinator -> controller  : 점유 허가
  ctrl.deny       coordinator -> controller  : 점유 거절

추후 예정:
  ctrl.reclaim, ctrl.preempt, ctrl.lease_expired, ctrl.snapshot ...
"""


def make_claim(target_id: str, controller_id: str) -> dict:
    return {
        "kind": "ctrl.claim",
        "target_id": target_id,
        "controller_id": controller_id,
    }


def make_release(target_id: str, controller_id: str) -> dict:
    return {
        "kind": "ctrl.release",
        "target_id": target_id,
        "controller_id": controller_id,
    }


def make_heartbeat(target_id: str, controller_id: str) -> dict:
    return {
        "kind": "ctrl.heartbeat",
        "target_id": target_id,
        "controller_id": controller_id,
    }


def make_grant(target_id: str, controller_id: str) -> dict:
    return {
        "kind": "ctrl.grant",
        "target_id": target_id,
        "controller_id": controller_id,
    }


def make_deny(target_id: str, controller_id: str, reason: str) -> dict:
    return {
        "kind": "ctrl.deny",
        "target_id": target_id,
        "controller_id": controller_id,
        "reason": reason,
    }
