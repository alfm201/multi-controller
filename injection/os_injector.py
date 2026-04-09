"""
OSInjector: 수신한 input event 를 실제 OS 에 주입하는 인터페이스.

- abstract base `OSInjector` — 4 개의 메서드만 노출한다.
- `LoggingOSInjector` — OS 를 건드리지 않고 로그만 찍는다. 테스트/dry-run 용.
- `PynputOSInjector` — pynput 기반 실제 주입. target 노드에서 사용.

InputSink 는 이 인터페이스에만 의존한다. 따라서 pynput import 실패 시
main.py 에서 LoggingOSInjector 로 fallback 할 수 있다.

설계 메모
  - `inject_key` 는 down/up 을 bool 로 받는다. 이벤트 dict 의 kind 구분
    (`key_down`/`key_up`)은 sink 에서 이미 풀어서 호출 측이 down flag 만
    넘긴다.
  - `inject_mouse_button` 역시 down/up 을 bool 로 받는다.
  - `inject_mouse_move` 는 절대좌표를 받는다. 상대좌표 변환(DPI 등)은
    이 계층 밖의 과제다.
  - `inject_mouse_wheel` 은 원본 x,y 를 받지만 구현체는 보통 무시한다.
"""

import logging


class OSInjector:
    """Abstract interface. Sink 는 이 인터페이스에만 의존한다."""

    def inject_key(self, key_str: str, down: bool) -> None:
        raise NotImplementedError

    def inject_mouse_move(self, x: int, y: int) -> None:
        raise NotImplementedError

    def inject_mouse_button(
        self, button_str: str, x: int, y: int, down: bool
    ) -> None:
        raise NotImplementedError

    def inject_mouse_wheel(self, x: int, y: int, dx: int, dy: int) -> None:
        raise NotImplementedError


class LoggingOSInjector(OSInjector):
    """
    OS 를 건드리지 않고 로그만 찍는 구현.
    - 테스트용 (pynput 없이 sink 로직 검증)
    - dry-run / pynput 미설치 fallback
    """

    def inject_key(self, key_str: str, down: bool) -> None:
        state = "DOWN" if down else "UP"
        logging.info(f"[INJECT KEY    ] {state} key={key_str}")

    def inject_mouse_move(self, x: int, y: int) -> None:
        logging.info(f"[INJECT MOVE   ] x={x} y={y}")

    def inject_mouse_button(
        self, button_str: str, x: int, y: int, down: bool
    ) -> None:
        state = "DOWN" if down else "UP"
        logging.info(
            f"[INJECT CLICK  ] {button_str} {state} x={x} y={y}"
        )

    def inject_mouse_wheel(self, x: int, y: int, dx: int, dy: int) -> None:
        logging.info(f"[INJECT WHEEL  ] x={x} y={y} dx={dx} dy={dy}")


class PynputOSInjector(OSInjector):
    """
    pynput 기반 실제 OS 주입.

    - keyboard.Controller 와 mouse.Controller 를 직접 사용한다.
    - 키/버튼 문자열을 pynput 객체로 되돌리는 것은 injection/key_parser.py 에 위임.
    - 파싱 실패한 키는 조용히 drop (경고 로그만 남긴다). 한 이벤트 실패로
      수신 스레드가 죽는 것을 막기 위함.

    알려진 제약:
      - DPI 불일치 시 마우스 좌표가 어긋날 수 있다.
      - macOS 는 접근성 권한 허가가 필요하다.
      - Linux 는 pynput 의 Xorg 백엔드만 검증됨 (Wayland 미지원).
      - controller 노드의 로컬 입력 suppress 는 이 계층 밖의 책임.
    """

    def __init__(self):
        # import 는 생성자 안에서 — 모듈 import 시점에 pynput 이 없어도
        # 이 파일 전체는 로드되어야 한다 (LoggingOSInjector 를 위해).
        from pynput import keyboard, mouse

        self._keyboard = keyboard.Controller()
        self._mouse = mouse.Controller()
        # key_parser 도 lazy import — pynput 가 있어야 의미가 있는 모듈.
        from injection import key_parser

        self._parse_key = key_parser.parse_key
        self._parse_button = key_parser.parse_button

    def inject_key(self, key_str: str, down: bool) -> None:
        try:
            key = self._parse_key(key_str)
        except Exception as e:
            logging.warning(f"[INJECT KEY    ] parse failed key={key_str!r}: {e}")
            return
        if key is None:
            logging.warning(f"[INJECT KEY    ] unknown key={key_str!r}, dropped")
            return
        try:
            if down:
                self._keyboard.press(key)
            else:
                self._keyboard.release(key)
        except Exception as e:
            logging.warning(
                f"[INJECT KEY    ] OS call failed key={key_str!r} down={down}: {e}"
            )

    def inject_mouse_move(self, x: int, y: int) -> None:
        try:
            self._mouse.position = (int(x), int(y))
        except Exception as e:
            logging.warning(f"[INJECT MOVE   ] OS call failed x={x} y={y}: {e}")

    def inject_mouse_button(
        self, button_str: str, x: int, y: int, down: bool
    ) -> None:
        try:
            button = self._parse_button(button_str)
        except Exception as e:
            logging.warning(
                f"[INJECT CLICK  ] parse failed button={button_str!r}: {e}"
            )
            return
        if button is None:
            logging.warning(
                f"[INJECT CLICK  ] unknown button={button_str!r}, dropped"
            )
            return
        # 좌표도 같이 맞춰준다 — 원격 위치에서 클릭이 일어났다는 의미.
        try:
            self._mouse.position = (int(x), int(y))
            if down:
                self._mouse.press(button)
            else:
                self._mouse.release(button)
        except Exception as e:
            logging.warning(
                f"[INJECT CLICK  ] OS call failed button={button_str!r} "
                f"down={down}: {e}"
            )

    def inject_mouse_wheel(self, x: int, y: int, dx: int, dy: int) -> None:
        try:
            # pynput.mouse.Controller.scroll(dx, dy)
            self._mouse.scroll(int(dx), int(dy))
        except Exception as e:
            logging.warning(
                f"[INJECT WHEEL  ] OS call failed dx={dx} dy={dy}: {e}"
            )
