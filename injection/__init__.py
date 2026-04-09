"""
injection: target 노드가 수신한 input event 를 실제 OS 에 적용하는 계층.

routing/sink.py 의 InputSink 는 이 패키지의 OSInjector 구현체에 위임한다.
교체 지점을 한 곳으로 고정하기 위함. 테스트에서는 LoggingOSInjector 또는
mock 을 주입해서 pynput 없이도 로직을 검증할 수 있다.
"""
