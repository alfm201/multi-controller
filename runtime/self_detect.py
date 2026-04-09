"""config.nodes 중에서 현재 프로세스에 해당하는 항목을 찾는다."""

import logging
import socket


def get_local_ips():
    """현재 머신에서 확인 가능한 IPv4 주소 목록을 수집한다."""
    ips = {"127.0.0.1"}
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(
            hostname, None, socket.AF_INET, socket.SOCK_STREAM
        ):
            ip = info[4][0]
            if ip:
                ips.add(ip)
    except socket.gaierror:
        pass
    return ips


def _label(node):
    return f"{node['name']}({node['ip']}:{node['port']})"


def detect_self_node(nodes, override_name=None):
    """
    현재 프로세스가 어떤 node 항목에 해당하는지 찾는다.

    - `--node-name`이 주어지면 그 값을 우선한다.
    - 아니면 로컬 IP와 hostname을 이용해 자동 탐지한다.
    """
    if override_name:
        matches = [node for node in nodes if node.get("name") == override_name]
        if not matches:
            raise RuntimeError(
                f"--node-name={override_name} 와 일치하는 nodes 항목이 없습니다."
            )
        if len(matches) > 1:
            labels = ", ".join(_label(node) for node in matches)
            raise RuntimeError(
                f"--node-name={override_name} 와 일치하는 항목이 여러 개입니다: {labels}"
            )
        logging.info("[SELF] override -> %s", _label(matches[0]))
        return matches[0]

    hostname = socket.gethostname()
    local_ips = get_local_ips()
    logging.info("[SELF] hostname=%s local_ips=%s", hostname, sorted(local_ips))

    ip_matches = [node for node in nodes if node.get("ip") in local_ips]
    if not ip_matches:
        raise RuntimeError(
            "config.nodes 중 현재 PC의 IP와 일치하는 항목이 없습니다. "
            "같은 PC에서 여러 인스턴스를 테스트한다면 --node-name을 사용하세요."
        )
    if len(ip_matches) == 1:
        logging.info("[SELF] auto -> %s", _label(ip_matches[0]))
        return ip_matches[0]

    host_matches = [
        node
        for node in ip_matches
        if node.get("name") and node["name"].lower() == hostname.lower()
    ]
    if len(host_matches) == 1:
        logging.info("[SELF] auto+hostname -> %s", _label(host_matches[0]))
        return host_matches[0]

    labels = ", ".join(_label(node) for node in ip_matches)
    raise RuntimeError(
        f"여러 nodes가 현재 PC와 매칭됩니다: {labels}. "
        "같은 PC 테스트라면 --node-name을 지정하세요."
    )
