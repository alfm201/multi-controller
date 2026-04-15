"""Peer handshake rejection helpers."""

from __future__ import annotations

from dataclasses import dataclass


PEER_REJECT_KIND = "ctrl.peer_reject"
REJECT_REASON_UNKNOWN_NODE = "unknown_node"

DEFAULT_RETRY_AFTER_SEC_BY_REASON = {
    REJECT_REASON_UNKNOWN_NODE: 60.0,
}


@dataclass(frozen=True)
class PeerRejectInfo:
    reason: str
    detail: str = ""
    retry_after_sec: float | None = None
    retryable: bool = True


def make_peer_reject(
    reason: str,
    *,
    detail: str = "",
    retry_after_sec: float | None = None,
    retryable: bool | None = None,
) -> dict:
    normalized_reason = str(reason or "").strip() or "unknown"
    frame = {
        "kind": PEER_REJECT_KIND,
        "reason": normalized_reason,
        "detail": str(detail or "").strip(),
        "retryable": bool(default_retryable(normalized_reason) if retryable is None else retryable),
    }
    effective_retry_after = default_retry_after_sec(normalized_reason) if retry_after_sec is None else retry_after_sec
    if effective_retry_after is not None:
        frame["retry_after_sec"] = float(max(effective_retry_after, 0.0))
    return frame


def parse_peer_reject(frame: dict) -> PeerRejectInfo:
    if frame.get("kind") != PEER_REJECT_KIND:
        raise ValueError(f"expected {PEER_REJECT_KIND!r}, got {frame.get('kind')!r}")
    reason = str(frame.get("reason") or "").strip()
    if not reason:
        raise ValueError("peer reject missing reason")
    detail = str(frame.get("detail") or "").strip()
    retry_after_raw = frame.get("retry_after_sec")
    retry_after_sec = None
    if retry_after_raw is not None:
        retry_after_sec = float(retry_after_raw)
        if retry_after_sec < 0:
            retry_after_sec = 0.0
    retryable = bool(frame.get("retryable", default_retryable(reason)))
    return PeerRejectInfo(
        reason=reason,
        detail=detail,
        retry_after_sec=retry_after_sec,
        retryable=retryable,
    )


def default_retry_after_sec(reason: str) -> float | None:
    return DEFAULT_RETRY_AFTER_SEC_BY_REASON.get(str(reason or "").strip())


def default_retryable(reason: str) -> bool:
    return True


def describe_peer_reject_reason(reason: str, detail: str = "") -> str:
    normalized_reason = str(reason or "").strip()
    detail = str(detail or "").strip()
    if normalized_reason == REJECT_REASON_UNKNOWN_NODE:
        return detail or "상대 노드 목록에 현재 PC 정보가 없습니다."
    return detail or normalized_reason or "알 수 없는 사유"
