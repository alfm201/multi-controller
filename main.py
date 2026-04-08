import argparse
import json
import logging
import queue
import socket
import threading
import time
from pathlib import Path

from utils.logger_setup import setup_logging
from capture.input_capture import InputCapture
from network.tcp_sender import TcpEventSender
from network.tcp_receiver import TcpEventReceiver


CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--node-name",
        help="같은 PC 테스트용 self override. config.json의 nodes[].name과 일치해야 함",
    )
    parser.add_argument(
        "--config",
        default=str(CONFIG_PATH),
        help="config.json 경로",
    )
    return parser.parse_args()


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        config = json.load(f)

    validate_config(config)
    return config


def validate_config(config):
    if "nodes" not in config:
        raise ValueError("config.json에 'nodes' 항목이 없습니다.")

    nodes = config["nodes"]
    if not isinstance(nodes, list):
        raise ValueError("'nodes'는 리스트여야 합니다.")

    if not nodes:
        raise ValueError("'nodes'가 비어 있습니다.")

    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise ValueError(f"'nodes[{i}]'는 객체여야 합니다.")
        if "ip" not in node:
            raise ValueError(f"'nodes[{i}].ip'가 없습니다.")
        if "port" not in node:
            raise ValueError(f"'nodes[{i}].port'가 없습니다.")


def get_hostname():
    return socket.gethostname()


