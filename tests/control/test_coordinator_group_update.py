from control.coordination.client import CoordinatorClient
from control.coordination.protocol import (
    make_update_check_request,
    make_update_check_result,
    make_update_download_request,
    make_update_download_result,
)
from control.coordination.service import CoordinatorService
from control.state.context import NodeInfo, RuntimeContext
from model.display.layouts import build_layout_config
from transport.peer.dispatcher import FrameDispatcher


class RecordingConn:
    def __init__(self):
        self.frames = []
        self.closed = False

    def send_frame(self, frame):
        self.frames.append(frame)
        return True


class FailingConn(RecordingConn):
    def send_frame(self, frame):
        self.frames.append(frame)
        return False


class FakeRegistry:
    def __init__(self, conns):
        self._conns = conns
        self._listeners = []
        self._unbind_listeners = []

    def add_listener(self, listener):
        self._listeners.append(listener)

    def add_unbind_listener(self, listener):
        self._unbind_listeners.append(listener)

    def get(self, node_id):
        return self._conns.get(node_id)

    def all(self):
        return list(self._conns.items())


def _ctx():
    nodes = [
        NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000}),
        NodeInfo.from_dict({"name": "B", "ip": "127.0.0.2", "port": 5001}),
        NodeInfo.from_dict({"name": "C", "ip": "127.0.0.3", "port": 5002}),
    ]
    ctx = RuntimeContext(self_node=nodes[0], nodes=nodes)
    ctx.replace_layout(build_layout_config({}, nodes))
    return ctx


def test_coordinator_service_coalesces_group_update_checks():
    ctx = _ctx()
    registry = FakeRegistry({"B": RecordingConn(), "C": RecordingConn()})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(ctx, registry, dispatcher)
    sent = []
    original_reply = service._reply

    def tracking_reply(peer_id, frame):
        sent.append((peer_id, dict(frame)))
        return original_reply(peer_id, frame)

    service._reply = tracking_reply

    service._on_update_check_request("B", make_update_check_request("B", "req-b"))
    service._on_update_check_request("C", make_update_check_request("C", "req-c"))

    commands = [frame for _, frame in sent if frame["kind"] == "ctrl.update_check_command"]
    assert len(commands) == 1

    job_id = str(service._update_check_inflight["job_id"])
    active_candidate_id = str(service._update_check_inflight["active_candidate_id"])
    service._on_update_check_result(
        active_candidate_id,
        make_update_check_result(
            job_id=job_id,
            status="success",
            detail="",
            coordinator_epoch=service._coordinator_epoch,
            source_id=active_candidate_id,
            result={
                "current_version": "0.3.17",
                "latest_version": "0.3.18",
                "latest_tag_name": "v0.3.18",
                "release_url": "https://example.com/release/v0.3.18",
                "installer_url": "https://example.com/download/setup.exe",
                "status": "update_available",
            },
        ),
    )

    states = [frame for _, frame in sent if frame["kind"] == "ctrl.update_check_state"]
    assert len(states) == 2
    assert {frame["requester_id"] for frame in states} == {"B", "C"}
    assert all(frame["status"] == "success" for frame in states)


