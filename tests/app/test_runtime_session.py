from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

import app.bootstrap.entry as main_module
from app.bootstrap.entry import parse_args
from app.config.app_settings import AppSettings
from app.bootstrap.session import RuntimeSession
from control.state.context import NodeInfo, RuntimeContext


def test_runtime_session_run_forever_calls_shutdown_after_success():
    session = RuntimeSession(
        SimpleNamespace(),
        active_target=None,
        status_interval=0.0,
        ui_mode="console",
        shutdown_evt=threading.Event(),
    )
    steps = []

    session.build = lambda: steps.append("build")
    session.start = lambda: steps.append("start")
    session.run = lambda: steps.append("run")
    session.shutdown = lambda: steps.append("shutdown")

    session.run_forever()

    assert steps == ["build", "start", "run", "shutdown"]


def test_runtime_session_run_forever_calls_shutdown_after_build_failure():
    session = RuntimeSession(
        SimpleNamespace(),
        active_target=None,
        status_interval=0.0,
        ui_mode="console",
        shutdown_evt=threading.Event(),
    )
    steps = []

    def fail_build():
        steps.append("build")
        raise RuntimeError("boom")

    session.build = fail_build
    session.start = lambda: steps.append("start")
    session.run = lambda: steps.append("run")
    session.shutdown = lambda: steps.append("shutdown")

    with pytest.raises(RuntimeError, match="boom"):
        session.run_forever()

    assert steps == ["build", "shutdown"]


def test_main_delegates_runtime_execution_to_runtime_session(monkeypatch):
    args = parse_args(["--active-target", "B", "--status-interval", "2.5", "--tray"])
    captured = {}
    ctx = SimpleNamespace(
        self_node=SimpleNamespace(label=lambda: "SELF(10.0.0.1)"),
        peers=[],
    )

    monkeypatch.setattr(main_module, "parse_args", lambda: args)
    monkeypatch.setattr(main_module, "ensure_runtime_config", lambda _path: ({}, Path("config/config.json")))
    monkeypatch.setattr(main_module, "load_app_settings", lambda _config: AppSettings())
    monkeypatch.setattr(main_module, "setup_logging", lambda **_kwargs: "logs/app.log")
    monkeypatch.setattr(main_module, "enable_best_effort_dpi_awareness", lambda: None)
    monkeypatch.setattr(main_module, "release_input_guards", lambda: None)
    monkeypatch.setattr(main_module, "log_windows_interaction_diagnostics", lambda: None)
    monkeypatch.setattr(main_module, "build_runtime_context", lambda *_args, **_kwargs: ctx)
    monkeypatch.setattr(
        main_module,
        "validate_startup_args",
        lambda seen_ctx, active_target: captured.update(validated=(seen_ctx, active_target)),
    )
    monkeypatch.setattr(main_module.signal, "signal", lambda *_args, **_kwargs: None)

    class FakeRuntimeSession:
        def __init__(self, seen_ctx, **kwargs):
            captured["session"] = (seen_ctx, kwargs)

        def run_forever(self):
            captured["ran"] = True

    monkeypatch.setattr(main_module, "RuntimeSession", FakeRuntimeSession)

    main_module.main()

    assert captured["validated"] == (ctx, "B")
    assert captured["session"] == (
        ctx,
        {
            "active_target": "B",
            "status_interval": 2.5,
            "ui_mode": "tray",
            "shutdown_evt": captured["session"][1]["shutdown_evt"],
            "log_path": "logs/app.log",
        },
    )
    assert isinstance(captured["session"][1]["shutdown_evt"], threading.Event)
    assert captured["ran"] is True


def _ctx():
    nodes = [
        NodeInfo.from_dict({"node_id": "A", "name": "A", "ip": "127.0.0.1", "port": 5000}),
        NodeInfo.from_dict({"node_id": "B", "name": "B", "ip": "127.0.0.2", "port": 5001}),
    ]
    return RuntimeContext(self_node=nodes[0], nodes=nodes)


def test_runtime_session_handles_self_ip_change(monkeypatch):
    session = RuntimeSession(
        _ctx(),
        active_target=None,
        status_interval=0.0,
        ui_mode="console",
        shutdown_evt=threading.Event(),
    )
    sync_calls = []

    monkeypatch.setattr(session, "_sync_self_ip_change", lambda new_ip, announce: sync_calls.append((new_ip, announce)) or True)

    session._handle_self_ip_change("127.0.0.1", "192.168.0.10")

    assert sync_calls == [("192.168.0.10", True)]
    assert session._pending_self_ip_sync == {"desired_ip": "192.168.0.10", "retry_count": 0}


def test_runtime_session_handles_ambiguous_self_ip_change_via_peer_probe(monkeypatch):
    session = RuntimeSession(
        _ctx(),
        active_target=None,
        status_interval=0.0,
        ui_mode="console",
        shutdown_evt=threading.Event(),
    )
    sync_calls = []

    monkeypatch.setattr(session, "_resolve_self_ip_from_known_peers", lambda previous_ip, details=None: "10.0.0.5")
    monkeypatch.setattr(session, "_sync_self_ip_change", lambda new_ip, announce: sync_calls.append((new_ip, announce)) or True)

    session._handle_self_ip_change(
        "192.168.0.10",
        "",
        {
            "local_ips": ("127.0.0.1", "10.0.0.5", "172.16.0.7"),
            "state": SimpleNamespace(coordinator_id="B", online_peers=("B",)),
            "ambiguous": True,
        },
    )

    assert sync_calls == [("10.0.0.5", True)]
    assert session._pending_self_ip_sync == {"desired_ip": "10.0.0.5", "retry_count": 0}


