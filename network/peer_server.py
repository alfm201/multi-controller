"""Inbound TCP 연결을 받아 handshake 후 registry에 바인딩한다."""

import logging
import socket
import threading

from network.frames import encode_frame
from network.handshake import HELLO_TIMEOUT, recv_hello, send_hello
from network.peer_connection import PeerConnection
from network.peer_reject import REJECT_REASON_UNKNOWN_NODE, make_peer_reject
from runtime.app_version import get_current_compatibility_version, get_current_version


class PeerServer:
    LISTEN_BACKLOG = 64

    def __init__(self, ctx, registry, dispatcher):
        self.ctx = ctx
        self.registry = registry
        self.dispatcher = dispatcher
        self._server_sock = None
        self._thread = None
        self._stop = threading.Event()
        self._bootstrap_handler = None

    def start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # 특정 NIC가 아니라 모든 인터페이스에서 연결을 받는다.
        sock.bind(("0.0.0.0", self.ctx.self_node.port))
        sock.listen(self.LISTEN_BACKLOG)
        self._server_sock = sock

        logging.info(
            "[PEER SERVER] listening 0.0.0.0:%s (self=%s)",
            self.ctx.self_node.port,
            self.ctx.self_node.node_id,
        )

        self._thread = threading.Thread(
            target=self._accept_loop,
            daemon=True,
            name="peer-server",
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

    def set_bootstrap_handler(self, handler) -> None:
        self._bootstrap_handler = handler

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
            send_hello(
                sock,
                self.ctx.self_node.node_id,
                app_version=get_current_version(),
                compatibility_version=get_current_compatibility_version(),
            )
            peer_hello = recv_hello(sock)
        except Exception as exc:
            logging.info("[PEER HANDSHAKE FAIL] inbound from %s: %s", addr, exc)
            try:
                sock.close()
            except OSError:
                pass
            return
        sock.settimeout(None)

        if peer_hello.bootstrap:
            handler = self._bootstrap_handler
            try:
                if callable(handler):
                    response = handler(peer_hello, addr)
                    if isinstance(response, dict):
                        sock.sendall(encode_frame(response))
            except Exception as exc:
                logging.warning("[PEER BOOTSTRAP FAIL] inbound from %s: %s", addr, exc)
            finally:
                try:
                    sock.close()
                except OSError:
                    pass
            return

        if self.ctx.get_node(peer_hello.node_id) is None:
            logging.info(
                "[PEER HANDSHAKE REJECT] unknown node_id=%r from %s",
                peer_hello.node_id,
                addr,
            )
            try:
                sock.sendall(
                    encode_frame(
                        make_peer_reject(
                            REJECT_REASON_UNKNOWN_NODE,
                            detail="상대 노드 목록에 현재 PC 정보가 없습니다.",
                        )
                    )
                )
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
            return

        conn = PeerConnection(
            sock=sock,
            peer_node_id=peer_hello.node_id,
            on_frame=self.dispatcher.dispatch,
            on_close=self.registry.unbind,
            peer_app_version=peer_hello.app_version,
            peer_compatibility_version=peer_hello.compatibility_version,
        )
        if not self.registry.bind(peer_hello.node_id, conn, notify=False):
            logging.info(
                "[PEER HANDSHAKE DUPLICATE] %s from %s, closing loser",
                peer_hello.node_id,
                addr,
            )
            conn.close()
            return

        conn.start()
        self.registry.notify_bound_ready(peer_hello.node_id, conn)
        logging.info("[PEER ACCEPTED] %s from %s", peer_hello.node_id, addr)

