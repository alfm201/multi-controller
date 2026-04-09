"""
Input event 팩토리.

이 모듈은 "이벤트 값을 만드는 것" 만 담당한다.
Wire 직렬화는 network/frames.py, 송/수신은 network/peer_connection.py 의 일이다.

모든 이벤트는 kind 필드로 구분된다. kind 값의 목록은
network/dispatcher.py 의 INPUT_KINDS 와 같아야 한다.
"""

import time


def now_ts() -> float:
    return time.time()


def _normalize_key(key):
    try:
        return key.char
    except AttributeError:
        return str(key)


def make_key_down_event(key) -> dict:
    return {
        "ts": now_ts(),
        "kind": "key_down",
        "key": _normalize_key(key),
    }


def make_key_up_event(key) -> dict:
    return {
        "ts": now_ts(),
        "kind": "key_up",
        "key": _normalize_key(key),
    }


def make_mouse_move_event(x, y) -> dict:
    return {
        "ts": now_ts(),
        "kind": "mouse_move",
        "x": x,
        "y": y,
    }


def make_mouse_button_event(x, y, button, pressed) -> dict:
    return {
        "ts": now_ts(),
        "kind": "mouse_button",
        "x": x,
        "y": y,
        "button": str(button),
        "pressed": pressed,
    }


def make_mouse_wheel_event(x, y, dx, dy) -> dict:
    return {
        "ts": now_ts(),
        "kind": "mouse_wheel",
        "x": x,
        "y": y,
        "dx": dx,
        "dy": dy,
    }


def make_system_event(message: str) -> dict:
    """
    로컬 전용 시스템 이벤트 (예: ESC 로 capture 종료 시 main 에 알림).
    InputRouter 는 system 이벤트를 원격으로 보내지 않는다.
    """
    return {
        "ts": now_ts(),
        "kind": "system",
        "message": message,
    }
