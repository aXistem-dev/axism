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
    assert calls == [["sessions", "rename", CLI_SESSION, "New title"]]
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
