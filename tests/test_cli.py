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
