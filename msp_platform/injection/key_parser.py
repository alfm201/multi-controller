"""
key_parser: wire string → pynput Key/Button 역변환.

model/events.py::_normalize_key 는 pynput 의 Key 객체를 받으면
  - key.char 가 있으면 그 문자열 (printable 키)
  - 없으면 str(key) — pynput 규약상 항상 `"Key.<name>"` 형태
을 wire 로 내보낸다.

수신 측에서는 이 문자열을 다시 pynput 객체로 복원해야 OS 주입이 가능하다.
그 역함수가 parse_key 다. 같은 원리로 parse_button 은
  - `"Button.left"` → `pynput.mouse.Button.left`
등을 수행한다.

parse_* 함수는 pynput 을 import 해야 하므로, 이 모듈 자체는 pynput 이
설치된 환경에서만 의미를 갖는다. 미설치 환경에서는 ImportError 가 발생하며,
호출 측 (PynputOSInjector) 이 fallback 을 책임진다.

알 수 없는 입력은 None 을 돌려준다. 호출 측 (os_injector.PynputOSInjector)
이 이를 받아서 경고 로그 후 drop 한다. 예외를 올려 수신 스레드를 죽이지
않기 위함.
"""

from pynput import keyboard, mouse

_KEY_PREFIX = "Key."
_BUTTON_PREFIX = "Button."
_KEY_ALIASES = {
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "shift_l": "shift",
    "shift_r": "shift",
    "alt_l": "alt",
    "alt_r": "alt",
    "cmd_l": "cmd",
    "cmd_r": "cmd",
}


def parse_key(s: str):
    """
    wire 문자열을 pynput 의 Key 객체 또는 raw char 로 되돌린다.

    반환값:
      - printable 한 단일 문자 → 그 문자열 그대로 ('a', 'Z', '1', ' ')
        pynput.keyboard.Controller.press(str) 가 그대로 동작한다.
      - 'Key.<name>' → keyboard.Key 열거형 값. 예: 'Key.esc' → Key.esc
      - 알 수 없는 'Key.xxx' → None (호출 측이 drop)
      - None/빈 문자열 → None
    """
    if not s:
        return None
    if s.startswith(_KEY_PREFIX):
        name = s[len(_KEY_PREFIX):]
        name = _KEY_ALIASES.get(name, name)
        return getattr(keyboard.Key, name, None)
    # printable char (single or grapheme) — pynput accepts str directly.
    return s


def parse_button(s: str):
    """
    wire 문자열을 pynput 의 Button 열거형으로 되돌린다.

      - 'Button.left' → mouse.Button.left
      - 'Button.right' → mouse.Button.right
      - 'Button.middle' → mouse.Button.middle
      - 알 수 없는 값 또는 None → None
    """
    if not s:
        return None
    if s.startswith(_BUTTON_PREFIX):
        name = s[len(_BUTTON_PREFIX):]
        return getattr(mouse.Button, name, None)
    # 혹시 접두사 없이 'left' 같은 형태로 들어와도 한 번 시도해 본다.
    return getattr(mouse.Button, s, None)