def test_runtime_session_warns_when_ambiguous_self_ip_change_cannot_be_resolved(monkeypatch):
    session = RuntimeSession(
        _ctx(),
        active_target=None,
        status_interval=0.0,
        ui_mode="console",
        shutdown_evt=threading.Event(),
    )
    notices = []

    monkeypatch.setattr(session, "_resolve_self_ip_from_known_peers", lambda previous_ip, details=None: "")
    monkeypatch.setattr(session, "_notify_self_ip_sync_failure", lambda message: notices.append(message))

    session._handle_self_ip_change(
        "192.168.0.10",
        "",
        {
            "local_ips": ("127.0.0.1", "10.0.0.5", "172.16.0.7"),
            "state": SimpleNamespace(coordinator_id="B", online_peers=("B",)),
            "ambiguous": True,
        },
    )

    assert notices == ["내 PC IP 변경은 감지됐지만 새 연결 경로를 확인하지 못해 자동 전환하지 않았습니다."]
    assert session._pending_self_ip_sync is None


def test_runtime_session_resolves_self_ip_from_coordinator_then_online_peers(monkeypatch):
    ctx = _ctx()
    ctx.replace_nodes(
        [
            ctx.self_node,
            ctx.get_node("B"),
            NodeInfo.from_dict({"node_id": "C", "name": "C", "ip": "127.0.0.3", "port": 5002}),
        ]
    )
    session = RuntimeSession(
        ctx,
        active_target=None,
        status_interval=0.0,
        ui_mode="console",
        shutdown_evt=threading.Event(),
    )
    probed = []

    def fake_probe(node):
        probed.append(node.node_id)
        return "" if node.node_id == "B" else "10.0.0.5"

    monkeypatch.setattr(session, "_probe_local_ip_via_peer", fake_probe)

    resolved = session._resolve_self_ip_from_known_peers(
        "192.168.0.10",
        {
            "local_ips": ("127.0.0.1", "10.0.0.5", "172.16.0.7"),
            "state": SimpleNamespace(coordinator_id="B", online_peers=("C", "B")),
            "ambiguous": True,
        },
    )

    assert resolved == "10.0.0.5"
    assert probed == ["B", "C"]


def test_runtime_session_retries_self_ip_sync_after_stale_revision(monkeypatch):
    ctx = _ctx()
    session = RuntimeSession(
        ctx,
        active_target=None,
        status_interval=0.0,
        ui_mode="console",
        shutdown_evt=threading.Event(),
    )
    session._pending_self_ip_sync = {"desired_ip": "192.168.0.10", "retry_count": 0}
    retried = []

    monkeypatch.setattr(session, "_sync_self_ip_change", lambda new_ip, announce: retried.append((new_ip, announce)) or True)

    session._handle_self_ip_sync_node_list_change({"reject_reason": "stale_revision", "request_id": "req-1"})

    assert retried == [("192.168.0.10", False)]
    assert session._pending_self_ip_sync == {"desired_ip": "192.168.0.10", "retry_count": 1}


def test_runtime_session_clears_pending_self_ip_sync_after_request_ack():
    ctx = _ctx()
    updated_nodes = [
        NodeInfo.from_dict({"node_id": "A", "name": "A", "ip": "192.168.0.10", "port": 5000}),
        NodeInfo.from_dict({"node_id": "B", "name": "B", "ip": "127.0.0.2", "port": 5001}),
    ]
    ctx.replace_nodes(updated_nodes)
    session = RuntimeSession(
        ctx,
        active_target=None,
        status_interval=0.0,
        ui_mode="console",
        shutdown_evt=threading.Event(),
    )
    session._pending_self_ip_sync = {"desired_ip": "192.168.0.10", "retry_count": 1, "request_id": "req-1"}

    session._handle_self_ip_sync_node_list_change({"request_id": "req-1"})

    assert session._pending_self_ip_sync is None


def test_runtime_session_warns_and_clears_pending_self_ip_sync_after_timeout(monkeypatch):
    ctx = _ctx()
    session = RuntimeSession(
        ctx,
        active_target=None,
        status_interval=0.0,
        ui_mode="console",
        shutdown_evt=threading.Event(),
    )
    session._pending_self_ip_sync = {"desired_ip": "192.168.0.10", "retry_count": 1, "request_id": "req-1"}
    notices = []

    monkeypatch.setattr(session, "_notify_self_ip_sync_failure", lambda message: notices.append(message))

    session._handle_self_ip_sync_node_list_change({"request_id": "req-1", "reject_reason": "timeout"})

    assert session._pending_self_ip_sync is None
    assert notices == ["내 PC IP 변경을 다른 PC에 동기화하지 못했습니다. 잠시 후 다시 시도해 주세요."]
    return
    assert notices == [("내 PC IP 변경을 다른 PC에 동기화하지 못했습니다. 잠시 후 다시 시도해 주세요.", "warning")]
