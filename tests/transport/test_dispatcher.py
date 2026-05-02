from transport.peer.dispatcher import FrameDispatcher


def test_dispatcher_isolates_control_handler_exceptions():
    dispatcher = FrameDispatcher()
    calls = []

    def failing_handler(_peer_id, _frame):
        calls.append("failed")
        raise RuntimeError("boom")

    def healthy_handler(_peer_id, _frame):
        calls.append("healthy")

    dispatcher.register_control_handler("ctrl.fail", failing_handler)
    dispatcher.register_control_handler("ctrl.ok", healthy_handler)

    dispatcher.dispatch("B", {"kind": "ctrl.fail"})
    dispatcher.dispatch("B", {"kind": "ctrl.ok"})

    assert calls == ["failed", "healthy"]


def test_dispatcher_isolates_input_handler_exceptions():
    dispatcher = FrameDispatcher()
    calls = []

    dispatcher.set_input_handler(lambda _peer_id, _frame: (_ for _ in ()).throw(RuntimeError("boom")))
    dispatcher.dispatch("B", {"kind": "mouse_move"})

    dispatcher.set_input_handler(lambda _peer_id, _frame: calls.append("healthy"))
    dispatcher.dispatch("B", {"kind": "mouse_move"})

    assert calls == ["healthy"]
