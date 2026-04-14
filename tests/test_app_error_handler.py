from pathlib import Path
from types import SimpleNamespace

from runtime.app_error_handler import (
    build_user_friendly_error_message,
    install_unhandled_exception_handler,
)


def test_build_user_friendly_error_message_includes_log_path():
    message = build_user_friendly_error_message(
        app_name="Multi Screen Pass",
        exc_type=RuntimeError,
        exc_value=RuntimeError("boom"),
        log_path=Path("logs/debug.log"),
    )

    assert "예기치 않은 오류" in message
    assert "RuntimeError: boom" in message
    assert str(Path("logs/debug.log")) in message


def test_install_unhandled_exception_handler_shows_dialog_and_runs_cleanup(monkeypatch):
    cleanup_calls = []
    shown = []
    registered = []

    monkeypatch.setattr("runtime.app_error_handler.atexit.register", lambda fn: registered.append(fn))
    monkeypatch.setattr("runtime.app_error_handler.show_user_friendly_error_dialog", lambda **kwargs: shown.append(kwargs))
    monkeypatch.setattr("runtime.app_error_handler.logging.critical", lambda *args, **kwargs: None)
    monkeypatch.setattr("runtime.app_error_handler.sys.excepthook", lambda *args: None)
    monkeypatch.setattr("runtime.app_error_handler.threading.excepthook", lambda args: None)

    install_unhandled_exception_handler(
        app_name="Multi Screen Pass",
        cleanup_actions=(lambda: cleanup_calls.append("cleanup"),),
        log_path="logs/debug.log",
        delegate_previous=False,
    )

    assert len(registered) == 1
    registered[0]()
    assert cleanup_calls == ["cleanup"]

    monkeypatch.setattr("runtime.app_error_handler.show_user_friendly_error_dialog", lambda **kwargs: shown.append(kwargs))
    import runtime.app_error_handler as module

    module.sys.excepthook(RuntimeError, RuntimeError("boom"), None)

    assert cleanup_calls == ["cleanup", "cleanup"]
    assert len(shown) == 1
    assert shown[0]["log_path"] == "logs/debug.log"


def test_install_unhandled_exception_handler_can_delegate_previous_hooks(monkeypatch):
    previous_sys_calls = []
    previous_thread_calls = []

    monkeypatch.setattr("runtime.app_error_handler.atexit.register", lambda fn: None)
    monkeypatch.setattr("runtime.app_error_handler.show_user_friendly_error_dialog", lambda **kwargs: True)
    monkeypatch.setattr("runtime.app_error_handler.logging.critical", lambda *args, **kwargs: None)
    monkeypatch.setattr("runtime.app_error_handler.sys.excepthook", lambda *args: previous_sys_calls.append(args))
    monkeypatch.setattr(
        "runtime.app_error_handler.threading.excepthook",
        lambda args: previous_thread_calls.append(args),
    )

    install_unhandled_exception_handler(
        app_name="Multi Screen Pass",
        cleanup_actions=(),
        delegate_previous=True,
    )

    import runtime.app_error_handler as module

    exc = RuntimeError("boom")
    module.sys.excepthook(RuntimeError, exc, None)
    thread_args = SimpleNamespace(exc_type=RuntimeError, exc_value=exc, exc_traceback=None)
    module.threading.excepthook(thread_args)

    assert len(previous_sys_calls) == 1
    assert previous_thread_calls == [thread_args]


def test_install_unhandled_exception_handler_shows_only_one_dialog(monkeypatch):
    shown = []

    monkeypatch.setattr("runtime.app_error_handler.atexit.register", lambda fn: None)
    monkeypatch.setattr("runtime.app_error_handler.logging.critical", lambda *args, **kwargs: None)
    monkeypatch.setattr("runtime.app_error_handler.show_user_friendly_error_dialog", lambda **kwargs: shown.append(kwargs))
    monkeypatch.setattr("runtime.app_error_handler.sys.excepthook", lambda *args: None)
    monkeypatch.setattr("runtime.app_error_handler.threading.excepthook", lambda args: None)

    install_unhandled_exception_handler(
        app_name="Multi Screen Pass",
        cleanup_actions=(),
        delegate_previous=False,
    )

    import runtime.app_error_handler as module

    exc = RuntimeError("boom")
    module.sys.excepthook(RuntimeError, exc, None)
    module.sys.excepthook(RuntimeError, exc, None)

    assert len(shown) == 1
