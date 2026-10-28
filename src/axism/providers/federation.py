"""Combine inventories from every enabled SessionProvider."""

from __future__ import annotations

from dataclasses import dataclass

from axism.discover import ProjectInfo, SessionMeta
from axism.live import LiveSession
from axism.providers.base import SessionProvider
from axism.settings import DEFAULT_PROVIDER, Settings, load_settings, save_settings


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


@dataclass
class DetectionResult:
    """Outcome of detecting backends that had no recorded choice."""

    found: list[str]
    missing: list[str]
    changed: bool
    save_error: str = ""
    # Nothing is enabled, so Claude Code is shown as the fallback.
    fallback: bool = False
    # settings.json exists but could not be read, so nothing was saved.
    unreadable: bool = False

    def message(self) -> str:
        from axism.providers import PROVIDER_LABELS

        def labels(ids: list[str]) -> str:
            return ", ".join(PROVIDER_LABELS.get(i, i) for i in ids)

        settings_hint = "change in Settings (,)"
        if self.fallback:
            text = f"No agents found \u2014 showing Claude Code; {settings_hint}"
        elif self.found:
            text = f"Found {labels(self.found)}"
            if self.missing:
                text += f"; {labels(self.missing)} not detected"
            text += f" \u2014 {settings_hint}"
        else:
            text = f"{labels(self.missing)} not detected \u2014 {settings_hint}"
        if self.unreadable:
            text += " (settings.json could not be read; not saved)"
        elif self.save_error:
            text += f" (could not save settings: {self.save_error})"
        return text


def detect_undecided_providers(
    settings: Settings | None = None, *, save: bool = True
) -> DetectionResult:
    """Switch backends with no recorded choice on or off by detecting them.

    Recorded choices are never modified. Falls back to Claude Code when
    nothing ends up enabled. Saves only when something changed, and never
    over a settings file that exists but could not be read.
    """
    from axism.providers import get_provider, list_provider_ids

    s = settings if settings is not None else load_settings()
    recorded = s.recorded_providers()
    undecided = [pid for pid in list_provider_ids() if pid not in recorded]
    if not undecided:
        return DetectionResult([], [], False)
    found: list[str] = []
    missing: list[str] = []
    for pid in undecided:
        prefs = s.provider_prefs(pid)
        try:
            detected = bool(get_provider(pid, config_dir=prefs.config_dir).detect())
        except Exception:
            detected = False
        prefs.enabled = detected
        (found if detected else missing).append(pid)
    fallback = not any(s.provider_prefs(pid).enabled for pid in list_provider_ids())
    if fallback and DEFAULT_PROVIDER in undecided:
        s.provider_prefs(DEFAULT_PROVIDER).enabled = True
    unreadable = bool(s.load_error())
    save_error = ""
    if save and not unreadable:
        try:
            save_settings(s)
        except OSError as exc:
            save_error = exc.strerror or type(exc).__name__
    s.mark_recorded(set(undecided))
    return DetectionResult(
        found, missing, True, save_error, fallback=fallback, unreadable=unreadable
    )


UNREADABLE_SKIPPED = "folder unreadable \u2014 skipped"


def _skip(skipped: list[str] | None, provider: SessionProvider) -> None:
    if skipped is not None and provider.label not in skipped:
        skipped.append(provider.label)


def skipped_note(skipped: list[str]) -> str:
    """One line naming each agent whose folder could not be read (plain text)."""
    return "; ".join(f"{label}: {UNREADABLE_SKIPPED}" for label in skipped)


def federate_discover(
    providers: list[SessionProvider], *, skipped: list[str] | None = None
) -> list[ProjectInfo]:
    """Discover all projects from ``providers``, tagged and sorted by recency.

    A provider whose folder cannot be read (``OSError``) is left out and its
    label appended to ``skipped``.
    """
    projects: list[ProjectInfo] = []
    for provider in providers:
        try:
            found = provider.discover(provider.config_root())
        except OSError:
            _skip(skipped, provider)
            continue
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
    skipped: list[str] | None = None,
) -> dict[str, LiveSession]:
    """Merge live maps; keys are ``provider:session_id``.

    A provider whose folder cannot be read (``OSError``) adds no live records
    and its label is appended to ``skipped``.
    """
    live: dict[str, LiveSession] = {}
    for provider in providers:
        root = provider.config_root()
        try:
            records = provider.merge_live(root, use_cli=use_cli)
        except OSError:
            _skip(skipped, provider)
            continue
        for session_id, record in records.items():
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
