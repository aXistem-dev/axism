"""Persistent aXism preferences (XDG config, not inside provider trees)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SETTINGS_FILENAME = "settings.json"
DEFAULT_PROVIDER = "claude_code"


def settings_dir() -> Path:
    """aXism config directory (``$AXISM_CONFIG_DIR`` or ``~/.config/axism``)."""
    override = os.environ.get("AXISM_CONFIG_DIR")
    if override:
        return Path(override).expanduser().resolve()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser().resolve() / "axism"
    return (Path.home() / ".config" / "axism").resolve()


def settings_path() -> Path:
    return settings_dir() / SETTINGS_FILENAME


def _parse_enabled(value: object) -> bool:
    """Real booleans pass through; ``"true"``/``"false"`` strings are read; else on."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text == "false":
            return False
    return True


@dataclass
class ProviderPrefs:
    """Per-provider preferences (extensible for future backends)."""

    enabled: bool = True
    config_dir: str | None = None  # absolute path; None → provider default / env

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "config_dir": self.config_dir,
        }

    @classmethod
    def from_dict(cls, data: object) -> ProviderPrefs:
        if not isinstance(data, dict):
            return cls()
        raw_dir = data.get("config_dir")
        config_dir: str | None
        if isinstance(raw_dir, str) and raw_dir.strip():
            config_dir = str(Path(raw_dir.strip()).expanduser())
        else:
            config_dir = None
        return cls(enabled=_parse_enabled(data.get("enabled")), config_dir=config_dir)


@dataclass
class Settings:
    """User preferences for providers and (later) session display."""

    active_provider: str = DEFAULT_PROVIDER
    providers: dict[str, ProviderPrefs] = field(default_factory=dict)
    # Provider ids present in the loaded settings.json (not the in-memory default).
    _recorded: set[str] = field(default_factory=set, repr=False, compare=False)
    # Set when settings.json exists but could not be read or parsed.
    _load_error: str = field(default="", repr=False, compare=False)

    def __post_init__(self) -> None:
        if DEFAULT_PROVIDER not in self.providers:
            self.providers[DEFAULT_PROVIDER] = ProviderPrefs()

    def recorded_providers(self) -> set[str]:
        """Provider ids the user's settings file records (empty without a file)."""
        return set(self._recorded)

    def load_error(self) -> str:
        """Why an existing settings file could not be read ('' when it was fine or absent)."""
        return self._load_error

    def mark_recorded(self, names: set[str] | list[str]) -> None:
        self._recorded |= set(names)

    def provider_prefs(self, name: str | None = None) -> ProviderPrefs:
        key = name or self.active_provider
        if key not in self.providers:
            self.providers[key] = ProviderPrefs()
        return self.providers[key]

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_provider": self.active_provider,
            "providers": {k: v.to_dict() for k, v in sorted(self.providers.items())},
        }

    @classmethod
    def from_dict(cls, data: object) -> Settings:
        if not isinstance(data, dict):
            return cls()
        active = data.get("active_provider")
        if not isinstance(active, str) or not active.strip():
            active = DEFAULT_PROVIDER
        providers: dict[str, ProviderPrefs] = {}
        raw_providers = data.get("providers")
        if isinstance(raw_providers, dict):
            for name, pdata in raw_providers.items():
                if isinstance(name, str) and name.strip():
                    providers[name.strip()] = ProviderPrefs.from_dict(pdata)
        return cls(
            active_provider=active.strip(),
            providers=providers,
            _recorded=set(providers),
        )


def default_settings() -> Settings:
    return Settings(
        active_provider=DEFAULT_PROVIDER,
        providers={DEFAULT_PROVIDER: ProviderPrefs(enabled=True, config_dir=None)},
    )


def _unreadable(reason: str) -> Settings:
    settings = default_settings()
    settings._load_error = reason
    return settings


def load_settings(path: Path | None = None) -> Settings:
    """Load settings from disk.

    A missing file gives the defaults. A file that exists but cannot be read
    or parsed also gives the defaults, with :meth:`Settings.load_error` set so
    callers know not to overwrite it.
    """
    target = path or settings_path()
    try:
        text = target.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return default_settings()
    except (OSError, UnicodeDecodeError) as exc:
        return _unreadable(getattr(exc, "strerror", None) or type(exc).__name__)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return _unreadable("not valid JSON")
    if not isinstance(data, dict):
        return _unreadable("not a JSON object")
    return Settings.from_dict(data)


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    """Write settings atomically. Returns the path written."""
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    settings.mark_recorded(set(settings.providers))
    payload = json.dumps(settings.to_dict(), indent=2, sort_keys=True) + "\n"
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, target)
    return target
