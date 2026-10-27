"""Tests for the Hermes provider (default-profile sessions in state.db)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from axism.providers import get_provider
from axism.providers.hermes import HermesProvider
from axism.providers.hermes_store import ops as hermes_ops
from axism.providers.hermes_store.paths import hermes_home, state_db

CLI_SESSION = "20260101_090000_aaaaaa"
TUI_SESSION = "20260102_100000_bbbbbb"
WEBUI_SESSION = "cc11dd22ee33"
CRON_SESSION = "cron_deadbeef_20260103_110000"

SESSION_COLUMNS = (
    "id TEXT PRIMARY KEY",
    "source TEXT",
    "title TEXT",
    "display_name TEXT",
    "cwd TEXT",
    "git_repo_root TEXT",
    "started_at REAL",
    "last_activity_at REAL",
    "message_count INTEGER",
    "archived INTEGER",
    "hidden INTEGER",
    "pinned INTEGER",
    "profile_name TEXT",
)


def _make_state_db(db: Path, workspace: str, repo: str) -> None:
    con = sqlite3.connect(db)
    with con:
        con.execute(f"CREATE TABLE sessions ({', '.join(SESSION_COLUMNS)})")
        con.execute(
            "CREATE TABLE messages ("
            "id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,"
            " timestamp REAL)"
        )
        rows = [
            (CLI_SESSION, "cli", "Build the thing", None, workspace, None,
             100.0, 300.0, 4, 0, 0, 0, "default"),
            (TUI_SESSION, "tui", None, None, workspace, repo,
             110.0, 200.0, 2, 0, 0, 0, "default"),
            (WEBUI_SESSION, "webui", "Browser chat", None, None, None,
             120.0, 150.0, 2, 0, 0, 0, "default"),
            (CRON_SESSION, "cron", None, None, None, None,
             130.0, 400.0, 3, 0, 0, 0, "default"),
            ("archived_one", "cli", "Old chat", None, workspace, None,
             90.0, 95.0, 1, 1, 0, 0, "default"),
            ("hidden_one", "cli", "Hidden chat", None, workspace, None,
             91.0, 96.0, 1, 0, 1, 0, "default"),
        ]
        con.executemany(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        con.executemany(
            "INSERT INTO messages (session_id, role, content, timestamp)"
            " VALUES (?, ?, ?, ?)",
            [
                (CLI_SESSION, "user", "first cli question", 101.0),
                (CLI_SESSION, "assistant", "answer body", 102.0),
                (TUI_SESSION, "user", "  tui   prompt  text ", 111.0),
                (WEBUI_SESSION, "user", "web prompt", 121.0),
                (CRON_SESSION, "user", "cron prompt", 131.0),
            ],
        )
    con.close()


@pytest.fixture
def hermes_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    root = tmp_path / "hermes"
    root.mkdir()
    workspace = tmp_path / "work" / "demo"
    workspace.mkdir(parents=True)
    repo = tmp_path / "work"
    (root / "config.yaml").write_text("{}\n", encoding="utf-8")
    _make_state_db(state_db(root), str(workspace), str(repo))
    monkeypatch.setenv("HERMES_HOME", str(root))
    return root, str(workspace)


def test_home_from_env(hermes_env: tuple[Path, str]):
    root, _ = hermes_env
    assert hermes_home() == root.resolve()
    assert HermesProvider().config_root() == root.resolve()
    assert get_provider("hermes").name == "hermes"


def test_discover_filters_and_groups(hermes_env: tuple[Path, str]):
    _root, workspace = hermes_env
    projects = HermesProvider().discover()
    by_slug = {p.slug: p for p in projects}

    # Cron jobs, archived, and hidden rows are machine traffic or dismissed.
    all_ids = {s.session_id for p in projects for s in p.sessions}
    assert all_ids == {CLI_SESSION, TUI_SESSION, WEBUI_SESSION}

    # git_repo_root wins over cwd, so the TUI session groups by the repo root.
    assert CLI_SESSION in {s.session_id for s in by_slug[workspace].sessions}
    repo_slug = str(Path(workspace).parent)
    assert TUI_SESSION in {s.session_id for s in by_slug[repo_slug].sessions}
    assert WEBUI_SESSION in {s.session_id for s in by_slug["_unscoped"].sessions}


def test_session_metadata(hermes_env: tuple[Path, str]):
    sessions = {
        s.session_id: s for p in HermesProvider().discover() for s in p.sessions
    }
    cli = sessions[CLI_SESSION]
    assert cli.title == "Build the thing"
    assert cli.entrypoint == "cli"
    assert cli.mtime == 300.0
    assert cli.size_bytes == len("first cli question") + len("answer body")

    # No title: fall back to a whitespace-normalised first prompt.
    assert sessions[TUI_SESSION].title is None
    assert sessions[TUI_SESSION].display_title == "tui prompt text"


def test_fragments_protect_the_database(hermes_env: tuple[Path, str]):
    root, _ = hermes_env
    legacy = root / "sessions"
    legacy.mkdir()
    (legacy / f"session_{CLI_SESSION}.json").write_text("{}", encoding="utf-8")

    inv = HermesProvider().collect_fragments(CLI_SESSION)
    kinds = {f.kind for f in inv.fragments}
    assert kinds == {"session_rows", "legacy_json"}

    rows = next(f for f in inv.fragments if f.kind == "session_rows")
    assert rows.is_protected(), "state.db must never be a deletable fragment"
    assert [f.kind for f in inv.deletable] == ["legacy_json"]


def test_delete_uses_the_cli_and_clears_legacy_files(
    hermes_env: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
):
    root, _ = hermes_env
    legacy = root / "sessions"
    legacy.mkdir()
    legacy_file = legacy / f"session_{CLI_SESSION}.json"
    legacy_file.write_text("{}", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(_root: Path, args: list[str], **_kwargs) -> tuple[int, str]:
        calls.append(args)
        return 0, "deleted"

    monkeypatch.setattr(hermes_ops, "run_hermes", fake_run)

    provider = HermesProvider()
    plan = provider.plan_delete(CLI_SESSION)
    assert plan.can_delete

    dry = provider.execute_deletes([plan], dry_run=True)
    assert any("would run hermes sessions delete" in line for line in dry)
    assert calls == []
    assert legacy_file.exists()

    provider.execute_deletes([plan], dry_run=False)
    assert calls == [["sessions", "delete", CLI_SESSION, "--yes"]]
    assert not legacy_file.exists()
    assert state_db(root).is_file()


def test_rename_shells_out(hermes_env: tuple[Path, str], monkeypatch: pytest.MonkeyPatch):
    calls: list[list[str]] = []

    def fake_run(_root: Path, args: list[str], **_kwargs) -> tuple[int, str]:
        calls.append(args)
        return 0, ""

    monkeypatch.setattr(hermes_ops, "run_hermes", fake_run)
    provider = HermesProvider()

    ok, msg = provider.rename(CLI_SESSION, "  New   title ")
    assert ok, msg
    assert calls == [["sessions", "rename", CLI_SESSION, "--", "New title"]]
    assert provider.rename(CLI_SESSION, "  ")[0] is False


def test_rename_reports_cli_failure(
    hermes_env: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        hermes_ops, "run_hermes", lambda *_a, **_k: (1, "hermes: no such session")
    )
    ok, msg = HermesProvider().rename(CLI_SESSION, "Title")
    assert ok is False
    assert "no such session" in msg


def test_missing_session_is_blocked(hermes_env: tuple[Path, str]):
    plan = HermesProvider().plan_delete("does_not_exist")
    assert plan.blocked_reason == "session not found"


def test_workspace_delete_covers_its_sessions(
    hermes_env: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
):
    _root, workspace = hermes_env
    monkeypatch.setattr(hermes_ops, "run_hermes", lambda *_a, **_k: (0, "deleted"))
    provider = HermesProvider()

    plan = provider.plan_project_delete(workspace)
    assert plan.can_delete
    assert [sp.session_id for sp in plan.session_plans] == [CLI_SESSION]

    actions = provider.execute_project_delete(plan, dry_run=True)
    assert any(CLI_SESSION in line for line in actions)

    assert (
        provider.plan_project_delete("/nowhere").blocked_reason
        == "workspace not found: /nowhere"
    )


def test_move_is_unsupported(hermes_env: tuple[Path, str]):
    provider = HermesProvider()
    assert provider.capabilities.move_sessions is False
    assert provider.capabilities.move_projects is False
    assert provider.move_sessions([], "/tmp/x").ok is False
    assert provider.move_projects([], "/tmp/x").ok is False


def test_empty_home_discovers_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "empty"))
    assert HermesProvider().discover() == []


def test_state_db_under_a_hash_in_the_path_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "a#b" / "hermes"
    root.mkdir(parents=True)
    workspace = tmp_path / "work" / "demo"
    workspace.mkdir(parents=True)
    _make_state_db(state_db(root), str(workspace), str(tmp_path / "work"))
    monkeypatch.setenv("HERMES_HOME", str(root))
    ids = {s.session_id for p in HermesProvider().discover() for s in p.sessions}
    assert CLI_SESSION in ids


def _capture_subprocess(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    seen: list[dict] = []

    class Done:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(argv, **kwargs):
        seen.append({"argv": argv, "env": kwargs.get("env", {})})
        return Done()

    monkeypatch.setattr(hermes_ops.shutil, "which", lambda _b: "/usr/bin/hermes")
    monkeypatch.setattr(hermes_ops.subprocess, "run", fake_run)
    return seen


def test_root_outside_profiles_is_pinned_with_dash_p_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    seen = _capture_subprocess(monkeypatch)
    root = tmp_path / "hermes"
    ok, _ = hermes_ops.rename_session(root, CLI_SESSION, "-dash title")
    assert ok
    assert seen[0]["argv"] == [
        "hermes", "-p", "default", "sessions", "rename", CLI_SESSION, "--", "-dash title",
    ]
    assert seen[0]["env"]["HERMES_HOME"] == str(root)

    hermes_ops.delete_session(root, CLI_SESSION)
    assert seen[1]["argv"] == [
        "hermes", "-p", "default", "sessions", "delete", CLI_SESSION, "--yes",
    ]


def test_profile_root_is_not_pinned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    seen = _capture_subprocess(monkeypatch)
    root = tmp_path / "profiles" / "work"
    hermes_ops.delete_session(root, CLI_SESSION)
    assert seen[0]["argv"] == ["hermes", "sessions", "delete", CLI_SESSION, "--yes"]


def test_resume_argv_pins_the_profile(tmp_path: Path):
    assert hermes_ops.resume_argv(CLI_SESSION, tmp_path / "hermes") == [
        "hermes", "-p", "default", "--resume", CLI_SESSION,
    ]
    assert hermes_ops.resume_argv(CLI_SESSION, tmp_path / "profiles" / "w") == [
        "hermes", "--resume", CLI_SESSION,
    ]


def test_open_command_uses_the_config_root(hermes_env: tuple[Path, str], monkeypatch):
    monkeypatch.setattr(hermes_ops.shutil, "which", lambda _b: "/usr/bin/hermes")
    provider = HermesProvider()
    session = provider.discover()[0].sessions[0]
    argv, _ = provider.open_command(session)
    assert argv[:3] == ["hermes", "-p", "default"]


def _live(sid: str, pid: int = 4242):
    from axism.live import LiveSession

    return LiveSession(
        session_id=sid, kind="interactive", state="running", pid=pid, source="filesystem"
    )


def test_failed_stop_aborts_the_delete_even_with_force(
    hermes_env: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        hermes_ops, "run_hermes", lambda _r, a, **_k: calls.append(a) or (0, "deleted")
    )
    monkeypatch.setattr("axism.live._live_pid", lambda *_a, **_k: True)
    provider = HermesProvider()
    monkeypatch.setattr(provider, "stop", lambda sid, live=None: (False, "stuck"))
    plan = provider.plan_delete(CLI_SESSION, force=True)
    plan.live = _live(CLI_SESSION)
    actions = provider.execute_deletes([plan], dry_run=False, force=True)
    assert f"aborted: could not stop live session ({CLI_SESSION[:8]})" in actions
    assert calls == []


def test_unknown_session_is_never_forced(hermes_env: tuple[Path, str]):
    provider = HermesProvider()
    plan = provider.plan_delete("nope_nope", force=True)
    assert plan.hard_blocked
    actions = provider.execute_deletes([plan], dry_run=False, force=True)
    assert actions[0].startswith("aborted")


def test_workspace_delete_keeps_going_never_when_stop_fails(
    hermes_env: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
):
    _root, workspace = hermes_env
    calls: list[list[str]] = []
    monkeypatch.setattr(
        hermes_ops, "run_hermes", lambda _r, a, **_k: calls.append(a) or (0, "deleted")
    )
    monkeypatch.setattr("axism.live._live_pid", lambda *_a, **_k: True)
    provider = HermesProvider()
    monkeypatch.setattr(provider, "stop", lambda sid, live=None: (False, "stuck"))
    plan = provider.plan_project_delete(workspace, force=True)
    for sp in plan.session_plans:
        sp.live = _live(sp.session_id)
    actions = provider.execute_project_delete(plan, dry_run=False, force=True)
    assert any(a.startswith("aborted: could not stop live session") for a in actions)
    assert "aborted: project kept because a live session could not be stopped" in actions
    assert calls == []
    assert provider.plan_project_delete("/nowhere", force=True).hard_blocked


@pytest.mark.parametrize("bad", [".", "..", "", "../x", "a/b"])
def test_non_plain_session_ids_match_nothing(
    hermes_env: tuple[Path, str], bad: str, monkeypatch: pytest.MonkeyPatch
):
    root, _ = hermes_env
    calls: list = []
    monkeypatch.setattr(
        hermes_ops, "run_hermes", lambda *a, **k: calls.append(a) or (0, "x")
    )
    provider = HermesProvider()
    plan = provider.plan_delete(bad, force=True)
    assert plan.hard_blocked and plan.blocked_reason == "session not found"
    assert provider.execute_deletes([plan], dry_run=False, force=True)[0].startswith(
        "aborted"
    )
    assert provider.rename(bad, "x")[0] is False
    assert calls == []
    assert state_db(root).is_file()


def test_symlinked_legacy_file_is_unlinked_not_followed(
    hermes_env: tuple[Path, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root, _ = hermes_env
    monkeypatch.setattr(hermes_ops, "run_hermes", lambda *_a, **_k: (0, "deleted"))
    victim = tmp_path / "victim.json"
    victim.write_text("{}", encoding="utf-8")
    (root / "sessions").mkdir()
    link = root / "sessions" / f"session_{CLI_SESSION}.json"
    link.symlink_to(victim)
    provider = HermesProvider()
    plan = provider.plan_delete(CLI_SESSION)
    provider.execute_deletes([plan], dry_run=False)
    assert victim.exists()
    assert not link.is_symlink()


def test_hermes_fragments_outside_the_legacy_dirs_are_refused(
    hermes_env: tuple[Path, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from axism.delete import DeletePlan
    from axism.fragments import Fragment, FragmentInventory

    root, _ = hermes_env
    monkeypatch.setattr(hermes_ops, "run_hermes", lambda *_a, **_k: (0, "deleted"))
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    inv = FragmentInventory(session_id=CLI_SESSION, project_slug=None)
    inv.fragments.append(Fragment(kind="legacy_json", path=outside, size_bytes=1))
    inv.fragments.append(Fragment(kind="legacy_json", path=root / "sessions", size_bytes=1))
    (root / "sessions").mkdir()
    plan = DeletePlan(session_id=CLI_SESSION, inventory=inv)
    actions = HermesProvider().execute_deletes([plan], dry_run=False, force=True)
    assert sum(a.startswith("refused: ") for a in actions) == 2
    assert outside.exists() and (root / "sessions").is_dir()