def test_coordinator_service_coalesces_download_requests_and_falls_back_sequentially():
    ctx = _ctx()
    registry = FakeRegistry({"B": RecordingConn(), "C": RecordingConn()})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(ctx, registry, dispatcher)
    sent = []
    original_reply = service._reply

    def tracking_reply(peer_id, frame):
        sent.append((peer_id, dict(frame)))
        return original_reply(peer_id, frame)

    service._reply = tracking_reply

    service._on_update_download_request(
        "B",
        make_update_download_request(
            requester_id="B",
            request_id="req-b",
            tag_name="v0.3.18",
            installer_url="https://example.com/download/setup.exe",
        ),
    )
    service._on_update_download_request(
        "C",
        make_update_download_request(
            requester_id="C",
            request_id="req-c",
            tag_name="v0.3.18",
            installer_url="https://example.com/download/setup.exe",
        ),
    )

    commands = [frame for _, frame in sent if frame["kind"] == "ctrl.update_download_command"]
    assert len(commands) == 1

    cache_key = "v0.3.18|https://example.com/download/setup.exe"
    first_job = service._update_download_jobs[cache_key]
    first_job_id = str(first_job["job_id"])
    first_candidate_id = str(first_job["active_candidate_id"])

    service._on_update_download_result(
        first_candidate_id,
        make_update_download_result(
            job_id=first_job_id,
            status="failed",
            detail="download failed",
            coordinator_epoch=service._coordinator_epoch,
            source_id=first_candidate_id,
        ),
    )

    commands = [frame for _, frame in sent if frame["kind"] == "ctrl.update_download_command"]
    assert len(commands) == 2

    second_job = service._update_download_jobs[cache_key]
    second_job_id = str(second_job["job_id"])
    second_candidate_id = str(second_job["active_candidate_id"])

    service._on_update_download_result(
        second_candidate_id,
        make_update_download_result(
            job_id=second_job_id,
            status="ready",
            detail="",
            coordinator_epoch=service._coordinator_epoch,
            source_id=second_candidate_id,
            share_port=18765,
            share_id="share-1",
            share_token="token-1",
            sha256="abc123",
            size_bytes=1024,
        ),
    )

    states = [frame for _, frame in sent if frame["kind"] == "ctrl.update_download_state"]
    assert len(states) == 2
    assert {frame["requester_id"] for frame in states} == {"B", "C"}
    assert all(frame["status"] == "ready" for frame in states)


def test_coordinator_service_retries_group_update_check_after_candidate_timeout():
    ctx = _ctx()
    registry = FakeRegistry({"B": RecordingConn(), "C": RecordingConn()})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(ctx, registry, dispatcher)
    current_time = {"value": 1.0}
    service._now = lambda: current_time["value"]
    sent = []
    original_reply = service._reply

    def tracking_reply(peer_id, frame):
        sent.append((peer_id, dict(frame)))
        return original_reply(peer_id, frame)

    service._reply = tracking_reply

    service._on_update_check_request("B", make_update_check_request("B", "req-b"))
    commands = [frame for _, frame in sent if frame["kind"] == "ctrl.update_check_command"]
    assert len(commands) == 1

    current_time["value"] += service.UPDATE_CHECK_CANDIDATE_TIMEOUT_SEC + 0.1
    service._expire_once()
    commands = [frame for _, frame in sent if frame["kind"] == "ctrl.update_check_command"]
    assert len(commands) == 2

    current_time["value"] += service.UPDATE_CHECK_CANDIDATE_TIMEOUT_SEC + 0.1
    service._expire_once()
    commands = [frame for _, frame in sent if frame["kind"] == "ctrl.update_check_command"]
    assert len(commands) == 3

    current_time["value"] += service.UPDATE_CHECK_CANDIDATE_TIMEOUT_SEC + 0.1
    service._expire_once()
    states = [frame for _, frame in sent if frame["kind"] == "ctrl.update_check_state"]
    assert len(states) == 1
    assert states[0]["status"] == "failed"
    assert "업데이트 확인" in states[0]["detail"]


def test_coordinator_service_retries_group_update_download_after_candidate_timeout():
    ctx = _ctx()
    registry = FakeRegistry({"B": RecordingConn(), "C": RecordingConn()})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(ctx, registry, dispatcher)
    current_time = {"value": 1.0}
    service._now = lambda: current_time["value"]
    sent = []
    original_reply = service._reply

    def tracking_reply(peer_id, frame):
        sent.append((peer_id, dict(frame)))
        return original_reply(peer_id, frame)

    service._reply = tracking_reply

    service._on_update_download_request(
        "B",
        make_update_download_request(
            requester_id="B",
            request_id="req-b",
            tag_name="v0.3.18",
            installer_url="https://example.com/download/setup.exe",
        ),
    )
    commands = [frame for _, frame in sent if frame["kind"] == "ctrl.update_download_command"]
    assert len(commands) == 1

    current_time["value"] += service.UPDATE_DOWNLOAD_CANDIDATE_TIMEOUT_SEC + 0.1
    service._expire_once()
    commands = [frame for _, frame in sent if frame["kind"] == "ctrl.update_download_command"]
    assert len(commands) == 2

    current_time["value"] += service.UPDATE_DOWNLOAD_CANDIDATE_TIMEOUT_SEC + 0.1
    service._expire_once()
    commands = [frame for _, frame in sent if frame["kind"] == "ctrl.update_download_command"]
    assert len(commands) == 3

    current_time["value"] += service.UPDATE_DOWNLOAD_CANDIDATE_TIMEOUT_SEC + 0.1
    service._expire_once()
    states = [frame for _, frame in sent if frame["kind"] == "ctrl.update_download_state"]
    assert len(states) == 1
    assert states[0]["status"] == "failed"
    assert "설치 파일" in states[0]["detail"]


