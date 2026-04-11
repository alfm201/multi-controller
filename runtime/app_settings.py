"""Application-level user settings helpers."""

from __future__ import annotations

from dataclasses import dataclass


DEFAULT_PREVIOUS_TARGET_HOTKEY = "Ctrl+Alt+Q"
DEFAULT_NEXT_TARGET_HOTKEY = "Ctrl+Alt+E"
DEFAULT_TOGGLE_AUTO_SWITCH_HOTKEY = "Ctrl+Alt+Z"
DEFAULT_QUIT_APP_HOTKEY = "Ctrl+Alt+Esc"

_MODIFIER_ALIASES = {
    "CTRL": "Ctrl",
    "CONTROL": "Ctrl",
    "ALT": "Alt",
    "SHIFT": "Shift",
    "WIN": "Win",
    "CMD": "Win",
    "META": "Win",
}

_MODIFIER_GROUPS = {
    "Ctrl": ("Key.ctrl", "Key.ctrl_l", "Key.ctrl_r"),
    "Alt": ("Key.alt", "Key.alt_l", "Key.alt_r"),
    "Shift": ("Key.shift", "Key.shift_l", "Key.shift_r"),
    "Win": ("Key.cmd", "Key.cmd_l", "Key.cmd_r"),
}

_TRIGGER_ALIASES = {
    "ESC": "Esc",
    "ESCAPE": "Esc",
    "TAB": "Tab",
    "SPACE": "Space",
    "ENTER": "Enter",
}

_TRIGGER_TO_KEY = {
    "Esc": "Key.esc",
    "Tab": "Key.tab",
    "Space": "Key.space",
    "Enter": "Key.enter",
}


@dataclass(frozen=True)
class AppHotkeySettings:
    previous_target: str = DEFAULT_PREVIOUS_TARGET_HOTKEY
    next_target: str = DEFAULT_NEXT_TARGET_HOTKEY
    toggle_auto_switch: str = DEFAULT_TOGGLE_AUTO_SWITCH_HOTKEY
    quit_app: str = DEFAULT_QUIT_APP_HOTKEY


@dataclass(frozen=True)
class AppSettings:
    hotkeys: AppHotkeySettings = AppHotkeySettings()


def load_app_settings(config: dict | None) -> AppSettings:
    config = {} if config is None else dict(config)
    raw_settings = config.get("settings") or {}
    raw_hotkeys = raw_settings.get("hotkeys") or {}
    return AppSettings(
        hotkeys=AppHotkeySettings(
            previous_target=normalize_hotkey_string(
                raw_hotkeys.get("previous_target", DEFAULT_PREVIOUS_TARGET_HOTKEY)
            ),
            next_target=normalize_hotkey_string(
                raw_hotkeys.get("next_target", DEFAULT_NEXT_TARGET_HOTKEY)
            ),
            toggle_auto_switch=normalize_hotkey_string(
                raw_hotkeys.get("toggle_auto_switch", DEFAULT_TOGGLE_AUTO_SWITCH_HOTKEY)
            ),
            quit_app=normalize_hotkey_string(
                raw_hotkeys.get(
                    "quit_app",
                    raw_hotkeys.get("stop_capture", DEFAULT_QUIT_APP_HOTKEY),
                )
            ),
        )
    )


def serialize_app_settings(settings: AppSettings) -> dict:
    return {
        "hotkeys": {
            "previous_target": normalize_hotkey_string(settings.hotkeys.previous_target),
            "next_target": normalize_hotkey_string(settings.hotkeys.next_target),
            "toggle_auto_switch": normalize_hotkey_string(settings.hotkeys.toggle_auto_switch),
            "quit_app": normalize_hotkey_string(settings.hotkeys.quit_app),
        }
    }


def normalize_hotkey_string(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("hotkey must be a string")
    parts = [part.strip() for part in value.replace("-", "+").split("+") if part.strip()]
    if not parts:
        raise ValueError("hotkey must not be empty")

    modifiers: list[str] = []
    trigger: str | None = None
    for part in parts:
        upper = part.upper()
        if upper in _MODIFIER_ALIASES:
            canonical_modifier = _MODIFIER_ALIASES[upper]
            if canonical_modifier not in modifiers:
                modifiers.append(canonical_modifier)
            continue
        if trigger is not None:
            raise ValueError(f"hotkey has multiple trigger keys: {value}")
        trigger = _normalize_trigger(part)
    if trigger is None:
        raise ValueError("hotkey must include a trigger key")

    order = {name: index for index, name in enumerate(("Ctrl", "Alt", "Shift", "Win"))}
    modifiers.sort(key=lambda item: order[item])
    return "+".join([*modifiers, trigger])


def hotkey_to_matcher_parts(value: str) -> tuple[tuple[tuple[str, ...], ...], str]:
    canonical = normalize_hotkey_string(value)
    parts = canonical.split("+")
    modifiers = tuple(_MODIFIER_GROUPS[part] for part in parts[:-1])
    trigger_token = parts[-1]
    return modifiers, _trigger_to_key(trigger_token)


def validate_hotkey_settings(settings: AppHotkeySettings) -> AppHotkeySettings:
    normalized = AppHotkeySettings(
        previous_target=normalize_hotkey_string(settings.previous_target),
        next_target=normalize_hotkey_string(settings.next_target),
        toggle_auto_switch=normalize_hotkey_string(settings.toggle_auto_switch),
        quit_app=normalize_hotkey_string(settings.quit_app),
    )
    bindings = {
        normalized.previous_target,
        normalized.next_target,
        normalized.toggle_auto_switch,
        normalized.quit_app,
    }
    if len(bindings) != 4:
        raise ValueError("각 핫키는 서로 다른 조합이어야 합니다.")
    return normalized


def _normalize_trigger(value: str) -> str:
    token = value.strip()
    if not token:
        raise ValueError("trigger key must not be empty")
    upper = token.upper()
    if upper in _TRIGGER_ALIASES:
        return _TRIGGER_ALIASES[upper]
    if len(token) == 1 and token.isprintable():
        return token.upper()
    if upper.startswith("F") and upper[1:].isdigit():
        number = int(upper[1:])
        if 1 <= number <= 24:
            return f"F{number}"
    raise ValueError(f"지원하지 않는 핫키 키입니다: {token}")


def _trigger_to_key(trigger: str) -> str:
    if trigger in _TRIGGER_TO_KEY:
        return _TRIGGER_TO_KEY[trigger]
    if len(trigger) == 1:
        return trigger.lower()
    if trigger.startswith("F") and trigger[1:].isdigit():
        return f"Key.f{int(trigger[1:])}"
    raise ValueError(f"지원하지 않는 핫키 키입니다: {trigger}")
