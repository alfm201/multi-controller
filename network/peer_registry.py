"""
PeerRegistry: node_id -> PeerConnection 을 관리한다.

핵심:
  - 한 peer 에 대해 "지금 살아있는" 연결을 0개 또는 1개만 유지한다.
  - inbound(accept) 와 outbound(dial) 가 경합하면 먼저 bind 한 쪽이 이긴다.
    진 쪽의 소켓은 바로 닫힌다. ("first to bind wins")

이렇게 해두면 InputRouter 가 target 으로 송신할 때, 그 연결이
내가 dial 해서 만든 것인지 상대가 dial 해서 accept 한 것인지를
구분할 필요 없이 그냥 PeerConnection 을 꺼내 쓰면 된다.
"""

import logging
import threading


class PeerRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._conns = {}  # node_id -> PeerConnection
        self._listeners = []

    # ------------------------------------------------------------
    # observers (로깅이나 coordinator 에서 유용)
    # ------------------------------------------------------------
    def add_listener(self, listener):
        """listener(event: 'bound'|'unbound', node_id: str)"""
        self._listeners.append(listener)

    def _notify(self, event, node_id):
        for l in list(self._listeners):
            try:
                l(event, node_id)
            except Exception:
                logging.exception("[PEER REGISTRY LISTENER ERROR]")

    # ------------------------------------------------------------
    # bind / unbind
    # ------------------------------------------------------------
    def bind(self, node_id, conn) -> bool:
        """
        conn 을 node_id 에 등록한다. 이미 살아있는 conn 이 있으면 실패(False).
        호출자는 실패 시 conn.close() 를 불러 진 쪽을 정리해야 한다.
        """
        with self._lock:
            existing = self._conns.get(node_id)
            if existing is not None and not existing.closed:
                return False
            self._conns[node_id] = conn
        logging.debug(f"[PEER BOUND] {node_id}")
        self._notify("bound", node_id)
        return True

    def unbind(self, node_id, conn) -> bool:
        with self._lock:
            if self._conns.get(node_id) is conn:
                del self._conns[node_id]
            else:
                return False
        logging.debug(f"[PEER UNBOUND] {node_id}")
        self._notify("unbound", node_id)
        return True

    # ------------------------------------------------------------
    # query
    # ------------------------------------------------------------
    def get(self, node_id):
        with self._lock:
            conn = self._conns.get(node_id)
        if conn is None or conn.closed:
            return None
        return conn

    def has(self, node_id) -> bool:
        return self.get(node_id) is not None

    def all(self):
        with self._lock:
            return list(self._conns.items())

    # ------------------------------------------------------------
    # shutdown
    # ------------------------------------------------------------
    def close_all(self):
        with self._lock:
            conns = list(self._conns.values())
        for c in conns:
            c.close()
