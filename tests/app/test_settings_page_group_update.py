from types import SimpleNamespace

from PySide6.QtCore import Qt

from app.config.app_settings import AppSettings
from app.ui.settings_page import SettingsPage
from app.update.app_version import UpdateCheckResult


class FakeCoordClient:
    def __init__(self, *, check_result: bool = True, download_result: bool = True):
        self.request_check_calls = []
        self.request_download_calls = []
        self._check_result = check_result
        self._download_result = download_result
        self._update_check_handler = None
        self._update_check_status_handler = None
        self._update_download_handler = None
        self._update_download_status_handler = None

    def set_update_check_handler(self, handler):
        self._update_check_handler = handler

    def set_update_check_status_handler(self, handler):
        self._update_check_status_handler = handler

    def set_update_download_handler(self, handler):
        self._update_download_handler = handler

    def set_update_download_status_handler(self, handler):
        self._update_download_status_handler = handler

    def request_group_update_check(self):
        self.request_check_calls.append(True)
        if not self._check_result:
            return None
        return f"req-check-{len(self.request_check_calls)}"

    def request_group_update_download(self, **kwargs):
        self.request_download_calls.append(dict(kwargs))
        if not self._download_result:
            return None
        return f"req-download-{len(self.request_download_calls)}"


class RecordingFlexibleUpdateInstaller:
    def __init__(self):
        self.calls = []

    def prepare_update(self, result, **kwargs):
        self.calls.append((result, dict(kwargs)))
        return SimpleNamespace(
            installer_path="installer.exe",
            manifest_path="manifest.json",
            launcher_pid=1234,
            relaunch_mode=kwargs.get("relaunch_mode", "preserve"),
        )


def test_settings_page_manual_check_falls_back_to_group_update_check(qtbot):
    ctx = SimpleNamespace(settings=AppSettings(), layout=None)
    coord_client = FakeCoordClient()

    def failing_update_checker():
        raise OSError("network down")

    page = SettingsPage(
        ctx,
        coord_client=coord_client,
        update_checker=failing_update_checker,
    )
    qtbot.addWidget(page)

    qtbot.mouseClick(page._version_check_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: len(coord_client.request_check_calls) == 1)

    assert page._version_check_running is True


def test_settings_page_group_update_download_uses_shared_installer_url(qtbot):
    ctx = SimpleNamespace(
        settings=AppSettings(),
        layout=None,
        self_node=SimpleNamespace(node_id="A"),
    )
    installer = RecordingFlexibleUpdateInstaller()
    coord_client = FakeCoordClient()
    page = SettingsPage(
        ctx,
        coord_client=coord_client,
        update_installer=installer,
        request_quit=lambda: None,
    )
    qtbot.addWidget(page)
    page._latest_update_result = UpdateCheckResult(
        current_version="0.3.17",
        latest_version="0.3.18",
        latest_tag_name="v0.3.18",
        release_url="https://example.com/release/v0.3.18",
        installer_url="https://example.com/download/MultiScreenPass-Setup-0.3.18.exe",
        status="update_available",
    )

    page._start_update_install(trigger="manual")

    assert coord_client.request_download_calls == [
        {
            "tag_name": "v0.3.18",
            "installer_url": "https://example.com/download/MultiScreenPass-Setup-0.3.18.exe",
            "current_version": "0.3.17",
            "latest_version": "0.3.18",
        }
    ]

    coord_client._update_download_status_handler(
        {
            "status": "ready",
            "detail": "",
            "request_id": "req-download-1",
            "source_id": "A",
            "share_port": 18765,
            "share_id": "share-1",
            "share_token": "token-1",
            "sha256": "abc123",
            "size_bytes": 1024,
            "coordinator_epoch": "A:1",
        }
    )

    qtbot.waitUntil(lambda: bool(installer.calls))

    kwargs = installer.calls[0][1]
    assert kwargs["installer_url_override"] == "http://127.0.0.1:18765/installer/share-1?token=token-1"
    assert kwargs["expected_sha256"] == "abc123"
    assert kwargs["expected_size_bytes"] == 1024
