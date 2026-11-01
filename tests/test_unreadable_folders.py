"""An agent folder that cannot be read skips that agent with a note instead of failing."""

from __future__ import annotations

import asyncio
import json
import os
import re
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


def _has_note(pid: str, text: str) -> bool:
    """The skip note for the whole agent, or for one of its projects."""
    label = re.escape(LABELS[pid])
    return re.search(rf"{label}( project [^:]+)?: folder unreadable — skipped", text) is not None


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
    assert _has_note(pid, err), err


@pytest.mark.parametrize("locked", CASES, indirect=True)
def test_list_json_with_only_an_unreadable_agent_exits_one(locked, tmp_path, capsys, request):
    pid, root = locked
    _settings(tmp_path, {pid: root})
    # A skipped project still leaves its agent readable; only a whole agent fails.
    expected = 0 if request.node.callspec.params["locked"].endswith("_project") else 1
    assert _run(["list", "--json"]) == expected
    out, _err = capsys.readouterr()
    assert json.loads(out) == []


@pytest.mark.parametrize("locked", CASES, indirect=True)
def test_list_one_agent_with_an_unreadable_folder(locked, tmp_path, capsys, request):
    pid, root = locked
    _settings(tmp_path, {"claude_code": tmp_path / "claude-none"})
    # Only an agent that cannot be read at all fails the listing; the note says why.
    expected = 0 if request.node.callspec.params["locked"].endswith("_project") else 1
    assert _run(["--provider", pid, "--config-dir", str(root), "list"]) == expected
    _out, err = capsys.readouterr()
    assert "No projects found" in err
    assert _has_note(pid, err), err


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
            assert _has_note(pid, toast), toast
            await pilot.press("r")
            await pilot.pause()
            toast = str(app.query_one("#toast").render())
            assert _has_note(pid, toast), toast

    asyncio.run(main())


@pytest.mark.parametrize(
    "argv",
    [
        ["resume", "abcdef12", "--print-only"],
        ["delete", "abcdef12", "--dry-run"],
        ["move", "abcdef12", "--to", "/tmp/elsewhere", "--dry-run"],
    ],
)
def test_session_commands_report_an_unreadable_folder_without_a_traceback(
    argv, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(tmp_path / "axcfg"))
    root = tmp_path / "cursor-locked"
    (root / "projects").mkdir(parents=True)
    _settings(tmp_path, {"cursor": root})
    root.chmod(0)
    try:
        rc = _run(["--provider", "cursor", "--config-dir", str(root), *argv])
    finally:
        root.chmod(0o700)
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("axism: "), err
    assert "Permission denied" in err


@pytest.mark.parametrize("case", ["claude_project", "cursor_project"])
def test_an_unreadable_project_is_skipped_on_its_own(case, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(tmp_path / "axcfg"))
    pid, root, gate = _locked_case(tmp_path, case)
    if pid == "claude_code":
        other = root / "projects" / "-home-alice-src-other"
        other.mkdir()
        (other / "22222222-2222-4222-8222-222222222222.jsonl").write_text(
            '{"type":"user","cwd":"/home/alice/src/other","message":{"content":"hi"}}\n',
            encoding="utf-8",
        )
        listed = "22222222-2222-4222-8222-222222222222"
    else:
        conv = root / "projects" / "p2" / "agent-transcripts" / "c0ffee00-0000-4000-8000-000000000000"
        conv.mkdir(parents=True)
        (conv / "c0ffee00-0000-4000-8000-000000000000.jsonl").write_text(
            '{"role":"user","message":{"content":[{"type":"text","text":"hi"}]}}\n',
            encoding="utf-8",
        )
        listed = "c0ffee00-0000-4000-8000-000000000000"
    _settings(tmp_path, {pid: root})
    gate.chmod(0)
    try:
        assert _run(["list"]) == 0
    finally:
        gate.chmod(0o700)
    out, err = capsys.readouterr()
    assert listed in out
    assert f"{LABELS[pid]}: folder unreadable" not in err, err
    assert f"{LABELS[pid]} project " in err and "folder unreadable — skipped" in err, err


