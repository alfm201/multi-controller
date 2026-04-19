"""Tests for app/update/update_domain.py."""

from app.update.update_domain import (
    UPDATE_ACTION_REMOTE_REQUEST,
    UPDATE_ORIGIN_REMOTE_COMMAND,
    UPDATE_STAGE_INSTALLING,
    UPDATE_STAGE_REQUEST_SENT,
    UPDATE_STAGE_UPDATE_AVAILABLE,
    UPDATE_REASON_BUSY,
    UPDATE_REASON_TIMEOUT,
    UPDATE_STAGE_FAILED,
    UPDATE_TARGET_REMOTE_NODE,
    UPDATE_TARGET_SELF,
    build_update_event_message,
    build_update_notice_payload,
    make_remote_update_status_payload,
    normalize_update_event,
    should_announce_update_notice,
)


def test_normalize_update_event_maps_legacy_remote_status_to_common_stage():
    event = normalize_update_event(
        {
            "target_id": "B",
            "requester_id": "A",
            "status": "starting",
            "latest_version": "0.3.18",
        },
        default_target_kind=UPDATE_TARGET_REMOTE_NODE,
        default_action=UPDATE_ACTION_REMOTE_REQUEST,
        default_origin=UPDATE_ORIGIN_REMOTE_COMMAND,
    )

    assert event["stage"] == UPDATE_STAGE_INSTALLING
    assert event["status"] == UPDATE_STAGE_INSTALLING
    assert event["target_version"] == "0.3.18"
    assert event["target_kind"] == UPDATE_TARGET_REMOTE_NODE


def test_make_remote_update_status_payload_keeps_common_update_metadata():
    payload = make_remote_update_status_payload(
        target_id="B",
        requester_id="A",
        status=UPDATE_STAGE_REQUEST_SENT,
        current_version="0.3.17",
        latest_version="0.3.18",
        action=UPDATE_ACTION_REMOTE_REQUEST,
        origin=UPDATE_ORIGIN_REMOTE_COMMAND,
    )

    assert payload["status"] == "requested"
    assert payload["stage"] == UPDATE_STAGE_REQUEST_SENT
    assert payload["action"] == UPDATE_ACTION_REMOTE_REQUEST
    assert payload["origin"] == UPDATE_ORIGIN_REMOTE_COMMAND
    assert payload["target_kind"] == UPDATE_TARGET_REMOTE_NODE
    assert payload["target_version"] == "0.3.18"
    assert payload["reason"] == ""


def test_build_update_notice_payload_carries_stage_action_and_versions():
    payload = build_update_notice_payload(
        stage=UPDATE_STAGE_UPDATE_AVAILABLE,
        current_version="0.3.17",
        target_version="0.3.18",
        tag_name="v0.3.18",
        target_kind=UPDATE_TARGET_SELF,
    )

    assert payload["stage"] == UPDATE_STAGE_UPDATE_AVAILABLE
    assert payload["target_kind"] == UPDATE_TARGET_SELF
    assert payload["visible"] is True
    assert payload["tag_name"] == "v0.3.18"
    assert "v0.3.18" in payload["title"]
    assert payload["detail"] == "설치 버튼을 눌러 새 버전 준비를 시작할 수 있습니다."
    assert should_announce_update_notice(payload) is True


def test_build_update_event_message_for_self_update_available_omits_versions_in_detail():
    payload = build_update_notice_payload(
        stage=UPDATE_STAGE_UPDATE_AVAILABLE,
        current_version="0.3.17",
        target_version="0.3.18",
        tag_name="v0.3.18",
        target_kind=UPDATE_TARGET_SELF,
    )

    message, tone = build_update_event_message(payload)

    assert tone == "accent"
    assert message == "설치 버튼을 눌러 새 버전 준비를 시작할 수 있습니다."


def test_build_update_event_message_formats_remote_request_and_versions():
    payload = make_remote_update_status_payload(
        target_id="B",
        requester_id="A",
        status=UPDATE_STAGE_REQUEST_SENT,
        current_version="0.3.17",
        latest_version="0.3.18",
        action=UPDATE_ACTION_REMOTE_REQUEST,
        origin=UPDATE_ORIGIN_REMOTE_COMMAND,
    )

    message, tone = build_update_event_message(payload, node_label="B(회의실)")

    assert tone == "accent"
    assert message == "B(회의실) 노드에 업데이트 요청을 전송했습니다."


def test_build_update_event_message_formats_remote_busy_failure_from_reason():
    payload = make_remote_update_status_payload(
        target_id="B",
        requester_id="A",
        status=UPDATE_STAGE_FAILED,
        reason=UPDATE_REASON_BUSY,
        detail="임의 상세 문구",
        action=UPDATE_ACTION_REMOTE_REQUEST,
        origin=UPDATE_ORIGIN_REMOTE_COMMAND,
    )

    message, tone = build_update_event_message(payload, node_label="B(회의실)")

    assert tone == "warning"
    assert message == "B(회의실) 노드는 이미 업데이트 진행 중입니다."


def test_build_update_event_message_formats_remote_timeout_failure():
    payload = make_remote_update_status_payload(
        target_id="B",
        requester_id="A",
        status=UPDATE_STAGE_FAILED,
        reason=UPDATE_REASON_TIMEOUT,
        detail="응답 시간 초과",
        action=UPDATE_ACTION_REMOTE_REQUEST,
        origin=UPDATE_ORIGIN_REMOTE_COMMAND,
    )

    message, tone = build_update_event_message(payload, node_label="B(회의실)")

    assert tone == "warning"
    assert message == "B(회의실) 노드 업데이트에 실패했습니다. (응답 시간 초과)"
