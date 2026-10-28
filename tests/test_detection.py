"""Detection of backends that have no recorded choice in settings."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from axism import cli
from axism.providers import PROVIDER_LABELS, get_provider
from axism.providers.base import ProviderBase
from axism.providers.federation import DetectionResult, detect_undecided_providers
from axism.settings import load_settings, settings_path


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    cfg = tmp_path / "axism-config"
    roots = {
        "claude": tmp_path / "claude",
        "cursor": tmp_path / "cursor",
        "hermes": tmp_path / "hermes",
    }
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(roots["claude"]))
    monkeypatch.setenv("CURSOR_DATA_DIR", str(roots["cursor"]))
    monkeypatch.setenv("HERMES_HOME", str(roots["hermes"]))
    for r in roots.values():
        r.mkdir()
    return roots


def _claude_data(root: Path) -> None:
    (root / "projects" / "-home-alice-src-demo").mkdir(parents=True)


def _write_settings(providers: dict[str, bool]) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "active_provider": "claude_code",
                "providers": {
                    k: {"enabled": v, "config_dir": None} for k, v in providers.items()
                },
            }
        ),
        encoding="utf-8",
    )


def _enabled(settings) -> set[str]:
    return {k for k, v in settings.providers.items() if v.enabled}


def test_first_launch_enables_only_detected_and_saves(env) -> None:
    _claude_data(env["claude"])
    (env["hermes"] / "state.db").write_bytes(b"")
    result = detect_undecided_providers()
    assert result.changed
    assert result.found == ["claude_code", "hermes"]
    assert result.missing == ["cursor"]
    assert settings_path().is_file()
    assert _enabled(load_settings()) == {"claude_code", "hermes"}
    assert load_settings().recorded_providers() >= {"claude_code", "cursor", "hermes"}


def test_existing_choice_never_overridden(env) -> None:
    _write_settings({"claude_code": True, "cursor": False, "hermes": False})
    (env["cursor"] / "chats" / "abc").mkdir(parents=True)
    result = detect_undecided_providers()
    assert not result.changed
    assert _enabled(load_settings()) == {"claude_code"}


def test_new_backend_detected_once(env, monkeypatch: pytest.MonkeyPatch) -> None:
    class Stub(ProviderBase):
        name = "stub"
        label = "Stub"

        def __init__(self, config_dir=None) -> None:
            pass

        def detect(self) -> bool:
            return True

    from axism import providers

    monkeypatch.setitem(PROVIDER_LABELS, "stub", "Stub")
    real_get = providers.get_provider
    monkeypatch.setattr(
        providers,
        "get_provider",
        lambda name="claude_code", *, config_dir=None: (
            Stub() if name == "stub" else real_get(name, config_dir=config_dir)
        ),
    )
    _write_settings({"claude_code": True, "cursor": True, "hermes": False})
    first = detect_undecided_providers()
    assert first.changed and first.found == ["stub"]
    assert load_settings().provider_prefs("stub").enabled
    before = settings_path().stat().st_mtime_ns
    second = detect_undecided_providers()
    assert second.changed is False
    assert settings_path().stat().st_mtime_ns == before


def test_nothing_detected_falls_back_to_claude_code(env) -> None:
    result = detect_undecided_providers()
    assert result.found == ["claude_code"]
    assert _enabled(load_settings()) == {"claude_code"}


def test_binary_on_path_counts_as_detected(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        shutil, "which", lambda cmd, *a, **k: "/usr/bin/x" if cmd == "cursor-agent" else None
    )
    result = detect_undecided_providers()
    assert result.found == ["cursor"]
    assert _enabled(load_settings()) == {"cursor"}


def test_message_text() -> None:
    r = DetectionResult(["claude_code", "hermes"], ["cursor"], True)
    assert r.message() == (
        "Found Claude Code, Hermes; Cursor not detected — change in Settings (,)"
    )
    assert DetectionResult(["claude_code"], [], True).message() == (
        "Found Claude Code — change in Settings (,)"
    )


def test_cli_prints_message_once_to_stderr(env, capsys) -> None:
    _claude_data(env["claude"])
    with pytest.raises(SystemExit):
        cli.main(["providers"])
    first = capsys.readouterr().err
    assert first.count("Found Claude Code") == 1
    with pytest.raises(SystemExit):
        cli.main(["providers"])
    assert "Found" not in capsys.readouterr().err


def test_detect_never_raises_on_permission_error(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(self):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "iterdir", boom)
    monkeypatch.setattr(Path, "is_file", boom)
    for pid in ("claude_code", "cursor", "hermes"):
        assert get_provider(pid).detect() is False
    assert detect_undecided_providers(save=False).found == ["claude_code"]
