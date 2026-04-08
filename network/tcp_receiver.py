import socket
import logging

from core.events import deserialize_event


def print_event(event):
    kind = event.get("kind")

    if kind == "key_down":
        logging.info(f"[REMOTE KEY DOWN ] {event.get('key')}")
    elif kind == "key_up":
        logging.info(f"[REMOTE KEY UP   ] {event.get('key')}")
    elif kind == "mouse_move":
        logging.info(f"[REMOTE MOVE     ] x={event.get('x')} y={event.get('y')}")
    elif kind == "mouse_button":
        state = "DOWN" if event.get("pressed") else "UP"
        logging.info(
            f"[REMOTE CLICK    ] {event.get('button')} {state} "
            f"x={event.get('x')} y={event.get('y')}"
        )
    elif kind == "mouse_wheel":
        logging.info(
            f"[REMOTE WHEEL    ] x={event.get('x')} y={event.get('y')} "
            f"dx={event.get('dx')} dy={event.get('dy')}"
        )
    elif kind == "system":
        logging.info(f"[REMOTE SYSTEM   ] {event.get('message')}")
    else:
        logging.info(f"[REMOTE UNKNOWN  ] {event}")


class TcpEventReceiver:
    def __init__(self, host="0.0.0.0", port=5000):
        self.host = host
        self.port = port

    def serve_forever(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen(1)

            logging.info(f"[LISTENING] {self.host}:{self.port}")

            while True:
                conn, addr = server.accept()
                logging.info(f"[CONNECTED] {addr}")
                self._handle_client(conn, addr)

    def _handle_client(self, conn, addr):
        buffer = ""

        try:
            with conn:
                while True:
                    data = conn.recv(4096)
                    if not data:
                        logging.info(f"[DISCONNECTED] {addr}")
                        break

                    buffer += data.decode("utf-8")

                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)

                        if not line.strip():
                            continue

                        try:
                            event = deserialize_event(line)
                            print_event(event)
                        except Exception:
                            logging.warning(f"[BAD EVENT] {line}")
        except Exception as e:
            logging.exception(f"[RECEIVE ERROR] {e}")
