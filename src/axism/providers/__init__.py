"""Provider protocol and registry."""

from __future__ import annotations

from pathlib import Path

from axism.providers.base import SessionProvider
from axism.providers.claude_code import ClaudeCodeProvider, default_provider
from axism.settings import DEFAULT_PROVIDER, Settings, load_settings

# Factories for known backends. Future providers register here.
PROVIDER_LABELS: dict[str, str] = {
    "claude_code": "Claude Code",
}

__all__ = [
    "PROVIDER_LABELS",
    "ClaudeCodeProvider",
    "SessionProvider",
    "default_provider",
    "get_provider",
    "list_provider_ids",
    "provider_from_settings",
]


def list_provider_ids() -> list[str]:
    """Known provider ids (enabled or not)."""
    return list(PROVIDER_LABELS.keys())


def get_provider(
    name: str = DEFAULT_PROVIDER,
    *,
    config_dir: Path | str | None = None,
) -> SessionProvider:
    """Instantiate a provider by id."""
    if name == "claude_code":
        return ClaudeCodeProvider(config_dir=config_dir)
    raise KeyError(f"unknown provider: {name!r}")


def provider_from_settings(settings: Settings | None = None) -> SessionProvider:
    """Build the active provider from saved (or default) settings.

    Disabled active providers fall back to Claude Code defaults.
    ``$CLAUDE_CONFIG_DIR`` / CLI ``--config-dir`` still win when no
    settings ``config_dir`` is set (via ``ClaudeCodeProvider.config_root``).
    """
    s = settings if settings is not None else load_settings()
    name = s.active_provider
    prefs = s.provider_prefs(name)
    if not prefs.enabled or name not in PROVIDER_LABELS:
        name = DEFAULT_PROVIDER
        prefs = s.provider_prefs(name)
    return get_provider(name, config_dir=prefs.config_dir)
