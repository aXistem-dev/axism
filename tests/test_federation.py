"""Tests for federated multi-provider inventory."""

from __future__ import annotations

from pathlib import Path

from axism.discover import ProjectInfo, SessionMeta
from axism.providers import (
    enabled_providers_from_settings,
    federate_discover,
    federate_merge_live,
    live_for_session,
    tag_provider,
)
from axism.settings import ProviderPrefs, Settings


def test_tag_provider_stamps_projects_and_sessions(tmp_path: Path):
    sess = SessionMeta(
        session_id="abc",
        project_slug="slug",
        cwd="/home/alice/proj",
        title="t",
        first_prompt=None,
        transcript_path=tmp_path / "a.jsonl",
        mtime=1.0,
        size_bytes=10,
    )
    proj = ProjectInfo(
        slug="slug",
        cwd_guess="/home/alice/proj",
        path=tmp_path,
        sessions=[sess],
    )
    tag_provider([proj], "cursor")
    assert proj.provider == "cursor"
    assert sess.provider == "cursor"
    assert proj.key == "cursor:slug"
    assert sess.key == "cursor:abc"


def test_enabled_providers_respects_settings(tmp_path: Path):
    cursor_home = tmp_path / "cursor"
    hermes_home = tmp_path / "hermes"
    cursor_home.mkdir()
    hermes_home.mkdir()
    settings = Settings(
        active_provider="cursor",
        providers={
            "claude_code": ProviderPrefs(enabled=False),
            "cursor": ProviderPrefs(enabled=True, config_dir=str(cursor_home)),
            "hermes": ProviderPrefs(enabled=True, config_dir=str(hermes_home)),
        },
    )
    providers = enabled_providers_from_settings(settings)
    assert [p.name for p in providers] == ["cursor", "hermes"]


def test_federate_discover_merges_and_tags(tmp_path: Path, monkeypatch):
    class Fake:
        def __init__(self, name: str, projects: list[ProjectInfo]):
            self.name = name
            self.label = name
            self._projects = projects

        def config_root(self) -> Path:
            return tmp_path / self.name

        def discover(self, root=None):
            return list(self._projects)

        def merge_live(self, root=None, *, use_cli: bool = True):
            return {}

    p1 = ProjectInfo(
        slug="a",
        cwd_guess="/home/alice/a",
        path=tmp_path / "a",
        sessions=[
            SessionMeta(
                session_id="s1",
                project_slug="a",
                cwd="/home/alice/a",
                title="one",
                first_prompt=None,
                transcript_path=tmp_path / "s1",
                mtime=100.0,
                size_bytes=1,
            )
        ],
    )
    p2 = ProjectInfo(
        slug="a",
        cwd_guess="/home/alice/a",
        path=tmp_path / "b",
        sessions=[
            SessionMeta(
                session_id="s1",
                project_slug="a",
                cwd="/home/alice/a",
                title="two",
                first_prompt=None,
                transcript_path=tmp_path / "s2",
                mtime=50.0,
                size_bytes=2,
            )
        ],
    )
    providers = [Fake("claude_code", [p1]), Fake("cursor", [p2])]
    merged = federate_discover(providers)  # type: ignore[arg-type]
    assert len(merged) == 2
    assert {m.provider for m in merged} == {"claude_code", "cursor"}
    assert merged[0].provider == "claude_code"  # newer mtime first
    keys = {m.key for m in merged}
    assert keys == {"claude_code:a", "cursor:a"}

    live = federate_merge_live(providers)  # type: ignore[arg-type]
    assert live == {}

    # live_for_session falls back to bare id when needed
    sess = merged[0].sessions[0]
    assert live_for_session({}, sess) is None


def test_fallback_when_all_disabled():
    settings = Settings(
        active_provider="cursor",
        providers={
            "claude_code": ProviderPrefs(enabled=False),
            "cursor": ProviderPrefs(enabled=False),
            "hermes": ProviderPrefs(enabled=False),
        },
    )
    providers = enabled_providers_from_settings(settings)
    assert len(providers) == 1
    assert providers[0].name == "claude_code"
