from pathlib import Path

from app.logging.app_logging import tag_message


def test_tag_message_uses_single_domain_tag():
    assert tag_message("peer", "connected") == "[PEER] connected"
    assert tag_message("switch") == "[SWITCH]"


def test_source_does_not_use_disallowed_legacy_log_tags():
    repo_root = Path(__file__).resolve().parents[2]
    code_roots = (
        repo_root / "app",
        repo_root / "control",
        repo_root / "main.py",
        repo_root / "model",
        repo_root / "msp_platform",
        repo_root / "transport",
    )
    banned = (
        "[DEBUG]",
        "[AUTO SWITCH DEBUG]",
        "[CAPTURE DROP]",
        "[PEER DIAL CONNECT FAIL]",
        "[PEER DIAL HANDSHAKE FAIL]",
        "[PEER DIAL ID MISMATCH]",
        "[PEER DIAL LOSES RACE]",
    )
    offenders = []

    for root in code_roots:
        paths = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for banned_tag in banned:
                if banned_tag in text:
                    offenders.append(f"{path.relative_to(repo_root)} -> {banned_tag}")

    assert offenders == []