def get_local_ips():
    ips = set()

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM):
            ip = info[4][0]
            if ip:
                ips.add(ip)
    except socket.gaierror:
        pass

    for probe_ip in ("8.8.8.8", "1.1.1.1"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((probe_ip, 80))
            ips.add(s.getsockname()[0])
        except OSError:
            pass
        finally:
            s.close()

    ips.add("127.0.0.1")
    return ips


def node_label(node):
    name = node.get("name")
    if name:
        return f"{name}({node['ip']}:{node['port']})"
    return f"{node['ip']}:{node['port']}"


def is_same_node(a, b):
    return a.get("ip") == b.get("ip") and int(a.get("port")) == int(b.get("port"))


def detect_self_node(nodes, override_name=None):
    if override_name:
        matches = [
            node for node in nodes
            if node.get("name") and node.get("name") == override_name
        ]
        if not matches:
            raise RuntimeError(
                f"--node-name={override_name} 와 일치하는 nodes 항목이 없습니다."
            )
        if len(matches) > 1:
            labels = ", ".join(node_label(n) for n in matches)
            raise RuntimeError(
                f"--node-name={override_name} 와 일치하는 항목이 여러 개입니다: {labels}"
            )
        self_node = matches[0]
        logging.info(f"[SELF DETECTED BY OVERRIDE] {node_label(self_node)}")
        return self_node

    hostname = get_hostname()
    local_ips = get_local_ips()

    logging.info(f"[AUTO DETECT] hostname={hostname}")
    logging.info(f"[AUTO DETECT] local_ips={sorted(local_ips)}")

    ip_matches = [node for node in nodes if node["ip"] in local_ips]

    if not ip_matches:
        raise RuntimeError(
            "config.json의 nodes 중 현재 PC의 IP와 일치하는 항목을 찾지 못했습니다. "
            "같은 PC 테스트라면 --node-name 옵션을 사용하세요."
        )

    if len(ip_matches) == 1:
        self_node = ip_matches[0]
        logging.info(f"[SELF DETECTED] {node_label(self_node)}")
        return self_node

    hostname_matches = [
        node for node in ip_matches
        if node.get("name") and node.get("name").lower() == hostname.lower()
    ]

    if len(hostname_matches) == 1:
        self_node = hostname_matches[0]
        logging.info(f"[SELF DETECTED BY HOSTNAME] {node_label(self_node)}")
        return self_node

    labels = ", ".join(node_label(n) for n in ip_matches)
    raise RuntimeError(
        "현재 PC에 매칭되는 node가 여러 개입니다. "
        f"중복 후보: {labels}. "
        "같은 PC 테스트라면 --node-name 옵션을 사용하세요."
    )


def get_peer_nodes(nodes, self_node):
    return [node for node in nodes if not is_same_node(node, self_node)]


def start_receiver(self_node):
    receiver = TcpEventReceiver(
        host=self_node["ip"],
        port=int(self_node["port"]),
    )

    receiver_thread = threading.Thread(
        target=receiver.serve_forever,
        daemon=True,
    )
    receiver_thread.start()

    logging.info(f"[RECEIVER START] {node_label(self_node)}")
    return receiver, receiver_thread


class SenderWorker:
    """
    peer와의 연결은 선택적이다.
    - 연결 실패해도 프로그램 전체는 계속 동작
    - 연결이 없으면 이벤트는 드롭
    - 재시도는 지수 백오프로 천천히
    """

    def __init__(self, peer):
        self.peer = peer
        self.queue = queue.Queue()
        self.thread = None
        self.stop_event = threading.Event()
        self.sender = None
        self.connected = False
        self.retry_delay = 3.0
        self.max_retry_delay = 30.0
        self._last_state = None

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def log_state_once(self, state, message):
        if self._last_state != state:
            logging.info(message)
            self._last_state = state

    def connect_sender(self):
        sender = TcpEventSender(
            host=self.peer["ip"],
            port=int(self.peer["port"]),
            event_queue=self.queue,
        )
        sender.start()
        return sender

    def run(self):
        while not self.stop_event.is_set():
            if self.sender is None:
                try:
                    self.sender = self.connect_sender()
                    self.connected = True
                    self.retry_delay = 3.0
                    self.log_state_once(
                        "connected",
                        f"[PEER CONNECTED] -> {node_label(self.peer)}"
                    )
                except Exception as e:
                    self.connected = False
                    self.sender = None
                    self.log_state_once(
                        "disconnected",
                        f"[PEER DISCONNECTED] -> {node_label(self.peer)} "
                        f"(retry in {self.retry_delay:.0f}s, reason={e})"
                    )
                    time.sleep(self.retry_delay)
                    self.retry_delay = min(self.retry_delay * 2, self.max_retry_delay)
                    continue

            while (
                not self.stop_event.is_set()
                and self.sender is not None
                and getattr(self.sender, "running", False)
            ):
                time.sleep(0.5)

            if self.stop_event.is_set():
                break

            if self.sender is not None:
                try:
                    self.sender.stop()
                except Exception:
                    pass

            self.sender = None
            self.connected = False
            self.log_state_once(
                "disconnected",
                f"[PEER DISCONNECTED] -> {node_label(self.peer)} "
                f"(retry in {self.retry_delay:.0f}s)"
            )
            time.sleep(self.retry_delay)
            self.retry_delay = min(self.retry_delay * 2, self.max_retry_delay)

    def send_event(self, event):
        if self.connected:
            self.queue.put(event)

    def stop(self):
        self.stop_event.set()
        self.queue.put({"kind": "system", "message": "shutdown"})

        if self.sender is not None:
            try:
                self.sender.stop()
            except Exception:
                pass

    def join(self):
        if self.thread is not None:
            self.thread.join()


def fanout_loop(source_queue, sender_workers, stop_event):
    while not stop_event.is_set():
        event = source_queue.get()

        for worker in sender_workers:
            worker.send_event(event)

        if event.get("kind") == "system":
            break


def main():
    args = parse_args()
    setup_logging()

    config = load_config(args.config)
    nodes = config["nodes"]

    self_node = detect_self_node(nodes, override_name=args.node_name)
    peers = get_peer_nodes(nodes, self_node)

    logging.info(f"[SELF] {node_label(self_node)}")

    if not peers:
        logging.warning("[PEERS] 연결 대상이 없습니다. 수신만 동작합니다.")
    else:
        for peer in peers:
            logging.info(f"[PEER] {node_label(peer)}")

    capture_queue = queue.Queue()
    stop_event = threading.Event()

    capture = InputCapture(capture_queue)
    receiver, receiver_thread = start_receiver(self_node)

    sender_workers = []
    for peer in peers:
        worker = SenderWorker(peer)
        worker.start()
        sender_workers.append(worker)

    fanout_thread = threading.Thread(
        target=fanout_loop,
        args=(capture_queue, sender_workers, stop_event),
        daemon=True,
    )
    fanout_thread.start()

    try:
        capture.start()
        capture.join()
    except KeyboardInterrupt:
        logging.info("[INTERRUPTED] Ctrl+C")
    finally:
        stop_event.set()
        capture.stop()

        for worker in sender_workers:
            worker.send_event({"kind": "system", "message": "shutdown"})
            worker.stop()

        for worker in sender_workers:
            worker.join()

        logging.info("[EXIT] main 종료")


if __name__ == "__main__":
    main()
