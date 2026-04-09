"""각 peer로 outbound 연결을 유지하는 dialer."""

import logging
import socket
import threading
import time

from network.handshake import HELLO_TIMEOUT, recv_hello, send_hello
from network.peer_connection import PeerConnection
from routing.topology import should_connect


class PeerDialer:
    INITIAL_BACKOFF = 1.0
    MAX_BACKOFF = 15.0
    CONNECT_TIMEOUT = 3.0
    ALIVE_POLL = 0.5
    IDLE_POLL = 1.0

    def __init__(self, ctx, registry, dispatcher):
        self.ctx = ctx
        self.registry = registry
        self.dispatcher = dispatcher
        self._threads = []
        self._stop = threading.Event()

    def start(self):
        for peer in self.ctx.peers:
            if not should_connect(self.ctx.self_node.roles, peer.roles):
                logging.debug(
                    "[PEER SKIP] %s (self=%s peer=%s)",
                    peer.node_id,
                    list(self.ctx.self_node.roles),
                    list(peer.roles),
                )
                continue
            thread = threading.Thread(
                target=self._dial_loop,
                args=(peer,),
                daemon=True,
                name=f"peer-dialer-{peer.node_id}",
            )
            thread.start()
            self._threads.append(thread)

    def stop(self):
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2.0)
            if thread.is_alive():
                logging.warning("[PEER DIALER] thread %s did not exit in time", thread.name)

    def _dial_loop(self, peer):
        backoff = self.INITIAL_BACKOFF
        while not self._stop.is_set():
            if self.registry.has(peer.node_id):
                # 이미 inbound나 이전 dial로 연결이 살아 있다면 그대로 기다린다.
                self._stop.wait(self.IDLE_POLL)
                continue

            ok = self._dial_once(peer)
            if ok:
                backoff = self.INITIAL_BACKOFF
                while not self._stop.is_set() and self.registry.has(peer.node_id):
                    self._stop.wait(self.ALIVE_POLL)
            else:
                logging.debug("[PEER DIAL] %s failed, backoff %.1fs", peer.node_id, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, self.MAX_BACKOFF)

    def _dial_once(self, peer) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.CONNECT_TIMEOUT)
        try:
            sock.connect((peer.ip, peer.port))
        except OSError as exc:
            sock.close()
            logging.debug("[PEER DIAL CONNECT FAIL] %s: %s", peer.node_id, exc)
            return False

        try:
            sock.settimeout(HELLO_TIMEOUT)
            send_hello(sock, self.ctx.self_node.node_id)
            peer_id = recv_hello(sock)
            sock.settimeout(None)
        except Exception as exc:
            logging.info("[PEER DIAL HANDSHAKE FAIL] %s: %s", peer.node_id, exc)
            try:
                sock.close()
            except OSError:
                pass
            return False

        if peer_id != peer.node_id:
            logging.warning(
                "[PEER DIAL ID MISMATCH] expected=%s got=%s",
                peer.node_id,
                peer_id,
            )
            try:
                sock.close()
            except OSError:
                pass
            return False

        conn = PeerConnection(
            sock=sock,
            peer_node_id=peer_id,
            on_frame=self.dispatcher.dispatch,
            on_close=self.registry.unbind,
        )
        if not self.registry.bind(peer_id, conn):
            # 동시에 양쪽이 dial해도 먼저 bind에 성공한 연결을 그대로 사용한다.
            logging.info("[PEER DIAL LOSES RACE] %s already bound via inbound", peer_id)
            conn.close()
            return True

        conn.start()
        logging.info("[PEER DIALED] %s", peer_id)
        return True
