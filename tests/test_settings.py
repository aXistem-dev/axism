"""Tests for aXism settings persistence and provider wiring."""

from __future__ import annotations

from pathlib import Path

from axism.providers import get_provider, provider_from_settings
from axism.settings import (
    DEFAULT_PROVIDER,
    ProviderPrefs,
    Settings,
    default_settings,
    load_settings,
    save_settings,
)


def test_default_settings_roundtrip(tmp_path: Path):
    path = tmp_path / "settings.json"
    s = default_settings()
    save_settings(s, path)
    loaded = load_settings(path)
    assert loaded.active_provider == DEFAULT_PROVIDER
    assert loaded.provider_prefs().enabled is True
    assert loaded.provider_prefs().config_dir is None


def test_save_custom_config_dir(tmp_path: Path):
    path = tmp_path / "settings.json"
    cfg = tmp_path / "my-claude"
    cfg.mkdir()
    s = Settings(
        active_provider="claude_code",
        providers={
            "claude_code": ProviderPrefs(enabled=True, config_dir=str(cfg)),
        },
    )
    save_settings(s, path)
    loaded = load_settings(path)
    assert loaded.provider_prefs().config_dir == str(cfg)

    provider = provider_from_settings(loaded)
    assert provider.name == "claude_code"
    assert provider.config_root() == cfg.resolve()


def test_get_provider_override(tmp_path: Path):
    cfg = tmp_path / "alt"
    cfg.mkdir()
    p = get_provider("claude_code", config_dir=cfg)
    assert p.config_root() == cfg.resolve()


def test_load_missing_file(tmp_path: Path):
    loaded = load_settings(tmp_path / "nope.json")
    assert loaded.active_provider == DEFAULT_PROVIDER


def test_disabled_falls_back_to_default(tmp_path: Path):
    s = Settings(
        active_provider="claude_code",
        providers={"claude_code": ProviderPrefs(enabled=False, config_dir=None)},
    )
    # Still returns a Claude provider (fallback) rather than raising.
    p = provider_from_settings(s)
    assert p.name == "claude_code"
