"""Provider protocol and registry."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from axism.providers.base import (
    Capabilities,
    ProviderBase,
    SessionProvider,
    UnsupportedOperation,
)
from axism.providers.claude_code import ClaudeCodeProvider, default_provider
from axism.providers.federation import (
    DetectionResult,
    detect_undecided_providers,
    enabled_providers_from_settings,
    federate_discover,
    federate_merge_live,
    live_for_session,
    provider_by_name,
    roots_summary,
    skipped_note,
    tag_provider,
)
from axism.settings import DEFAULT_PROVIDER, Settings, load_settings

# Display names for known backends, in menu order.
PROVIDER_LABELS: dict[str, str] = {
    "claude_code": "Claude Code",
    "cursor": "Cursor",
    "hermes": "Hermes",
    "opencode": "OpenCode",
}

__all__ = [
    "PROVIDER_LABELS",
    "Capabilities",
    "ClaudeCodeProvider",
    "DetectionResult",
    "ProviderBase",
    "SessionProvider",
    "UnsupportedOperation",
    "default_provider",
    "default_provider_id",
    "default_refusal",
    "detect_undecided_providers",
    "disable_refusal",
    "enabled_providers_from_settings",
    "federate_discover",
    "federate_merge_live",
    "get_provider",
    "list_provider_ids",
    "live_for_session",
    "provider_by_name",
    "provider_config_hint",
    "provider_from_settings",
    "roots_summary",
    "skipped_note",
    "tag_provider",
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
    if name == "opencode":
        from axism.providers.opencode import OpenCodeProvider

        return OpenCodeProvider(config_dir=config_dir)
    raise KeyError(f"unknown provider: {name!r}")


def default_provider_id(settings: Settings) -> str:
    """The CLI default agent: ``active_provider`` when known and switched on,
    otherwise Claude Code."""
    name = settings.active_provider
    prefs = settings.providers.get(name)
    if name not in PROVIDER_LABELS or (prefs is not None and not prefs.enabled):
        return DEFAULT_PROVIDER
    return name


def disable_refusal(enabled: Mapping[str, bool], default: str, name: str) -> str:
    """Why ``name`` may not be switched off ('' when it may).

    At least one agent stays on, and the CLI default stays on (another agent
    has to become the default first). ``enabled`` maps agent ids to on/off.
    """
    if not enabled.get(name, False):
        return ""
    label = PROVIDER_LABELS.get(name, name)
    if sum(1 for on in enabled.values() if on) <= 1:
        return f"{label} is the only agent switched on; at least one has to stay on"
    if name == default:
        return f"{label} is the CLI default; make another agent the default first"
    return ""


def default_refusal(enabled: Mapping[str, bool], name: str) -> str:
    """Why ``name`` may not become the CLI default ('' when it may)."""
    if enabled.get(name, False):
        return ""
    label = PROVIDER_LABELS.get(name, name)
    return f"{label} is switched off; switch it on before making it the CLI default"


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
