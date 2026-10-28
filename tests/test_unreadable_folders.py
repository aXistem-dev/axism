"""An agent folder that cannot be read skips that agent with a note instead of failing."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from axism import cli
from axism.tui.app import SessionManagerApp
from tests.conftest import DEMO_SLUG, build_fake_config
from tests.test_hermes_provider import TUI_SESSION, _make_state_db

pytestmark = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="permissions do not apply to root",
)

LABELS = {
    "claude_code": "Claude Code",
    "cursor": "Cursor",
    "hermes": "Hermes",
    "opencode": "OpenCode",
}


def _run(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    code = exc.value.code
    return 0 if code is None else int(code)


def _locked_case(tmp_path: Path, case: str) -> tuple[str, Path, Path]:
    """``(agent id, its folder, the directory to lock)`` for one unreadable layout."""
    if case == "claude_project":
        root = build_fake_config(tmp_path / "claude-locked")
        return "claude_code", root, root / "projects" / DEMO_SLUG
    if case == "cursor_root":
        root = tmp_path / "cursor-locked"
        (root / "projects").mkdir(parents=True)
        return "cursor", root, root
    if case == "cursor_project":
        root = tmp_path / "cursor-locked"
        (root / "projects" / "p1" / "agent-transcripts").mkdir(parents=True)
        return "cursor", root, root / "projects" / "p1"
    if case == "hermes_root":
        root = tmp_path / "hermes-locked"
        root.mkdir()
        (root / "config.yaml").write_text("{}\n", encoding="utf-8")
        return "hermes", root, root
    if case == "opencode_root":
        root = tmp_path / "opencode"
        root.mkdir()
        return "opencode", root, root
    raise AssertionError(case)


def _readable(tmp_path: Path, pid: str) -> tuple[dict[str, Path], str]:
    """Another agent that stays readable next to ``pid``, and a session id it lists."""
    if pid == "claude_code":
        root = tmp_path / "hermes-ok"
        root.mkdir()
        work = tmp_path / "work" / "demo"
        work.mkdir(parents=True)
        _make_state_db(root / "state.db", str(work), str(tmp_path / "work"))
        return {"hermes": root}, TUI_SESSION
    return {"claude_code": build_fake_config(tmp_path / "claude-ok")}, "11111111-1111"


CASES = ["claude_project", "cursor_root", "cursor_project", "hermes_root", "opencode_root"]


def _settings(tmp_path: Path, enabled: dict[str, Path]) -> None:
    cfg = tmp_path / "axcfg"
    cfg.mkdir(exist_ok=True)
    providers = {
        pid: {"enabled": pid in enabled, "config_dir": str(enabled.get(pid, tmp_path / pid))}
        for pid in LABELS
    }
    (cfg / "settings.json").write_text(
        json.dumps({"active_provider": "claude_code", "providers": providers}),
        encoding="utf-8",
    )


@pytest.fixture
def locked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request):
    pid, root, gate = _locked_case(tmp_path, request.param)
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(tmp_path / "axcfg"))
    gate.chmod(0)
    try:
        yield pid, root
    finally:
        gate.chmod(0o700)


@pytest.mark.parametrize("locked", CASES, indirect=True)
def test_list_skips_an_unreadable_agent_with_a_note(locked, tmp_path, capsys):
    pid, root = locked
    others, listed = _readable(tmp_path, pid)
    _settings(tmp_path, {pid: root, **others})
    assert _run(["list"]) == 0
    out, err = capsys.readouterr()
    assert listed in out
    if pid != "opencode":
        # OpenCode reports an unreadable data folder as an unsupported layout itself.
        assert f"{LABELS[pid]}: folder unreadable — skipped" in err, err


@pytest.mark.parametrize("locked", CASES, indirect=True)
def test_list_json_with_an_unreadable_agent_exits_zero(locked, tmp_path, capsys):
    pid, root = locked
    _settings(tmp_path, {pid: root})
    assert _run(["list", "--json"]) == 0
    out, _err = capsys.readouterr()
    assert json.loads(out) == []


@pytest.mark.parametrize("locked", CASES, indirect=True)
def test_list_one_agent_with_an_unreadable_folder(locked, tmp_path, capsys):
    pid, root = locked
    _settings(tmp_path, {"claude_code": tmp_path / "claude-none"})
    # Nothing to list exits 1 as for any empty listing; the note says why.
    assert _run(["--provider", pid, "--config-dir", str(root), "list"]) == 1
    _out, err = capsys.readouterr()
    assert "No projects found" in err
    if pid != "opencode":
        assert f"{LABELS[pid]}: folder unreadable — skipped" in err, err


@pytest.mark.parametrize("locked", CASES, indirect=True)
def test_tui_mounts_and_notes_an_unreadable_agent(locked, tmp_path):
    pid, root = locked
    others, _listed = _readable(tmp_path, pid)
    _settings(tmp_path, {pid: root, **others})
    other = next(iter(others))

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert any(p.provider == other for p in app.projects)
            assert not any(p.provider == pid for p in app.projects)
            toast = str(app.query_one("#toast").render())
            if pid != "opencode":
                assert f"{LABELS[pid]}: folder unreadable — skipped" in toast, toast
            await pilot.press("r")
            await pilot.pause()
            if pid != "opencode":
                toast = str(app.query_one("#toast").render())
                assert f"{LABELS[pid]}: folder unreadable — skipped" in toast, toast

    asyncio.run(main())
