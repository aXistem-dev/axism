"""Registry wiring and the shared provider contract."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from axism.live import LiveSession
from axism.providers import (
    PROVIDER_LABELS,
    ProviderBase,
    SessionProvider,
    UnsupportedOperation,
    get_provider,
    list_provider_ids,
    provider_config_hint,
    provider_from_settings,
)
from axism.settings import ProviderPrefs, Settings


def test_every_registered_id_builds_a_provider():
    assert set(list_provider_ids()) == {"claude_code", "cursor", "hermes"}
    for pid in PROVIDER_LABELS:
        provider = get_provider(pid)
        assert provider.name == pid
        assert isinstance(provider, SessionProvider)
        assert provider_config_hint(pid)


def test_unknown_provider_raises():
    with pytest.raises(KeyError):
        get_provider("nope")


def test_settings_select_a_backend(tmp_path: Path):
    cfg = tmp_path / "cursor-home"
    cfg.mkdir()
    settings = Settings(
        active_provider="cursor",
        providers={"cursor": ProviderPrefs(enabled=True, config_dir=str(cfg))},
    )
    provider = provider_from_settings(settings)
    assert provider.name == "cursor"
    assert provider.config_root() == cfg.resolve()


def test_disabled_backend_falls_back_to_claude():
    settings = Settings(
        active_provider="hermes",
        providers={"hermes": ProviderPrefs(enabled=False)},
    )
    assert provider_from_settings(settings).name == "claude_code"


def test_base_refuses_unimplemented_operations():
    class Bare(ProviderBase):
        name = "bare"
        label = "Bare"

    provider = Bare()
    with pytest.raises(UnsupportedOperation, match="Bare does not support"):
        provider.rename("abc", "title")
    with pytest.raises(UnsupportedOperation):
        provider.plan_delete("abc")
    # Non-fatal refusals report instead of raising.
    assert provider.move_sessions([], "/tmp/x").ok is False
    assert provider.stop("abc")[0] is False
    assert provider.purge_project("/tmp/x")[0] == 1


def test_agent_process_match_ignores_axism_itself(monkeypatch: pytest.MonkeyPatch):
    from axism.providers import common

    session_id = "abc123"
    monkeypatch.setattr(
        common,
        "process_table",
        lambda: [
            # aXism carries the id (and backend name) in its own argv.
            (11, f"/opt/venv/bin/axism --provider hermes show {session_id}"),
            (12, f"grep -F {session_id}"),
            (13, f"/opt/hermes/venv/bin/hermes --resume {session_id}"),
        ],
    )
    assert common.find_agent_sessions({session_id}, {"hermes"}) == {session_id: 13}
    assert common.find_agent_sessions({session_id}, {"cursor-agent"}) == {}
    assert common.find_agent_sessions(set(), {"hermes"}) == {}


def test_stop_gating_is_per_backend():
    background = LiveSession(
        session_id="a", kind="background", state="working", source="filesystem"
    )
    interactive = LiveSession(
        session_id="b",
        kind="interactive",
        state="running",
        pid=os.getpid(),
        source="filesystem",
    )

    claude = get_provider("claude_code")
    assert claude.can_stop(background) is True
    # Claude's /stop has no meaning for an interactive session.
    assert claude.can_stop(interactive) is False
    assert claude.can_stop(None) is False

    # Cursor and Hermes only ever see interactive processes.
    for pid in ("cursor", "hermes"):
        provider = get_provider(pid)
        assert provider.can_stop(interactive) is True
        assert provider.can_stop(None) is False
