"""각 peer로 outbound 연결을 유지하는 dialer."""

import logging
import socket
import threading
import time

from network.handshake import HELLO_TIMEOUT, recv_hello, send_hello
from network.peer_connection import PeerConnection
from network.peer_reject import parse_peer_reject
from routing.topology import should_connect
from runtime.app_version import get_current_compatibility_version, get_current_version


class PeerDialer:
    INITIAL_BACKOFF = 1.0
    MAX_BACKOFF = 15.0
    CONNECT_TIMEOUT = 3.0
    ALIVE_POLL = 0.5
    IDLE_POLL = 1.0

    def __init__(self, ctx, registry, dispatcher, *, reject_callback=None, now_fn=None):
        self.ctx = ctx
        self.registry = registry
        self.dispatcher = dispatcher
        self.reject_callback = reject_callback
        self._now = now_fn or time.monotonic
        self._threads = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reject_until = {}
        self.dispatcher.register_control_handler("ctrl.peer_reject", self._handle_peer_reject)

    def start(self):
        self.refresh_peers()

    def stop(self):
        self._stop.set()
        for thread in list(self._threads.values()):
            thread.join(timeout=2.0)
            if thread.is_alive():
                logging.warning("[PEER DIALER] thread %s did not exit in time", thread.name)

    def refresh_peers(self):
        configured_ids = {peer.node_id for peer in self.ctx.peers}

        with self._lock:
            removed_ids = set(self._threads) - configured_ids
            for node_id in removed_ids:
                conn = self.registry.get(node_id)
                if conn is not None:
                    conn.close()
                del self._threads[node_id]
                self._reject_until.pop(node_id, None)

            for peer in self.ctx.peers:
                if peer.node_id in self._threads:
                    continue
                if not should_connect(self.ctx.self_node.roles, peer.roles):
                    logging.debug("[PEER SKIP] %s", peer.node_id)
                    continue
                thread = threading.Thread(
                    target=self._dial_loop,
                    args=(peer.node_id,),
                    daemon=True,
                    name=f"peer-dialer-{peer.node_id}",
                )
                thread.start()
                self._threads[peer.node_id] = thread

    def _dial_loop(self, peer_id):
        backoff = self.INITIAL_BACKOFF
        while not self._stop.is_set():
            peer = self.ctx.get_node(peer_id)
            if peer is None or peer.node_id == self.ctx.self_node.node_id:
                return

            reject_wait = self._reject_wait_sec(peer.node_id)
            if reject_wait > 0:
                self._stop.wait(min(reject_wait, self.IDLE_POLL))
                continue

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
            send_hello(
                sock,
                self.ctx.self_node.node_id,
                app_version=get_current_version(),
                compatibility_version=get_current_compatibility_version(),
            )
            peer_hello = recv_hello(sock)
            sock.settimeout(None)
        except Exception as exc:
            logging.info("[PEER DIAL HANDSHAKE FAIL] %s: %s", peer.node_id, exc)
            try:
                sock.close()
            except OSError:
                pass
            return False

        if peer_hello.node_id != peer.node_id:
            logging.warning(
                "[PEER DIAL ID MISMATCH] expected=%s got=%s",
                peer.node_id,
                peer_hello.node_id,
            )
            try:
                sock.close()
            except OSError:
                pass
            return False

        conn = PeerConnection(
            sock=sock,
            peer_node_id=peer_hello.node_id,
            on_frame=self.dispatcher.dispatch,
            on_close=self.registry.unbind,
            peer_app_version=peer_hello.app_version,
            peer_compatibility_version=peer_hello.compatibility_version,
        )
        if not self.registry.bind(peer_hello.node_id, conn, notify=False):
            # 동시에 양쪽이 dial해도 먼저 bind에 성공한 연결을 그대로 사용한다.
            logging.info(
                "[PEER DIAL LOSES RACE] %s already bound via inbound",
                peer_hello.node_id,
            )
            conn.close()
            return True

        conn.start()
        self.registry.notify_bound_ready(peer_hello.node_id, conn)
        logging.info("[PEER DIALED] %s", peer_hello.node_id)
        return True

    def _handle_peer_reject(self, peer_id, frame) -> None:
        try:
            reject = parse_peer_reject(frame)
        except ValueError as exc:
            logging.debug("[PEER REJECT] invalid reject frame from %s: %s", peer_id, exc)
            return
        if reject.retry_after_sec is not None:
            with self._lock:
                self._reject_until[peer_id] = max(
                    self._reject_until.get(peer_id, 0.0),
                    self._now() + reject.retry_after_sec,
                )
        logging.info(
            "[PEER REJECT] %s reason=%s detail=%s retry_after=%s",
            peer_id,
            reject.reason,
            reject.detail,
            reject.retry_after_sec,
        )
        if callable(self.reject_callback):
            try:
                self.reject_callback(peer_id, reject)
            except Exception:
                logging.exception("[PEER REJECT CALLBACK ERROR]")
        conn = self.registry.get(peer_id)
        if conn is not None:
            conn.close()

    def _reject_wait_sec(self, peer_id: str) -> float:
        with self._lock:
            reject_until = self._reject_until.get(peer_id, 0.0)
        return max(reject_until - self._now(), 0.0)