def test_opencode_with_an_unreadable_data_folder_gets_the_skip_note(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(tmp_path / "axcfg"))
    pid, root, gate = _locked_case(tmp_path, "opencode_root")
    _settings(tmp_path, {pid: root, "claude_code": build_fake_config(tmp_path / "claude-ok")})
    gate.chmod(0)
    try:
        assert _run(["list"]) == 0
    finally:
        gate.chmod(0o700)
    assert "OpenCode: folder unreadable — skipped" in capsys.readouterr().err


def test_an_unreadable_opencode_database_file_is_reported_as_unreadable(tmp_path, monkeypatch):
    from axism.providers.opencode import OpenCodeProvider

    root = tmp_path / "opencode"
    root.mkdir()
    db = root / "opencode.db"
    db.write_bytes(b"")
    db.chmod(0)
    try:
        note = OpenCodeProvider(root).status_note()
    finally:
        db.chmod(0o600)
    assert note == "database unreadable", note


def test_an_empty_listing_of_one_agent_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(tmp_path / "axcfg"))
    root = tmp_path / "cursor-empty"
    (root / "projects").mkdir(parents=True)
    _settings(tmp_path, {"cursor": root})
    assert _run(["--provider", "cursor", "--config-dir", str(root), "list"]) == 0
    assert _run(["list"]) == 0
    assert "No projects found" in capsys.readouterr().err


def test_an_unreadable_project_is_refused_by_move_and_delete_project(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(tmp_path / "axcfg"))
    pid, root, gate = _locked_case(tmp_path, "cursor_project")
    _settings(tmp_path, {pid: root})
    base = ["--provider", "cursor", "--config-dir", str(root)]
    gate.chmod(0)
    try:
        moved = _run([*base, "move", "--project", "p1", "--to", str(tmp_path / "x")])
        deleted = _run([*base, "delete-project", "p1", "--yes"])
    finally:
        gate.chmod(0o700)
    assert moved == 1
    assert deleted == 2
    err = capsys.readouterr().err
    assert "project folder unreadable: p1" in err, err
    assert gate.is_dir()


def test_an_unreadable_claude_project_is_never_moved_or_deleted(tmp_path, monkeypatch):
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(tmp_path / "axcfg"))
    pid, root, gate = _locked_case(tmp_path, "claude_project")
    _settings(tmp_path, {pid: root})
    base = ["--provider", pid, "--config-dir", str(root)]
    cwd = "/home/alice/src/demo"
    gate.chmod(0)
    try:
        assert _run([*base, "move", "--project", cwd, "--to", str(tmp_path / "x")]) == 1
        assert _run([*base, "delete-project", "--yes", "--", gate.name]) != 0
        assert _run([*base, "delete-project", cwd, "--yes"]) != 0
    finally:
        gate.chmod(0o700)
    assert gate.is_dir()


