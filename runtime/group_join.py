"""Helpers for bootstrapping group membership from a target IP."""

from __future__ import annotations

import socket

from network.frames import decode_frame
from network.handshake import HELLO_TIMEOUT, recv_hello, send_hello
from runtime.config_loader import DEFAULT_LISTEN_PORT


def merge_group_join_nodes(
    existing_nodes: list[dict],
    *,
    requester_node_id: str,
    requester_ip: str,
    requester_port: int = DEFAULT_LISTEN_PORT,
) -> list[dict]:
    merged = []
    matched = False
    for raw_node in existing_nodes:
        if not isinstance(raw_node, dict):
            continue
        name = str(raw_node.get("name") or "").strip()
        ip = str(raw_node.get("ip") or "").strip()
        if not name or not ip:
            continue
        node = {
            "name": name,
            "ip": ip,
            "port": int(raw_node.get("port", DEFAULT_LISTEN_PORT)),
            "note": str(raw_node.get("note", "") or "").strip(),
        }
        if node["name"] == requester_node_id:
            node["ip"] = requester_ip
            node["port"] = requester_port
            matched = True
        merged.append(node)
    if not matched:
        merged.append(
            {
                "name": requester_node_id,
                "ip": requester_ip,
                "port": requester_port,
                "note": "",
            }
        )
    return merged


def build_group_join_state(
    nodes: list[dict],
    *,
    detail: str = "",
    accepted: bool = True,
) -> dict:
    return {
        "kind": "group_join_state",
        "accepted": bool(accepted),
        "detail": str(detail or ""),
        "nodes": list(nodes),
    }


def request_group_join_state(
    target_ip: str,
    requester_node_id: str,
    *,
    port: int = DEFAULT_LISTEN_PORT,
    timeout_sec: float = HELLO_TIMEOUT,
) -> dict:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_sec)
    try:
        sock.connect((target_ip, port))
        send_hello(sock, requester_node_id, bootstrap=True)
        recv_hello(sock)
        response = _recv_single_frame(sock)
    finally:
        try:
            sock.close()
        except OSError:
            pass
    if response.get("kind") != "group_join_state":
        raise ValueError(str(response.get("detail") or "그룹 정보를 받아오지 못했습니다."))
    nodes = response.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError("그룹 노드 목록 형식이 올바르지 않습니다.")
    return {
        "accepted": bool(response.get("accepted", True)),
        "detail": str(response.get("detail") or ""),
        "nodes": nodes,
    }


def _recv_single_frame(sock) -> dict:
    buffer = b""
    while b"\n" not in buffer:
        chunk = sock.recv(1024)
        if not chunk:
            raise ConnectionError("group join peer closed before sending state")
        buffer += chunk
    line, _ = buffer.split(b"\n", 1)
    return decode_frame(line)
