import json
import time


def now_ts():
    return time.time()


def normalize_key(key):
    try:
        return key.char
    except AttributeError:
        return str(key)


def make_key_down_event(key):
    return {
        "ts": now_ts(),
        "kind": "key_down",
        "key": normalize_key(key),
    }


def make_key_up_event(key):
    return {
        "ts": now_ts(),
        "kind": "key_up",
        "key": normalize_key(key),
    }


def make_mouse_move_event(x, y):
    return {
        "ts": now_ts(),
        "kind": "mouse_move",
        "x": x,
        "y": y,
    }


def make_mouse_button_event(x, y, button, pressed):
    return {
        "ts": now_ts(),
        "kind": "mouse_button",
        "x": x,
        "y": y,
        "button": str(button),
        "pressed": pressed,
    }


def make_mouse_wheel_event(x, y, dx, dy):
    return {
        "ts": now_ts(),
        "kind": "mouse_wheel",
        "x": x,
        "y": y,
        "dx": dx,
        "dy": dy,
    }


def make_system_event(message):
    return {
        "ts": now_ts(),
        "kind": "system",
        "message": message,
    }


def serialize_event(event):
    return json.dumps(event, ensure_ascii=False) + "\n"


def deserialize_event(line):
    return json.loads(line)