def _cursor_workspace(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """(root, workspace, transcripts dir, CLI chat bucket) with a chat in each store."""
    from axism.providers.cursor_store.paths import cwd_bucket, slugify_path
    from tests.test_cursor_provider import _write_cli_chat, _write_transcript

    workspace = tmp_path / "work" / "demo"
    workspace.mkdir(parents=True)
    root = tmp_path / "cursor"
    transcripts = root / "projects" / slugify_path(workspace) / "agent-transcripts"
    _write_transcript(transcripts / "aaaaaaaa-0000-4000-8000-000000000001",
                      "aaaaaaaa-0000-4000-8000-000000000001", "hi")
    bucket = root / "chats" / cwd_bucket(workspace)
    _write_cli_chat(bucket / "bbbbbbbb-0000-4000-8000-000000000002", "CLI chat")
    return root, workspace, transcripts, bucket


@pytest.mark.parametrize("half", ["transcripts", "bucket"])
def test_a_half_unreadable_cursor_workspace_is_skipped_whole_and_never_moved(half, tmp_path):
    from axism.discover import ProjectInfo
    from axism.providers.cursor import CursorProvider
    from axism.providers.cursor_store.paths import slugify_path

    root, workspace, transcripts, bucket = _cursor_workspace(tmp_path)
    gate = transcripts if half == "transcripts" else bucket
    prov = CursorProvider(config_dir=root)
    slug = slugify_path(workspace)
    gate.chmod(0)
    try:
        found = [p for p in prov.discover() if p.slug == slug]
        assert len(found) == 1 and found[0].unreadable and not found[0].sessions
        # Even handed a project that looks readable, the move refuses.
        looks_ok = ProjectInfo(slug=slug, cwd_guess=str(workspace), path=root / "projects" / slug)
        result = prov.move_projects([looks_ok], str(tmp_path / "elsewhere"))
    finally:
        gate.chmod(0o700)
    assert not result.ok
    assert "unreadable" in result.message, result.message
    assert gate.is_dir()


def test_sessions_are_never_moved_into_an_unreadable_claude_project(tmp_path):
    from axism.providers.claude_code import ClaudeCodeProvider

    root = build_fake_config(tmp_path / "claude")
    locked = root / "projects" / "-home-alice-src-locked"
    locked.mkdir()
    prov = ClaudeCodeProvider(config_dir=root)
    sessions = [s for p in prov.discover() for s in p.sessions]
    before = [s.transcript_path for s in sessions]
    locked.chmod(0)
    try:
        result = prov.move_sessions(sessions[:1], "/home/alice/src/locked")
    finally:
        locked.chmod(0o700)
    assert not result.ok
    assert "destination folder unreadable" in result.message, result.message
    assert before[0].is_file()


def test_sessions_are_never_moved_into_an_unreadable_cursor_workspace(tmp_path):
    from axism.providers.cursor import CursorProvider
    from axism.providers.cursor_store.paths import slugify_path

    root, _workspace, transcripts, _bucket = _cursor_workspace(tmp_path)
    other = tmp_path / "work" / "locked"
    other.mkdir()
    dest_dir = root / "projects" / slugify_path(other)
    (dest_dir / "agent-transcripts").mkdir(parents=True)
    prov = CursorProvider(config_dir=root)
    sessions = [s for p in prov.discover() for s in p.sessions]
    dest_dir.chmod(0)
    try:
        result = prov.move_sessions(sessions, str(other))
    finally:
        dest_dir.chmod(0o700)
    assert not result.ok
    assert "destination folder unreadable" in result.message, result.message
    assert any(transcripts.iterdir())


def test_unreadable_folders_are_noted_where_is_dir_hides_permission_errors(
    tmp_path, monkeypatch, capsys
):
    """Newer Pythons answer ``is_dir()`` False on EACCES instead of raising."""
    import pathlib

    real = pathlib.Path.is_dir

    def quiet_is_dir(self, *args, **kwargs):
        try:
            return real(self, *args, **kwargs)
        except OSError:
            return False

    monkeypatch.setattr(pathlib.Path, "is_dir", quiet_is_dir)
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(tmp_path / "axcfg"))
    for case in ("cursor_project", "cursor_root", "claude_project"):
        pid, root, gate = _locked_case(tmp_path / case, case)
        _settings(tmp_path, {pid: root})
        gate.chmod(0)
        try:
            _run(["list"])
        finally:
            gate.chmod(0o700)
        assert _has_note(pid, capsys.readouterr().err), case


def test_claude_delete_project_blocks_an_unreadable_project(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(tmp_path / "axcfg"))
    pid, root, gate = _locked_case(tmp_path, "claude_project")
    _settings(tmp_path, {pid: root})
    gate.chmod(0)
    try:
        rc = _run(["--provider", pid, "--config-dir", str(root),
                   "delete-project", "--yes", "/home/alice/src/demo"])
    finally:
        gate.chmod(0o700)
    assert rc == 2
    assert "project folder unreadable" in capsys.readouterr().err
    assert gate.is_dir()


def test_list_exit_code_ignores_a_failed_live_lookup(tmp_path, monkeypatch, capsys):
    from axism.providers.claude_code import ClaudeCodeProvider

    monkeypatch.setenv("AXISM_CONFIG_DIR", str(tmp_path / "axcfg"))
    root = build_fake_config(tmp_path / "claude")
    _settings(tmp_path, {"claude_code": root})

    def boom(self, *a, **k):
        raise PermissionError(13, "Permission denied", str(root))

    monkeypatch.setattr(ClaudeCodeProvider, "merge_live", boom)
    assert _run(["list"]) == 0
    assert _run(["--provider", "claude_code", "--config-dir", str(root), "list"]) == 0
