"""peer 하나와의 단일 TCP 연결을 양방향으로 관리한다."""

import json
import logging
import select
import socket
import threading
import time


class PeerConnection:
    MAX_BUFFER_BYTES = 1 << 20  # 1 MiB 상한으로 과도한 입력을 막는다.
    SEND_TIMEOUT_SEC = 1.0
    SEND_CHUNK_BYTES = 16 * 1024

    def __init__(
        self,
        sock,
        peer_node_id,
        on_frame,
        on_close,
        *,
        peer_app_version=None,
        peer_compatibility_version=None,
    ):
        self.sock = sock
        self.peer_node_id = peer_node_id
        self.peer_app_version = peer_app_version
        self.peer_compatibility_version = peer_compatibility_version
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
        # 작은 입력 이벤트가 지연되지 않도록 TCP_NODELAY를 켠다.
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        # half-open 연결을 좀 더 빨리 감지할 수 있게 keepalive를 켠다.
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
            logging.exception("[PEER CLOSE ERROR]")

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
                self._send_payload(payload)
            return True
        except (OSError, TimeoutError, ValueError) as exc:
            logging.info("[PEER SEND FAIL] %s: %s", self.peer_node_id, exc)
            self.close()
            return False

    def _send_payload(self, payload: bytes) -> None:
        deadline = time.monotonic() + self.SEND_TIMEOUT_SEC
        view = memoryview(payload)
        while view and not self._closed.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("peer send timed out")
            _readable, writable, exceptional = select.select(
                [],
                [self.sock],
                [self.sock],
                remaining,
            )
            if exceptional:
                raise OSError("peer socket entered exceptional state")
            if not writable:
                continue
            try:
                sent = self.sock.send(view[: self.SEND_CHUNK_BYTES])
            except BlockingIOError:
                continue
            if sent <= 0:
                raise OSError("peer socket send returned 0")
            view = view[sent:]
        if view:
            raise OSError("peer connection closed during send")

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
                        "[PEER OVERSIZE] %s buffer=%s bytes, closing",
                        self.peer_node_id,
                        len(buf),
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
                            "[PEER BAD FRAME] %s: %r",
                            self.peer_node_id,
                            line[:120],
                        )
                        continue
                    try:
                        self._on_frame(self.peer_node_id, frame)
                    except Exception:
                        logging.exception("[PEER FRAME ERROR]")
        finally:
            self.close()

