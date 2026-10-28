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
    assert result.missing == ["cursor", "opencode"]
    assert settings_path().is_file()
    assert _enabled(load_settings()) == {"claude_code", "hermes"}
    assert load_settings().recorded_providers() >= {
        "claude_code",
        "cursor",
        "hermes",
        "opencode",
    }


def test_existing_choice_never_overridden(env) -> None:
    _write_settings(
        {"claude_code": True, "cursor": False, "hermes": False, "opencode": False}
    )
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
    assert result.found == []
    assert result.message() == (
        "No agents found — showing Claude Code; change in Settings (,)"
    )
    assert _enabled(load_settings()) == {"claude_code"}


def test_upgrade_with_agents_enabled_names_only_the_missing_one(env) -> None:
    _write_settings({"claude_code": True, "cursor": False, "hermes": True})
    result = detect_undecided_providers()
    assert result.changed and result.found == [] and result.missing == ["opencode"]
    assert result.message() == "OpenCode not detected — change in Settings (,)"
    assert _enabled(load_settings()) == {"claude_code", "hermes"}


def test_upgrade_with_new_agent_found(env, tmp_path: Path, monkeypatch) -> None:
    from tests.opencode_fixtures import make_opencode_db

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    make_opencode_db(tmp_path / "xdg" / "opencode", sessions=[], projects=[], messages=[])
    _write_settings({"claude_code": True, "hermes": False})
    result = detect_undecided_providers()
    assert result.found == ["opencode"] and result.missing == ["cursor"]
    assert result.message() == (
        "Found OpenCode; Cursor not detected — change in Settings (,)"
    )


def test_claude_code_recorded_off_nothing_detected_is_fallback(env) -> None:
    _write_settings({"claude_code": False, "cursor": False})
    result = detect_undecided_providers()
    assert result.found == []
    assert "Claude Code" not in result.message().split("showing")[0]
    assert result.message() == (
        "No agents found — showing Claude Code; change in Settings (,)"
    )
    assert _enabled(load_settings()) == set()


def test_fallback_claude_code_is_not_listed_as_found(env) -> None:
    _write_settings({"cursor": False})
    result = detect_undecided_providers()
    assert "claude_code" not in result.found
    assert result.message().startswith("No agents found")
    assert _enabled(load_settings()) == {"claude_code"}


_BROKEN = '{"active_provider": "hermes", "providers": {"hermes": {"config_dir": "/home/alice/h"'


@pytest.mark.parametrize(
    "content",
    [_BROKEN.encode(), b"\xff\xfe not utf-8", b"[1, 2]"],
    ids=["syntax-error", "not-utf8", "not-an-object"],
)
def test_unreadable_settings_are_never_overwritten(env, content: bytes) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    _claude_data(env["claude"])
    result = detect_undecided_providers()
    assert result.changed and result.found == ["claude_code"]
    assert result.message().endswith(" (settings.json could not be read; not saved)")
    assert path.read_bytes() == content


def test_settings_path_that_is_a_directory_is_not_saved(env) -> None:
    path = settings_path()
    path.mkdir(parents=True)
    result = detect_undecided_providers()
    assert "(settings.json could not be read; not saved)" in result.message()
    assert "could not save" not in result.message()
    assert path.is_dir()


def test_cli_keeps_malformed_settings_and_uses_detected_values(env, capsys) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_BROKEN, encoding="utf-8")
    _claude_data(env["claude"])
    with pytest.raises(SystemExit):
        cli.main(["list"])
    captured = capsys.readouterr()
    assert "(settings.json could not be read; not saved)" in captured.err
    assert "# providers: Claude Code\n" in captured.out
    assert path.read_text(encoding="utf-8") == _BROKEN


def test_cli_with_unwritable_settings_uses_detected_values(
    env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(blocker / "cfg"))
    _claude_data(env["claude"])
    with pytest.raises(SystemExit):
        cli.main(["list"])
    assert "# providers: Claude Code\n" in capsys.readouterr().out


def test_cli_tui_subcommand_prints_no_detection_line(
    env, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    import axism.tui.app as tui_app

    _claude_data(env["claude"])
    monkeypatch.setattr(tui_app, "run_tui", lambda: None)
    with pytest.raises(SystemExit):
        cli.main(["tui"])
    assert capsys.readouterr().err == ""
    assert not settings_path().exists()


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
    result = detect_undecided_providers(save=False)
    assert result.found == [] and result.fallback


def test_unwritable_config_dir_keeps_result_and_notes_it(
    env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(blocker / "cfg"))
    _claude_data(env["claude"])
    result = detect_undecided_providers()
    assert result.changed
    assert result.found == ["claude_code"]
    assert result.save_error
    assert "(could not save settings: " in result.message()


def test_cli_runs_with_unwritable_config_dir(
    env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(blocker / "cfg"))
    _claude_data(env["claude"])
    with pytest.raises(SystemExit) as exc:
        cli.main(["list"])
    assert exc.value.code == 0
    assert "could not save settings" in capsys.readouterr().err


def test_nothing_detected_with_claude_code_off_says_fallback(env) -> None:
    _write_settings({"claude_code": False})
    result = detect_undecided_providers()
    assert result.found == []
    assert result.message() == (
        "No agents found — showing Claude Code; change in Settings (,)"
    )
