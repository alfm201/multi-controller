"""
peer 연결 셋업 시 맨 앞에서 주고받는 HELLO 한 줄.

양쪽이 서로의 node_id 를 선언해야 PeerRegistry 가 node_id 를 키로
연결을 등록/조회할 수 있다. ip:port 만으로는 같은 PC 에서 여러
인스턴스를 구분할 수 없으므로 반드시 node_id 기반이어야 한다.
"""

import json

from network.frames import encode_frame, make_hello

HELLO_TIMEOUT = 5.0
HELLO_MAX_BYTES = 8192


def send_hello(sock, self_node_id: str) -> None:
    sock.sendall(encode_frame(make_hello(self_node_id)))


def recv_hello(sock) -> str:
    """
    블로킹으로 한 줄(=\\n 종료) HELLO 프레임을 읽고 peer 의 node_id 를 돌려준다.
    호출자는 사전에 socket timeout 을 설정해두어야 한다.
    """
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(1024)
        if not chunk:
            raise ConnectionError("peer closed during hello")
        buf += chunk
        if len(buf) > HELLO_MAX_BYTES:
            raise ValueError("hello frame too large")

    line, _ = buf.split(b"\n", 1)
    frame = json.loads(line.decode("utf-8"))
    if frame.get("kind") != "hello":
        raise ValueError(f"expected hello, got kind={frame.get('kind')!r}")

    node_id = frame.get("node_id")
    if not isinstance(node_id, str) or not node_id:
        raise ValueError("hello missing node_id")
    return node_id
