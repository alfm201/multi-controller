from __future__ import annotations

import threading
import time
from urllib.request import urlopen

from app.update.group_update import InstallerShareManager, build_shared_installer_url


def test_installer_share_manager_serves_downloads_sequentially(tmp_path):
    payload_path = tmp_path / "installer.exe"
    payload_path.write_bytes(b"x" * 4096)

    manager = InstallerShareManager(stream_chunk_size=1024, stream_delay_sec=0.05)
    shared = manager.share_file(payload_path, sha256="", size_bytes=payload_path.stat().st_size)
    url = build_shared_installer_url(
        "127.0.0.1",
        int(shared["share_port"]),
        str(shared["share_id"]),
        str(shared["share_token"]),
    )

    results = []

    def worker(index: int) -> None:
        started_at = time.monotonic()
        with urlopen(url, timeout=5.0) as response:
            body = response.read()
        finished_at = time.monotonic()
        results.append((index, started_at, finished_at, body))

    first = threading.Thread(target=worker, args=(1,), daemon=True)
    second = threading.Thread(target=worker, args=(2,), daemon=True)
    overall_started_at = time.monotonic()
    first.start()
    second.start()
    first.join(timeout=5.0)
    second.join(timeout=5.0)
    overall_finished_at = time.monotonic()
    manager.close()

    assert len(results) == 2
    ordered = sorted(results, key=lambda item: item[2])
    assert ordered[0][3] == b"x" * 4096
    assert ordered[1][3] == b"x" * 4096
    assert ordered[1][2] > ordered[0][2]
    assert (overall_finished_at - overall_started_at) >= 0.35
