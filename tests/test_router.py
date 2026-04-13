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


def test_active_target_forwards_mouse_move_as_relative_delta():
    conn = RecordingConn()
    router = InputRouter(_ctx(), FakeRegistry({"B": conn}))
    q = queue.Queue()
    thread = threading.Thread(target=router.run, args=(q,), daemon=True)
    thread.start()
    router.activate_target("B")
    q.put({"kind": "mouse_move", "x": 100, "y": 200, "ts": time.time()})
    q.put({"kind": "mouse_move", "x": 112, "y": 193, "ts": time.time()})
    time.sleep(0.05)
    router.stop()
    q.put({"kind": "system", "message": "shutdown"})
    thread.join(timeout=1.0)
    assert conn.frames == [
        {
            "kind": "mouse_move",
            "ts": conn.frames[0]["ts"],
            "relative": True,
            "dx": 12,
            "dy": -7,
        }
    ]


def test_active_target_strips_absolute_pointer_fields_from_mouse_button():
    conn = RecordingConn()
    router = InputRouter(_ctx(), FakeRegistry({"B": conn}))
    q = queue.Queue()
    thread = threading.Thread(target=router.run, args=(q,), daemon=True)
    thread.start()
    router.activate_target("B")
    q.put(
        {
            "kind": "mouse_button",
            "button": "Button.left",
            "pressed": True,
            "x": 100,
            "y": 200,
            "x_norm": 0.5,
            "y_norm": 0.6,
            "ts": time.time(),
        }
    )
    time.sleep(0.05)
    router.stop()
    q.put({"kind": "system", "message": "shutdown"})
    thread.join(timeout=1.0)
    assert conn.frames[0]["kind"] == "mouse_button"
    assert "x" not in conn.frames[0]
    assert "y" not in conn.frames[0]
    assert "x_norm" not in conn.frames[0]
    assert "y_norm" not in conn.frames[0]


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


def test_switch_to_pending_preserves_mouse_button_for_next_target_handoff():
    old_conn = RecordingConn()
    new_conn = RecordingConn()
    registry = FakeRegistry({"B": old_conn, "C": new_conn})
    router = InputRouter(_ctx(), registry)
    q = queue.Queue()
    thread = threading.Thread(target=router.run, args=(q,), daemon=True)
    thread.start()
    router.activate_target("B")
    q.put(
        {
            "kind": "mouse_button",
            "button": "Button.left",
            "pressed": True,
            "x": 100,
            "y": 200,
            "x_norm": 0.8,
            "y_norm": 0.4,
            "ts": time.time(),
        }
    )
    time.sleep(0.05)
    router.prepare_pointer_handoff({"kind": "mouse_move", "x_norm": 0.2, "y_norm": 0.6})
    router.set_pending_target("C")
    router.activate_target("C")
    time.sleep(0.05)
    router.stop()
    q.put({"kind": "system", "message": "shutdown"})
    thread.join(timeout=1.0)

    assert any(
        frame["kind"] == "mouse_button"
        and frame["button"] == "Button.left"
        and frame["pressed"] is False
        for frame in old_conn.frames
    )
    assert new_conn.frames[0]["kind"] == "mouse_move"
    assert new_conn.frames[0]["x_norm"] == 0.2
    assert new_conn.frames[0]["y_norm"] == 0.6
    assert new_conn.frames[1]["kind"] == "mouse_button"
    assert new_conn.frames[1]["button"] == "Button.left"
    assert new_conn.frames[1]["pressed"] is True
    assert new_conn.frames[1]["x_norm"] == 0.2
    assert new_conn.frames[1]["y_norm"] == 0.6


def test_handoff_sends_pointer_move_even_without_held_mouse_buttons():
    new_conn = RecordingConn()
    router = InputRouter(_ctx(), FakeRegistry({"C": new_conn}))

    router.prepare_pointer_handoff(
        {"kind": "mouse_move", "x": 320, "y": 240, "x_norm": 0.25, "y_norm": 0.5}
    )
    router.set_pending_target("C")
    router.activate_target("C")

    assert new_conn.frames == [
        {
            "kind": "mouse_move",
            "ts": new_conn.frames[0]["ts"],
            "x": 320,
            "y": 240,
            "x_norm": 0.25,
            "y_norm": 0.5,
        }
    ]


def test_has_pressed_mouse_buttons_tracks_held_state():
    conn = RecordingConn()
    router = InputRouter(_ctx(), FakeRegistry({"B": conn}))
    q = queue.Queue()
    thread = threading.Thread(target=router.run, args=(q,), daemon=True)
    thread.start()
    q.put(
        {
            "kind": "mouse_button",
            "button": "Button.left",
            "pressed": True,
            "x": 1,
            "y": 2,
            "ts": time.time(),
        }
    )
    time.sleep(0.05)
    assert router.has_pressed_mouse_buttons() is True
    q.put(
        {
            "kind": "mouse_button",
            "button": "Button.left",
            "pressed": False,
            "x": 1,
            "y": 2,
            "ts": time.time(),
        }
    )
    time.sleep(0.05)
    router.stop()
    q.put({"kind": "system", "message": "shutdown"})
    thread.join(timeout=1.0)
    assert router.has_pressed_mouse_buttons() is False


def test_event_processor_may_drop_event_before_forwarding():
    conn = RecordingConn()
    router = InputRouter(
        _ctx(),
        FakeRegistry({"B": conn}),
        event_processors=[lambda event: None if event["kind"] == "mouse_move" else event],
    )
    q = queue.Queue()
    thread = threading.Thread(target=router.run, args=(q,), daemon=True)
    thread.start()
    router.activate_target("B")
    q.put({"kind": "mouse_move", "x": 10, "y": 20, "ts": time.time()})
    time.sleep(0.05)
    router.stop()
    q.put({"kind": "system", "message": "shutdown"})
    thread.join(timeout=1.0)
    assert conn.frames == []
