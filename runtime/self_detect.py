"""
현재 PC 가 config.nodes 중 어느 항목에 해당하는지 판별한다.

외부 probe(8.8.8.8 / 1.1.1.1 UDP connect) 는 사용하지 않고
getaddrinfo 만 사용한다. 네트워크 단절 환경에서도 부작용이 없어야 한다.

같은 PC 에서 다중 인스턴스를 테스트하는 경우 --node-name override 를
지원한다. override 가 지정되면 auto detect 는 건너뛴다.
"""

import logging
import socket


def get_local_ips():
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
    if override_name:
        matches = [n for n in nodes if n.get("name") == override_name]
        if not matches:
            raise RuntimeError(
                f"--node-name={override_name} 와 일치하는 nodes 항목이 없습니다."
            )
        if len(matches) > 1:
            labels = ", ".join(_label(n) for n in matches)
            raise RuntimeError(
                f"--node-name={override_name} 와 일치하는 항목이 여러 개입니다: {labels}"
            )
        logging.info(f"[SELF] override -> {_label(matches[0])}")
        return matches[0]

    hostname = socket.gethostname()
    local_ips = get_local_ips()
    logging.info(f"[SELF] hostname={hostname} local_ips={sorted(local_ips)}")

    ip_matches = [n for n in nodes if n.get("ip") in local_ips]
    if not ip_matches:
        raise RuntimeError(
            "config.nodes 중 현재 PC 의 IP 와 일치하는 항목이 없습니다. "
            "같은 PC 테스트라면 --node-name 을 사용하세요."
        )
    if len(ip_matches) == 1:
        logging.info(f"[SELF] auto -> {_label(ip_matches[0])}")
        return ip_matches[0]

    host_matches = [
        n for n in ip_matches
        if n.get("name") and n["name"].lower() == hostname.lower()
    ]
    if len(host_matches) == 1:
        logging.info(f"[SELF] auto+hostname -> {_label(host_matches[0])}")
        return host_matches[0]

    labels = ", ".join(_label(n) for n in ip_matches)
    raise RuntimeError(
        f"여러 nodes 가 현재 PC 에 매칭됩니다: {labels}. "
        "같은 PC 테스트라면 --node-name 을 사용하세요."
    )
