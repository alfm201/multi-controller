"""node_id별 활성 PeerConnection을 관리하는 레지스트리."""

import logging
import threading

from app.logging.app_logging import TAG_PEER, tag_message


class PeerRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._conns = {}  # node_id -> PeerConnection
        self._listeners = []

    # ------------------------------------------------------------
    # observers
    # ------------------------------------------------------------
    def add_listener(self, listener):
        """listener(event: 'bound'|'unbound', node_id: str)"""
        self._listeners.append(listener)

    def add_unbind_listener(self, callback):
        """unbind가 발생한 뒤 callback(node_id)를 호출한다."""
        self.add_listener(
            lambda event, node_id: callback(node_id) if event == "unbound" else None
        )

    def _notify(self, event, node_id):
        for listener in list(self._listeners):
            try:
                listener(event, node_id)
            except Exception:
                logging.exception(tag_message(TAG_PEER, "registry listener failed"))

    # ------------------------------------------------------------
    # bind / unbind
    # ------------------------------------------------------------
    def bind(self, node_id, conn, *, notify: bool = True) -> bool:
        """
        node_id에 connection을 바인딩한다.

        이미 살아 있는 connection이 있으면 False를 반환한다.
        dual-dial 상황에서는 먼저 bind에 성공한 쪽이 이긴다.
        """
        with self._lock:
            existing = self._conns.get(node_id)
            if existing is not None and not existing.closed:
                return False
            self._conns[node_id] = conn
        logging.debug(tag_message(TAG_PEER, "bound node=%s"), node_id)
        if notify:
            self._notify("bound", node_id)
        return True

    def notify_bound_ready(self, node_id, conn) -> bool:
        with self._lock:
            current = self._conns.get(node_id)
            if current is not conn or conn.closed:
                return False
        self._notify("bound", node_id)
        return True

    def unbind(self, node_id, conn) -> bool:
        with self._lock:
            if self._conns.get(node_id) is conn:
                del self._conns[node_id]
            else:
                return False
        logging.debug(tag_message(TAG_PEER, "unbound node=%s"), node_id)
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
        for conn in conns:
            conn.close()

