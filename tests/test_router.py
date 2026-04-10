"""Tests for routing/router.py state transitions."""

import queue
import threading
import time

from routing.router import InputRouter
from runtime.context import NodeInfo, RuntimeContext


class RecordingConn:
    def __init__(self):
        self.frames = []
        self.closed = False

    def send_frame(self, frame):
        self.frames.append(frame)
        return True


class FakeRegistry:
    def __init__(self, conns):
        self._conns = conns

    def get(self, node_id):
        return self._conns.get(node_id)


def _ctx():
    nodes = [
        NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000}),
        NodeInfo.from_dict({"name": "B", "ip": "127.0.0.1", "port": 5001}),
        NodeInfo.from_dict({"name": "C", "ip": "127.0.0.1", "port": 5002}),
    ]
    return RuntimeContext(self_node=nodes[0], nodes=nodes)


def test_pending_target_does_not_forward():
    conn = RecordingConn()
    router = InputRouter(_ctx(), FakeRegistry({"B": conn}))
    q = queue.Queue()
    thread = threading.Thread(target=router.run, args=(q,), daemon=True)
    thread.start()
    router.set_pending_target("B")
    q.put({"kind": "key_down", "key": "a", "ts": time.time()})
    time.sleep(0.05)
    router.stop()
    q.put({"kind": "system", "message": "shutdown"})
    thread.join(timeout=1.0)
    assert conn.frames == []


def test_active_target_forwards():
    conn = RecordingConn()
    router = InputRouter(_ctx(), FakeRegistry({"B": conn}))
    q = queue.Queue()
    thread = threading.Thread(target=router.run, args=(q,), daemon=True)
    thread.start()
    router.activate_target("B")
    q.put({"kind": "key_down", "key": "a", "ts": time.time()})
    time.sleep(0.05)
    router.stop()
    q.put({"kind": "system", "message": "shutdown"})
    thread.join(timeout=1.0)
    assert conn.frames[0]["kind"] == "key_down"


def test_switch_to_pending_releases_pressed_keys_from_old_target():
    old_conn = RecordingConn()
    router = InputRouter(_ctx(), FakeRegistry({"B": old_conn}))
    q = queue.Queue()
    thread = threading.Thread(target=router.run, args=(q,), daemon=True)
    thread.start()
    router.activate_target("B")
    q.put({"kind": "key_down", "key": "a", "ts": time.time()})
    time.sleep(0.05)
    router.set_pending_target("C")
    router.stop()
    q.put({"kind": "system", "message": "shutdown"})
    thread.join(timeout=1.0)
    assert any(frame["kind"] == "key_up" and frame["key"] == "a" for frame in old_conn.frames)
