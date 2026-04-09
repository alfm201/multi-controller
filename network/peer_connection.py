"""
PeerConnection: 하나의 peer 와의 단일 TCP 소켓을 양방향으로 사용한다.

- accepted (inbound) 소켓도 즉시 outbound 송신에 재사용할 수 있다.
- 이것이 "A 가 먼저 실행되고 B 가 3초 뒤 실행되면 B->A 는 즉시 연결되는데
  A->B 는 한참 지연된다" 문제의 해결 포인트다. 기존 구조는 inbound 연결을
  단지 receiver 용으로만 취급했기 때문에 A 는 자신의 outbound dial 이
  다음 retry tick 에 성공할 때까지 송신할 수 없었다. 이제는 B 가 A 에
  dial 해 들어온 순간 그 소켓이 A 의 PeerRegistry 에 등록되고, A 는
  그 소켓을 통해 즉시 송신할 수 있다.

수명주기:
  create -> start() -> send_frame()/recv callback -> close() (자동 또는 수동)
"""

import json
import logging
import socket
import threading


class PeerConnection:
    MAX_BUFFER_BYTES = 1 << 20  # 1 MiB — DoS / garbage-stream guard

    def __init__(self, sock, peer_node_id, on_frame, on_close):
        self.sock = sock
        self.peer_node_id = peer_node_id
        self._on_frame = on_frame
        self._on_close = on_close
        self._send_lock = threading.Lock()
        self._recv_thread = None
        self._closed = threading.Event()
        self._tune_socket()

    # ------------------------------------------------------------
    # socket tuning
    # ------------------------------------------------------------
    def _tune_socket(self):
        # 작은 입력 이벤트들이 Nagle 에 의해 밀리지 않도록
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        # half-open 감지용 OS 레벨 keepalive
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass

    # ------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------
    def start(self):
        self._recv_thread = threading.Thread(
            target=self._recv_loop,
            daemon=True,
            name=f"peer-recv-{self.peer_node_id}",
        )
        self._recv_thread.start()

    def close(self):
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        try:
            self._on_close(self.peer_node_id, self)
        except Exception:
            logging.exception("[PEER ON_CLOSE ERROR]")

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    # ------------------------------------------------------------
    # send / recv
    # ------------------------------------------------------------
    def send_frame(self, frame: dict) -> bool:
        if self._closed.is_set():
            return False
        try:
            payload = (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8")
            with self._send_lock:
                self.sock.sendall(payload)
            return True
        except OSError as e:
            logging.info(f"[PEER SEND FAIL] {self.peer_node_id}: {e}")
            self.close()
            return False

    def _recv_loop(self):
        buf = b""
        try:
            while not self._closed.is_set():
                try:
                    data = self.sock.recv(4096)
                except OSError:
                    break
                if not data:
                    break
                buf += data
                if len(buf) > self.MAX_BUFFER_BYTES:
                    logging.warning(
                        f"[PEER OVERSIZE] {self.peer_node_id} "
                        f"buffer={len(buf)} bytes, closing"
                    )
                    break
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        frame = json.loads(line.decode("utf-8"))
                    except Exception:
                        logging.warning(
                            f"[PEER BAD FRAME] {self.peer_node_id}: {line[:120]!r}"
                        )
                        continue
                    try:
                        self._on_frame(self.peer_node_id, frame)
                    except Exception:
                        logging.exception("[PEER ON_FRAME ERROR]")
        finally:
            self.close()
