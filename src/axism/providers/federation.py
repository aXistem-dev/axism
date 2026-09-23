"""Combine inventories from every enabled SessionProvider."""

from __future__ import annotations

from axism.discover import ProjectInfo, SessionMeta
from axism.live import LiveSession
from axism.providers.base import SessionProvider
from axism.settings import DEFAULT_PROVIDER, Settings, load_settings


def tag_provider(projects: list[ProjectInfo], provider_name: str) -> list[ProjectInfo]:
    """Stamp ``provider`` on each project and its sessions (in place)."""
    for project in projects:
        project.provider = provider_name
        for session in project.sessions:
            session.provider = provider_name
    return projects


def enabled_providers_from_settings(
    settings: Settings | None = None,
) -> list[SessionProvider]:
    """Instantiate every enabled backend from settings (registry order).

    If none are enabled, fall back to the default Claude Code provider so the
    TUI always has something to show.
    """
    from axism.providers import get_provider, list_provider_ids

    s = settings if settings is not None else load_settings()
    out: list[SessionProvider] = []
    for name in list_provider_ids():
        prefs = s.provider_prefs(name)
        if not prefs.enabled:
            continue
        try:
            out.append(get_provider(name, config_dir=prefs.config_dir))
        except KeyError:
            continue
    if not out:
        prefs = s.provider_prefs(DEFAULT_PROVIDER)
        out.append(get_provider(DEFAULT_PROVIDER, config_dir=prefs.config_dir))
    return out


def federate_discover(providers: list[SessionProvider]) -> list[ProjectInfo]:
    """Discover all projects from ``providers``, tagged and sorted by recency."""
    projects: list[ProjectInfo] = []
    for provider in providers:
        found = provider.discover(provider.config_root())
        tag_provider(found, provider.name)
        projects.extend(found)
    projects.sort(
        key=lambda p: max((s.mtime for s in p.sessions), default=0),
        reverse=True,
    )
    return projects


def federate_merge_live(
    providers: list[SessionProvider],
    *,
    use_cli: bool = True,
) -> dict[str, LiveSession]:
    """Merge live maps; keys are ``provider:session_id``."""
    live: dict[str, LiveSession] = {}
    for provider in providers:
        root = provider.config_root()
        for session_id, record in provider.merge_live(root, use_cli=use_cli).items():
            live[f"{provider.name}:{session_id}"] = record
    return live


def provider_by_name(
    providers: list[SessionProvider], name: str | None
) -> SessionProvider | None:
    if not name:
        return None
    for provider in providers:
        if provider.name == name:
            return provider
    return None


def live_for_session(
    live: dict[str, LiveSession], session: SessionMeta
) -> LiveSession | None:
    """Look up a live record under the federated or bare session key."""
    hit = live.get(session.key)
    if hit is not None:
        return hit
    return live.get(session.session_id)


def roots_summary(providers: list[SessionProvider]) -> str:
    """Short status string of provider roots."""
    if not providers:
        return ""
    if len(providers) == 1:
        return str(providers[0].config_root())
    return " · ".join(f"{p.name}:{p.config_root()}" for p in providers)
