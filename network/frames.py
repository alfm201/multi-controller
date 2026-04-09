"""
Wire 프레임 포맷.

현 단계에서는 line-delimited JSON 을 사용한다.
  <json>\n<json>\n...

하나의 peer connection 은 input 이벤트와 control 이벤트를 모두 나른다.
프레임을 구분하는 것은 "kind" 필드 한 개이다.

  - "hello", "bye", "ping", "pong"            : 연결 수준 (peer 레이어 자체에서 처리)
  - "key_down", "key_up", "mouse_move", ...   : data plane (InputRouter <-> InputSink)
  - "ctrl.*"                                  : control plane (CoordinatorService <-> CoordinatorClient)
"""

import json


def encode_frame(frame: dict) -> bytes:
    return (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8")


def decode_frame(line) -> dict:
    if isinstance(line, (bytes, bytearray)):
        line = line.decode("utf-8")
    return json.loads(line)


def make_hello(self_node_id: str) -> dict:
    return {"kind": "hello", "node_id": self_node_id}


def make_bye() -> dict:
    return {"kind": "bye"}


def make_ping() -> dict:
    return {"kind": "ping"}


def make_pong() -> dict:
    return {"kind": "pong"}
