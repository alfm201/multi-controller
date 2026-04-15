"""Tests for network/peer_reject.py."""

from network.peer_reject import (
    PEER_REJECT_KIND,
    REJECT_REASON_UNKNOWN_NODE,
    describe_peer_reject_reason,
    make_peer_reject,
    parse_peer_reject,
)


def test_make_and_parse_peer_reject_preserves_reason_and_retry_after():
    frame = make_peer_reject(
        REJECT_REASON_UNKNOWN_NODE,
        detail="상대 노드 목록에 현재 PC 정보가 없습니다.",
        retry_after_sec=60,
    )

    reject = parse_peer_reject(frame)

    assert frame["kind"] == PEER_REJECT_KIND
    assert reject.reason == REJECT_REASON_UNKNOWN_NODE
    assert reject.detail == "상대 노드 목록에 현재 PC 정보가 없습니다."
    assert reject.retry_after_sec == 60.0
    assert reject.retryable is True


def test_describe_peer_reject_reason_uses_human_friendly_text():
    assert (
        describe_peer_reject_reason(REJECT_REASON_UNKNOWN_NODE)
        == "상대 노드 목록에 현재 PC 정보가 없습니다."
    )
