"""
PeerServer: self_node 에 바인딩해 peer 들의 inbound TCP 연결을 받는다.

accept 하자마자 다음을 수행한다:
  1) HELLO 송신
  2) peer HELLO 수신 -> peer_node_id 확인
  3) PeerConnection 을 만들어 PeerRegistry 에 bind
  4) 이미 같은 node_id 로 conn 이 있으면 (= outbound dial 이 먼저 붙었거나
     peer 가 동시에 dial 해서 accept 도 두 번 일어난 경우) 진 쪽의 소켓을
     닫고 기존 conn 을 그대로 재사용한다.

handshake 처리는 accept 루프를 막지 않도록 별도 스레드에서 돌린다.
"""

import logging
import socket
import threading

from network.handshake import HELLO_TIMEOUT, recv_hello, send_hello
from network.peer_connection import PeerConnection


class PeerServer:
    LISTEN_BACKLOG = 64

    def __init__(self, ctx, registry, dispatcher):
        self.ctx = ctx
        self.registry = registry
        self.dispatcher = dispatcher
        self._server_sock = None
        self._thread = None
        self._stop = threading.Event()

    def start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # 어느 인터페이스로 들어오든 accept 가능하도록 INADDR_ANY 로 bind.
        # self_node.ip 는 self detect 용이지 listen 주소가 아니다.
        sock.bind(("0.0.0.0", self.ctx.self_node.port))
        sock.listen(self.LISTEN_BACKLOG)
        self._server_sock = sock

        logging.info(
            f"[PEER SERVER] listening 0.0.0.0:{self.ctx.self_node.port} "
            f"(self={self.ctx.self_node.node_id})"
        )

        self._thread = threading.Thread(
            target=self._accept_loop, daemon=True, name="peer-server"
        )
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._server_sock is not None:
            try:
                self._server_sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                logging.warning("[PEER SERVER] accept thread did not exit in time")

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                sock, addr = self._server_sock.accept()
            except OSError:
                break

            threading.Thread(
                target=self._handshake_inbound,
                args=(sock, addr),
                daemon=True,
                name=f"peer-handshake-{addr[0]}:{addr[1]}",
            ).start()

    def _handshake_inbound(self, sock, addr):
        sock.settimeout(HELLO_TIMEOUT)
        try:
            send_hello(sock, self.ctx.self_node.node_id)
            peer_id = recv_hello(sock)
        except Exception as e:
            logging.info(f"[PEER HANDSHAKE FAIL] inbound from {addr}: {e}")
            try:
                sock.close()
            except OSError:
                pass
            return
        sock.settimeout(None)

        if self.ctx.get_node(peer_id) is None:
            logging.info(
                f"[PEER HANDSHAKE REJECT] unknown node_id={peer_id!r} from {addr}"
            )
            try:
                sock.close()
            except OSError:
                pass
            return

        conn = PeerConnection(
            sock=sock,
            peer_node_id=peer_id,
            on_frame=self.dispatcher.dispatch,
            on_close=self.registry.unbind,
        )
        if not self.registry.bind(peer_id, conn):
            logging.info(
                f"[PEER HANDSHAKE DUPLICATE] {peer_id} from {addr}, closing loser"
            )
            conn.close()
            return

        conn.start()
        logging.info(f"[PEER ACCEPTED] {peer_id} from {addr}")
