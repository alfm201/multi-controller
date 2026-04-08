import socket
import threading
import logging

from core.events import serialize_event


class TcpEventSender:
    def __init__(self, host, port, event_queue):
        self.host = host
        self.port = port
        self.event_queue = event_queue
        self.sock = None
        self.thread = None
        self.running = False

    def start(self):
        if self.running:
            return

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, self.port))
        logging.info(f"[CONNECTED] {self.host}:{self.port}")

        self.running = True
        self.thread = threading.Thread(target=self._send_loop, daemon=True)
        self.thread.start()

    def _send_loop(self):
        while self.running:
            event = self.event_queue.get()

            try:
                payload = serialize_event(event).encode("utf-8")
                self.sock.sendall(payload)
            except Exception as e:
                logging.exception(f"[SEND ERROR] {e}")
                self.running = False
                break

            if event.get("kind") == "system":
                self.running = False
                break

    def stop(self):
        self.running = False
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass

    def join(self):
        if self.thread is not None:
            self.thread.join()
