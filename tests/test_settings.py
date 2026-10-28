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


def test_load_non_utf8_file_gives_defaults(tmp_path: Path):
    bad = tmp_path / "settings.json"
    bad.write_bytes(b"\xff\xfe\x00{")
    assert load_settings(bad).active_provider == DEFAULT_PROVIDER


def test_enabled_accepts_only_real_booleans():
    from axism.settings import Settings

    def enabled(value: object) -> bool:
        s = Settings.from_dict(
            {"providers": {"cursor": {"enabled": value}}}
        )
        return s.provider_prefs("cursor").enabled

    assert enabled(False) is False
    assert enabled(True) is True
    assert enabled("false") is False
    assert enabled("true") is True
    assert enabled("False") is False
    assert enabled(0) is True
    assert enabled(None) is True
    assert enabled([]) is True
    assert enabled("nonsense") is True


import pytest


@pytest.mark.parametrize(
    "content",
    [
        '{"providers": []}',
        '{"providers": "cursor"}',
        '{"providers": {"cursor": true}}',
        '{"providers": {"cursor": {"enabled": false}, "hermes": null}}',
    ],
)
def test_wrongly_typed_providers_count_as_unreadable(tmp_path: Path, content: str):
    path = tmp_path / "settings.json"
    path.write_text(content, encoding="utf-8")
    assert load_settings(path).load_error()


def test_well_typed_or_absent_providers_are_readable(tmp_path: Path):
    path = tmp_path / "settings.json"
    for content in ('{}', '{"providers": null}', '{"providers": {"cursor": {}}}'):
        path.write_text(content, encoding="utf-8")
        assert load_settings(path).load_error() == ""
