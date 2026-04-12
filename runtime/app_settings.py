"""Application-level user settings helpers."""

from __future__ import annotations

from dataclasses import dataclass


DEFAULT_PREVIOUS_TARGET_HOTKEY = "Ctrl+Alt+Q"
DEFAULT_NEXT_TARGET_HOTKEY = "Ctrl+Alt+E"
DEFAULT_TOGGLE_AUTO_SWITCH_HOTKEY = "Ctrl+Alt+R"
DEFAULT_QUIT_APP_HOTKEY = "Ctrl+Alt+Esc"
DEFAULT_BACKUP_MIN_COUNT = 10
DEFAULT_BACKUP_MAX_AGE_DAYS = 30

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

_WINDOWS_MODIFIERS = {
    "Alt": 0x0001,
    "Ctrl": 0x0002,
    "Shift": 0x0004,
    "Win": 0x0008,
}

_WINDOWS_SPECIAL_VK = {
    "Esc": 0x1B,
    "Tab": 0x09,
    "Space": 0x20,
    "Enter": 0x0D,
}


@dataclass(frozen=True)
class AppHotkeySettings:
    previous_target: str = DEFAULT_PREVIOUS_TARGET_HOTKEY
    next_target: str = DEFAULT_NEXT_TARGET_HOTKEY
    toggle_auto_switch: str = DEFAULT_TOGGLE_AUTO_SWITCH_HOTKEY
    quit_app: str = DEFAULT_QUIT_APP_HOTKEY


@dataclass(frozen=True)
class BackupRetentionSettings:
    min_count: int = DEFAULT_BACKUP_MIN_COUNT
    max_age_days: int = DEFAULT_BACKUP_MAX_AGE_DAYS


@dataclass(frozen=True)
class AppSettings:
    hotkeys: AppHotkeySettings = AppHotkeySettings()
    backups: BackupRetentionSettings = BackupRetentionSettings()


def load_app_settings(config: dict | None) -> AppSettings:
    config = {} if config is None else dict(config)
    raw_settings = config.get("settings") or {}
    raw_hotkeys = raw_settings.get("hotkeys") or {}
    raw_backups = raw_settings.get("backups") or {}
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
        ),
        backups=validate_backup_retention_settings(
            BackupRetentionSettings(
                min_count=_coerce_int(
                    raw_backups.get("min_count", DEFAULT_BACKUP_MIN_COUNT),
                    field_name="settings.backups.min_count",
                ),
                max_age_days=_coerce_int(
                    raw_backups.get("max_age_days", DEFAULT_BACKUP_MAX_AGE_DAYS),
                    field_name="settings.backups.max_age_days",
                ),
            )
        ),
    )


def serialize_app_settings(settings: AppSettings) -> dict:
    backup_settings = validate_backup_retention_settings(settings.backups)
    return {
        "hotkeys": {
            "previous_target": normalize_hotkey_string(settings.hotkeys.previous_target),
            "next_target": normalize_hotkey_string(settings.hotkeys.next_target),
            "toggle_auto_switch": normalize_hotkey_string(settings.hotkeys.toggle_auto_switch),
            "quit_app": normalize_hotkey_string(settings.hotkeys.quit_app),
        },
        "backups": {
            "min_count": int(backup_settings.min_count),
            "max_age_days": int(backup_settings.max_age_days),
        },
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


def hotkey_to_windows_binding(value: str) -> tuple[int, int]:
    canonical = normalize_hotkey_string(value)
    parts = canonical.split("+")
    modifier_flags = 0
    for part in parts[:-1]:
        modifier_flags |= _WINDOWS_MODIFIERS[part]
    return modifier_flags, _trigger_to_vk(parts[-1])


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
        raise ValueError("hotkeys must all use different combinations")
    return normalized


def validate_backup_retention_settings(
    settings: BackupRetentionSettings,
) -> BackupRetentionSettings:
    min_count = _coerce_int(settings.min_count, field_name="settings.backups.min_count")
    max_age_days = _coerce_int(
        settings.max_age_days,
        field_name="settings.backups.max_age_days",
    )
    if min_count < 1:
        raise ValueError("backup minimum count must be at least 1")
    if max_age_days < 1:
        raise ValueError("backup max age must be at least 1 day")
    return BackupRetentionSettings(min_count=min_count, max_age_days=max_age_days)


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
    raise ValueError(f"unsupported trigger key: {token}")


def _trigger_to_key(trigger: str) -> str:
    if trigger in _TRIGGER_TO_KEY:
        return _TRIGGER_TO_KEY[trigger]
    if len(trigger) == 1:
        return trigger.lower()
    if trigger.startswith("F") and trigger[1:].isdigit():
        return f"Key.f{int(trigger[1:])}"
    raise ValueError(f"unsupported trigger key: {trigger}")


def _trigger_to_vk(trigger: str) -> int:
    if trigger in _WINDOWS_SPECIAL_VK:
        return _WINDOWS_SPECIAL_VK[trigger]
    if len(trigger) == 1:
        return ord(trigger.upper())
    if trigger.startswith("F") and trigger[1:].isdigit():
        return 0x70 + int(trigger[1:]) - 1
    raise ValueError(f"unsupported trigger key: {trigger}")


def _coerce_int(value, *, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