def test_coordinator_service_retries_group_update_check_when_command_send_fails():
    ctx = _ctx()
    registry = FakeRegistry({"B": FailingConn(), "C": RecordingConn()})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(ctx, registry, dispatcher)
    service._update_candidate_ids = lambda: ["B", "C"]
    sent = []
    original_reply = service._reply

    def tracking_reply(peer_id, frame):
        sent.append((peer_id, dict(frame)))
        return original_reply(peer_id, frame)

    service._reply = tracking_reply

    service._on_update_check_request("B", make_update_check_request("B", "req-b"))

    commands = [item for item in sent if item[1]["kind"] == "ctrl.update_check_command"]
    assert [peer_id for peer_id, _frame in commands] == ["B", "C"]
    assert service._update_check_inflight["active_candidate_id"] == "C"


def test_coordinator_service_retries_group_update_download_when_command_send_fails():
    ctx = _ctx()
    registry = FakeRegistry({"B": FailingConn(), "C": RecordingConn()})
    dispatcher = FrameDispatcher()
    service = CoordinatorService(ctx, registry, dispatcher)
    service._update_download_candidate_ids = lambda: ["B", "C"]
    sent = []
    original_reply = service._reply

    def tracking_reply(peer_id, frame):
        sent.append((peer_id, dict(frame)))
        return original_reply(peer_id, frame)

    service._reply = tracking_reply

    service._on_update_download_request(
        "B",
        make_update_download_request(
            requester_id="B",
            request_id="req-b",
            tag_name="v0.3.18",
            installer_url="https://example.com/download/setup.exe",
        ),
    )

    commands = [item for item in sent if item[1]["kind"] == "ctrl.update_download_command"]
    assert [peer_id for peer_id, _frame in commands] == ["B", "C"]
    cache_key = "v0.3.18|https://example.com/download/setup.exe"
    assert service._update_download_jobs[cache_key]["active_candidate_id"] == "C"


def test_coordinator_client_group_update_timeouts_emit_failed_status():
    ctx = _ctx()
    conn_b = RecordingConn()
    registry = FakeRegistry({"B": conn_b})
    dispatcher = FrameDispatcher()
    current = {"node": ctx.get_node("B")}
    client = CoordinatorClient(
        ctx,
        registry,
        dispatcher,
        coordinator_resolver=lambda: current["node"],
    )
    received_check = []
    received_download = []
    client.set_update_check_status_handler(received_check.append)
    client.set_update_download_status_handler(received_download.append)

    request_id = client.request_group_update_check()
    assert request_id
    check_request_id = conn_b.frames[-1]["request_id"]
    check_started_at = client._pending_one_shot_requests[check_request_id]["started_at"]
    client._expire_pending_one_shot_requests(now=check_started_at + client.UPDATE_CHECK_TIMEOUT_SEC + 0.1)

    assert received_check[-1]["status"] == "failed"
    assert received_check[-1]["reason"] == "timeout"

    request_id = client.request_group_update_download(
        tag_name="v0.3.18",
        installer_url="https://example.com/download/setup.exe",
    )
    assert request_id
    download_request_id = conn_b.frames[-1]["request_id"]
    download_started_at = client._pending_one_shot_requests[download_request_id]["started_at"]
    client._expire_pending_one_shot_requests(
        now=download_started_at + client.UPDATE_DOWNLOAD_TIMEOUT_SEC + 0.1
    )

    assert received_download[-1]["status"] == "failed"
    assert received_download[-1]["reason"] == "timeout"
