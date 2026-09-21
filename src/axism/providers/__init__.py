"""Provider protocol and registry."""

from __future__ import annotations

from pathlib import Path

from axism.providers.base import (
    Capabilities,
    ProviderBase,
    SessionProvider,
    UnsupportedOperation,
)
from axism.providers.claude_code import ClaudeCodeProvider, default_provider
from axism.settings import DEFAULT_PROVIDER, Settings, load_settings

# Display names for known backends, in menu order.
PROVIDER_LABELS: dict[str, str] = {
    "claude_code": "Claude Code",
    "cursor": "Cursor",
    "hermes": "Hermes",
}

__all__ = [
    "PROVIDER_LABELS",
    "Capabilities",
    "ClaudeCodeProvider",
    "ProviderBase",
    "SessionProvider",
    "UnsupportedOperation",
    "default_provider",
    "get_provider",
    "list_provider_ids",
    "provider_config_hint",
    "provider_from_settings",
]


def list_provider_ids() -> list[str]:
    """Known provider ids (enabled or not)."""
    return list(PROVIDER_LABELS.keys())


def provider_config_hint(name: str) -> str:
    """Where a backend looks for its data, for Settings placeholders."""
    try:
        return get_provider(name).config_hint
    except KeyError:
        return ""


def get_provider(
    name: str = DEFAULT_PROVIDER,
    *,
    config_dir: Path | str | None = None,
) -> SessionProvider:
    """Instantiate a provider by id."""
    if name == "claude_code":
        return ClaudeCodeProvider(config_dir=config_dir)
    if name == "cursor":
        from axism.providers.cursor import CursorProvider

        return CursorProvider(config_dir=config_dir)
    if name == "hermes":
        from axism.providers.hermes import HermesProvider

        return HermesProvider(config_dir=config_dir)
    raise KeyError(f"unknown provider: {name!r}")


def provider_from_settings(settings: Settings | None = None) -> SessionProvider:
    """Build the active provider from saved (or default) settings.

    Disabled or unknown active providers fall back to Claude Code defaults.
    Each backend resolves its own root from settings ``config_dir`` or its
    own environment variable.
    """
    s = settings if settings is not None else load_settings()
    name = s.active_provider
    prefs = s.provider_prefs(name)
    if not prefs.enabled or name not in PROVIDER_LABELS:
        name = DEFAULT_PROVIDER
        prefs = s.provider_prefs(name)
    return get_provider(name, config_dir=prefs.config_dir)
