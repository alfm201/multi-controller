"""
PeerDialer: 설정된 peer 각각에 대해 1개의 dial 루프 스레드를 돌린다.

동작:
  - PeerRegistry 에 해당 peer_id 의 살아있는 conn 이 있으면 아무것도 하지 않는다.
    (내가 accept 한 소켓일 수도 있고, 내가 이전에 dial 해서 붙인 소켓일 수도 있다.
     어느 쪽이든 재사용한다. 이 체크 덕분에 이전의 비대칭 연결 지연이 사라진다.)
  - 없으면 dial -> HELLO 교환 -> PeerConnection 생성 -> registry.bind.
  - 실패하면 지수 백오프 (1 -> 2 -> 4 ... 최대 15초).
  - 성공하면 그 연결이 죽을 때까지 대기한 뒤 다시 루프.

주의:
  - dial 에 성공했지만 registry.bind 에서 지면 (이미 inbound 가 먼저 bind) 진 쪽
    소켓은 버리고, registry 의 기존 conn 이 죽을 때까지 대기한다.
  - 이 클래스는 "SenderWorker" 의 자리에 해당하지만, 더 이상 "queue 를 통한 일방
    송신 채널" 이 아니다. 단순히 peer connection 을 만들어 registry 에 넣는
    역할만 한다. 실제 이벤트 송신은 InputRouter 가 registry 를 조회해서 수행한다.
"""

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
                    f"[PEER SKIP] {peer.node_id} "
                    f"(self={list(self.ctx.self_node.roles)} peer={list(peer.roles)})"
                )
                continue
            t = threading.Thread(
                target=self._dial_loop,
                args=(peer,),
                daemon=True,
                name=f"peer-dialer-{peer.node_id}",
            )
            t.start()
            self._threads.append(t)

    def stop(self):
        self._stop.set()

    # ------------------------------------------------------------
    def _dial_loop(self, peer):
        backoff = self.INITIAL_BACKOFF
        while not self._stop.is_set():
            if self.registry.has(peer.node_id):
                # inbound 가 이미 붙어있거나 이전 dial 이 살아있음 -> 건드리지 않음
                self._stop.wait(self.IDLE_POLL)
                continue

            ok = self._dial_once(peer)
            if ok:
                backoff = self.INITIAL_BACKOFF
                # 연결이 죽을 때까지 대기 -> 죽으면 루프 처음으로
                while not self._stop.is_set() and self.registry.has(peer.node_id):
                    self._stop.wait(self.ALIVE_POLL)
            else:
                logging.debug(
                    f"[PEER DIAL] {peer.node_id} failed, backoff {backoff:.1f}s"
                )
                self._stop.wait(backoff)
                backoff = min(backoff * 2, self.MAX_BACKOFF)

    def _dial_once(self, peer) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.CONNECT_TIMEOUT)
        try:
            sock.connect((peer.ip, peer.port))
        except OSError as e:
            sock.close()
            logging.debug(f"[PEER DIAL CONNECT FAIL] {peer.node_id}: {e}")
            return False

        try:
            sock.settimeout(HELLO_TIMEOUT)
            send_hello(sock, self.ctx.self_node.node_id)
            peer_id = recv_hello(sock)
            sock.settimeout(None)
        except Exception as e:
            logging.info(f"[PEER DIAL HANDSHAKE FAIL] {peer.node_id}: {e}")
            try:
                sock.close()
            except OSError:
                pass
            return False

        if peer_id != peer.node_id:
            logging.warning(
                f"[PEER DIAL ID MISMATCH] expected={peer.node_id} got={peer_id}"
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
            # 경쟁에서 짐: 이미 inbound 가 bound 되어 있음 -> 진 쪽 소켓 폐기
            logging.info(
                f"[PEER DIAL LOSES RACE] {peer_id} already bound via inbound"
            )
            conn.close()
            # registry 쪽 conn 이 살아있으니 True 로 취급해 alive wait 루프로 진입
            return True

        conn.start()
        logging.info(f"[PEER DIALED] {peer_id}")
        return True
