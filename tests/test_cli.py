"""CLI behaviour: config-dir fallback, delete exit codes, move liveness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from axism import cli
from axism.live import LiveSession
from axism.paths import encode_project_slug
from tests.conftest import DEMO_CWD, SESSION_A


def _run(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    code = exc.value.code
    return 0 if code is None else int(code)


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "axism-config"
    cfg.mkdir()
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(cfg))
    return cfg


def test_config_dir_with_unknown_active_provider_falls_back(
    _isolated_settings: Path, fake_root: Path, monkeypatch: pytest.MonkeyPatch
):
    (_isolated_settings / "settings.json").write_text(
        json.dumps({"active_provider": "codex"}), encoding="utf-8"
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(fake_root))
    assert _run(["--config-dir", str(fake_root), "list"]) == 0
    assert _run(["list", "--config-dir", str(fake_root)]) == 0


def test_delete_without_yes_exits_one(fake_root: Path, capsys):
    rc = _run(["delete", SESSION_A])
    assert rc == 1
    assert "Refusing to delete without --yes" in capsys.readouterr().err


def test_delete_dry_run_exits_zero(fake_root: Path, capsys):
    assert _run(["delete", SESSION_A, "--dry-run"]) == 0
    assert "Refusing" not in capsys.readouterr().err


def test_delete_hard_blocked_exits_two_in_every_mode(fake_root: Path):
    missing = "99999999"
    assert _run(["delete", missing]) == 2
    assert _run(["delete", missing, "--dry-run"]) == 2
    assert _run(["delete", missing, "--yes"]) == 2
    assert _run(["delete", missing, "--dry-run", "--yes"]) == 2
    assert _run(["delete", missing, "--dry-run", "--yes", "--force"]) == 2


def test_delete_yes_succeeds(fake_root: Path):
    assert _run(["delete", SESSION_A, "--yes"]) == 0


def test_delete_project_without_yes_exits_one(fake_root: Path, capsys):
    rc = _run(["delete-project", DEMO_CWD])
    assert rc == 1
    assert "Refusing to delete without --yes" in capsys.readouterr().err
    assert _run(["delete-project", DEMO_CWD, "--dry-run"]) == 0


def test_delete_project_hard_blocked_exits_two_in_every_mode(fake_root: Path):
    missing = "/home/alice/src/nope"
    assert _run(["delete-project", missing]) == 2
    assert _run(["delete-project", missing, "--dry-run"]) == 2
    assert _run(["delete-project", missing, "--yes"]) == 2
    assert _run(["delete-project", missing, "--dry-run", "--yes"]) == 2


def _stub_live(monkeypatch: pytest.MonkeyPatch, session_id: str) -> None:
    from axism.providers.claude_code import ClaudeCodeProvider

    live = LiveSession(
        session_id=session_id, kind="background", state="working", cwd=DEMO_CWD
    )
    monkeypatch.setattr(
        ClaudeCodeProvider, "merge_live", lambda self, **kw: {session_id: live}
    )


def test_move_refuses_live_session_without_force(
    tmp_path: Path, fake_root: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    _stub_live(monkeypatch, SESSION_A)
    dest = str(tmp_path / "other-workspace")
    rc = _run(["move", SESSION_A, "--to", dest])
    assert rc != 0
    assert "live" in capsys.readouterr().err.lower()
    assert not (fake_root / "projects" / encode_project_slug(dest)).exists()


def test_move_live_session_allowed_with_force_and_dry_run_still_checked(
    tmp_path: Path, fake_root: Path, monkeypatch: pytest.MonkeyPatch
):
    _stub_live(monkeypatch, SESSION_A)
    dest = str(tmp_path / "other-workspace")
    assert _run(["move", SESSION_A, "--to", dest, "--dry-run"]) != 0
    assert _run(["move", SESSION_A, "--to", dest, "--force"]) == 0
    assert (fake_root / "projects" / encode_project_slug(dest)).exists()


def test_move_project_refuses_live_session_without_force(
    tmp_path: Path, fake_root: Path, monkeypatch: pytest.MonkeyPatch
):
    _stub_live(monkeypatch, SESSION_A)
    dest = str(tmp_path / "other-workspace")
    assert _run(["move", "--project", DEMO_CWD, "--to", dest]) == 2
    assert (fake_root / "projects" / "-home-alice-src-demo").exists()


def _stub_failed_stop(monkeypatch: pytest.MonkeyPatch, session_id: str) -> None:
    live = LiveSession(
        session_id=session_id, kind="interactive", state="busy", pid=4242, cwd=DEMO_CWD
    )
    monkeypatch.setattr("axism.delete.merge_live", lambda *a, **k: {session_id: live})
    monkeypatch.setattr("axism.live._live_pid", lambda *_a, **_k: True)
    monkeypatch.setattr("axism.live._pid_alive", lambda pid: True)
    monkeypatch.setattr(
        "axism.delete.kill_live_session", lambda *a, **k: (False, "stuck")
    )


def test_delete_yes_exits_one_when_a_step_failed(
    fake_root: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    _stub_failed_stop(monkeypatch, SESSION_A)
    assert _run(["delete", SESSION_A, "--yes", "--force"]) == 1
    assert "aborted" in capsys.readouterr().out
    assert (fake_root / "projects" / "-home-alice-src-demo" / f"{SESSION_A}.jsonl").exists()


def test_delete_project_yes_exits_one_when_a_step_failed(
    fake_root: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    _stub_failed_stop(monkeypatch, SESSION_A)
    assert _run(["delete-project", DEMO_CWD, "--yes", "--force"]) == 1
    assert "aborted" in capsys.readouterr().out
    assert (fake_root / "projects" / "-home-alice-src-demo").is_dir()


def test_delete_project_accepts_a_dash_slug_after_double_dash(fake_root: Path):
    assert _run(["delete-project", "--dry-run", "--", "-home-alice-src-demo"]) == 0
    assert _run(["delete-project", "--yes", "--", "-home-alice-src-demo"]) == 0
    assert not (fake_root / "projects" / "-home-alice-src-demo").exists()


def test_delete_malformed_or_uppercase_uuid_never_exits_zero_empty(fake_root: Path):
    assert _run(["delete", "99999999-9999-9999-9999-99999999999f\n", "--dry-run"]) == 2
    assert _run(["delete", "99999999-9999-9999-9999-99999999999F", "--dry-run"]) == 2
    assert _run(["delete", SESSION_A.upper(), "--dry-run"]) == 0


# ---- providers --default / --enable / --disable, tui --provider / --config-dir ----


def _write_choice(cfg: Path, active: str, enabled: dict[str, bool]) -> Path:
    path = cfg / "settings.json"
    path.write_text(
        json.dumps(
            {
                "active_provider": active,
                "providers": {pid: {"enabled": on} for pid, on in enabled.items()},
            }
        ),
        encoding="utf-8",
    )
    return path


def _saved_choice(path: Path) -> tuple[str, dict[str, bool]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["active_provider"], {
        pid: prefs["enabled"] for pid, prefs in data["providers"].items()
    }


def _line(out: str, pid: str) -> str:
    return next(ln for ln in out.splitlines() if f" {pid} " in ln)


ALL_OFF_BUT_CLAUDE = {"claude_code": True, "cursor": False, "hermes": False, "opencode": False}


def test_providers_default_enable_disable(_isolated_settings: Path, fake_root: Path, capsys):
    path = _write_choice(_isolated_settings, "claude_code", ALL_OFF_BUT_CLAUDE)

    assert _run(["providers", "--enable", "cursor"]) == 0
    out = capsys.readouterr().out
    assert _saved_choice(path) == ("claude_code", {**ALL_OFF_BUT_CLAUDE, "cursor": True})
    assert "enabled" in _line(out, "cursor")
    assert _line(out, "claude_code").startswith("*")
    legend = [ln for ln in out.splitlines() if ln.startswith("* = ")]
    assert legend == ["* = CLI default (used when a command gets no --provider)"]

    assert _run(["providers", "--default", "cursor"]) == 0
    out = capsys.readouterr().out
    assert _saved_choice(path)[0] == "cursor"
    assert _line(out, "cursor").startswith("*")
    assert not _line(out, "claude_code").startswith("*")

    assert _run(["providers", "--disable", "claude_code"]) == 0
    out = capsys.readouterr().out
    assert _saved_choice(path) == ("cursor", {**ALL_OFF_BUT_CLAUDE, "claude_code": False,
                                             "cursor": True})
    assert "disabled" in _line(out, "claude_code")

    # One call can switch an agent on and make it the default.
    assert _run(["providers", "--enable", "hermes", "--default", "hermes"]) == 0
    assert _saved_choice(path)[0] == "hermes"


def test_providers_plain_listing_explains_the_marker(
    _isolated_settings: Path, fake_root: Path, capsys
):
    _write_choice(_isolated_settings, "claude_code", ALL_OFF_BUT_CLAUDE)
    assert _run(["providers"]) == 0
    out = capsys.readouterr().out
    assert sum(ln.startswith("* = ") for ln in out.splitlines()) == 1


def test_providers_marks_the_effective_default_when_the_stored_one_is_off(
    _isolated_settings: Path, fake_root: Path, capsys
):
    # Stored default Hermes is switched off: Claude Code is the CLI default.
    _write_choice(_isolated_settings, "hermes", ALL_OFF_BUT_CLAUDE)
    assert _run(["providers"]) == 0
    out = capsys.readouterr().out
    assert _line(out, "claude_code").startswith("*")
    assert not _line(out, "hermes").startswith("*")


def test_providers_refusals(_isolated_settings: Path, fake_root: Path, capsys):
    path = _write_choice(_isolated_settings, "claude_code", ALL_OFF_BUT_CLAUDE)
    before = path.read_bytes()

    assert _run(["providers", "--default", "codex"]) == 2
    err = capsys.readouterr().err
    assert "unknown agent 'codex'" in err and "claude_code" in err
    assert _run(["providers", "--enable", "codex"]) == 2
    assert _run(["providers", "--disable", "codex"]) == 2

    assert _run(["providers", "--disable", "claude_code"]) == 1
    assert "only agent switched on" in capsys.readouterr().err

    assert _run(["providers", "--default", "cursor"]) == 1
    assert "Cursor is switched off" in capsys.readouterr().err

    assert _run(["providers", "--enable", "cursor", "--disable", "cursor"]) == 2
    capsys.readouterr()
    assert path.read_bytes() == before

    # The default cannot be switched off; another agent must become the default first.
    _write_choice(_isolated_settings, "claude_code", {**ALL_OFF_BUT_CLAUDE, "cursor": True})
    before = path.read_bytes()
    assert _run(["providers", "--disable", "claude_code"]) == 1
    assert "Claude Code is the CLI default" in capsys.readouterr().err
    assert path.read_bytes() == before
    assert _run(["providers", "--default", "cursor", "--disable", "claude_code"]) == 0
    assert _saved_choice(path)[0] == "cursor"

    # An unreadable settings file is never overwritten.
    path.write_text("{not json", encoding="utf-8")
    assert _run(["providers", "--enable", "hermes"]) == 1
    assert "could not be read" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == "{not json"


def test_tui_provider_and_config_dir_apply_for_run_only(
    _isolated_settings: Path,
    fake_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from axism.tui import app as tui_app

    path = _write_choice(
        _isolated_settings,
        "claude_code",
        {"claude_code": True, "cursor": True, "hermes": False, "opencode": False},
    )
    before = path.read_bytes()
    monkeypatch.setenv("CURSOR_DATA_DIR", str(tmp_path / "cursor-home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(fake_root))
    seen: list[tui_app.SessionManagerApp] = []
    monkeypatch.setattr(
        tui_app.SessionManagerApp, "run", lambda self, *a, **k: seen.append(self)
    )
    other = tmp_path / "other-cursor"
    other.mkdir()

    assert _run(["tui", "--provider", "cursor", "--config-dir", str(other)]) == 0
    app = seen[-1]
    assert [p.name for p in app.providers] == ["cursor"]
    assert app.providers[0].config_root() == other.resolve()
    assert app.provider.name == "cursor"

    assert _run(["tui", "--provider", "hermes"]) == 0
    assert [p.name for p in seen[-1].providers] == ["hermes"]

    # Without --provider the folder applies to the CLI default (Claude Code).
    claude_other = tmp_path / "claude-other"
    claude_other.mkdir()
    assert _run(["tui", "--config-dir", str(claude_other)]) == 0
    roots = {p.name: p.config_root() for p in seen[-1].providers}
    assert roots["claude_code"] == claude_other.resolve()
    assert set(roots) == {"claude_code", "cursor"}

    assert path.read_bytes() == before


def test_enabling_an_agent_never_moves_the_stored_default(
    _isolated_settings: Path, fake_root: Path
):
    # The stored default (Cursor) is switched off, so Claude Code is the default.
    path = _write_choice(
        _isolated_settings, "cursor", {**ALL_OFF_BUT_CLAUDE, "cursor": False}
    )
    assert _run(["providers", "--enable", "cursor"]) == 0
    assert _saved_choice(path) == (
        "claude_code",
        {**ALL_OFF_BUT_CLAUDE, "cursor": True},
    )
    assert _run(["providers", "--default", "cursor"]) == 0
    assert _saved_choice(path)[0] == "cursor"


def test_unreadable_refusal_ends_with_the_fix_hint(
    _isolated_settings: Path, fake_root: Path, capsys
):
    (_isolated_settings / "settings.json").write_text("{not json", encoding="utf-8")
    assert _run(["providers", "--enable", "hermes"]) == 1
    err = capsys.readouterr().err.strip()
    assert err.endswith("fix or remove it, then restart aXism")
