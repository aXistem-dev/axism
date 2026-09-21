"""Tests for the Cursor provider (agent transcripts + CLI SQLite chats)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from axism.providers import get_provider
from axism.providers.cursor import CursorProvider
from axism.providers.cursor_store.discover import read_cli_meta
from axism.providers.cursor_store.paths import (
    TITLE_SIDECAR,
    cursor_data_dir,
    cwd_bucket,
    decode_slug,
    slugify_path,
)

AGENT_ONLY = "11111111-2222-3333-4444-555555555555"
CLI_ONLY = "66666666-7777-8888-9999-aaaaaaaaaaaa"
BOTH_STORES = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"


def _write_transcript(conv_dir: Path, chat_id: str, prompt: str) -> None:
    conv_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {"type": "status", "status": "started"},
        {
            "role": "user",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": f"<timestamp>now</timestamp>\n<user_query>\n{prompt}\n</user_query>",
                    }
                ]
            },
        },
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}},
    ]
    (conv_dir / f"{chat_id}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


def _write_cli_chat(chat_dir: Path, name: str) -> None:
    chat_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "agentId": chat_dir.name,
        "name": name,
        "mode": "agent",
        "createdAt": 1700000000000,
        "lastUsedModel": "composer",
    }
    con = sqlite3.connect(chat_dir / "store.db")
    with con:
        con.execute("CREATE TABLE blobs (id TEXT PRIMARY KEY, data BLOB)")
        con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        con.execute("INSERT INTO blobs VALUES ('abc', ?)", (b"body",))
        con.execute(
            "INSERT INTO meta VALUES ('0', ?)",
            (json.dumps(meta).encode("utf-8").hex(),),
        )
    con.close()
    (chat_dir / "prompt_history.json").write_text("[]", encoding="utf-8")


@pytest.fixture
def cursor_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """Synthetic ~/.cursor tree plus a real workspace dir to resolve slugs."""
    workspace = tmp_path / "work" / "demo"
    workspace.mkdir(parents=True)
    root = tmp_path / "cursor"
    slug = slugify_path(workspace)

    transcripts = root / "projects" / slug / "agent-transcripts"
    _write_transcript(transcripts / AGENT_ONLY, AGENT_ONLY, "explain the build")
    _write_transcript(transcripts / BOTH_STORES, BOTH_STORES, "shared chat")

    bucket = root / "chats" / cwd_bucket(workspace)
    _write_cli_chat(bucket / CLI_ONLY, "CLI only chat")
    _write_cli_chat(bucket / BOTH_STORES, "Shared chat")

    monkeypatch.setenv("CURSOR_DATA_DIR", str(root))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    return root, str(workspace)


def test_slug_and_bucket_rules(tmp_path: Path):
    assert slugify_path("/a/b-c/d") == "a-b-c-d"
    assert not slugify_path("/a/b").startswith("-")
    nested = tmp_path / "deep" / "nest-with-dash"
    nested.mkdir(parents=True)
    assert decode_slug(slugify_path(nested)) == str(nested)
    assert cwd_bucket("/tmp/x") == hashlib.md5(b"/tmp/x").hexdigest()


def test_config_root_env(cursor_env: tuple[Path, str]):
    root, _ = cursor_env
    assert cursor_data_dir() == root.resolve()
    assert CursorProvider().config_root() == root.resolve()
    assert get_provider("cursor").name == "cursor"


def test_discover_merges_both_stores(cursor_env: tuple[Path, str]):
    _root, workspace = cursor_env
    projects = CursorProvider().discover()
    assert len(projects) == 1
    project = projects[0]
    assert project.cwd_guess == workspace

    by_id = {s.session_id: s for s in project.sessions}
    assert set(by_id) == {AGENT_ONLY, CLI_ONLY, BOTH_STORES}
    assert by_id[AGENT_ONLY].entrypoint == "agent"
    assert by_id[AGENT_ONLY].display_title == "explain the build"
    assert by_id[CLI_ONLY].entrypoint == "cli"
    assert by_id[CLI_ONLY].title == "CLI only chat"
    # One chat, two stores: a single row that knows about both.
    assert by_id[BOTH_STORES].entrypoint == "agent+cli"


def test_fragments_cover_every_store(cursor_env: tuple[Path, str]):
    provider = CursorProvider()
    inv = provider.collect_fragments(BOTH_STORES)
    kinds = sorted(f.kind for f in inv.fragments)
    assert kinds == ["cli_chat_dir", "transcript_dir"]
    assert all(not f.is_protected() for f in inv.fragments)


def test_rename_updates_sidecar_and_sqlite(cursor_env: tuple[Path, str]):
    root, _ = cursor_env
    provider = CursorProvider()

    ok, msg = provider.rename(AGENT_ONLY, "  Renamed agent chat ")
    assert ok, msg
    session = {s.session_id: s for s in provider.discover()[0].sessions}[AGENT_ONLY]
    assert session.title == "Renamed agent chat"

    ok, msg = provider.rename(CLI_ONLY, "New CLI title")
    assert ok, msg
    bucket = next((root / "chats").iterdir())
    assert read_cli_meta(bucket / CLI_ONLY / "store.db")["name"] == "New CLI title"

    assert provider.rename(AGENT_ONLY, "   ")[0] is False


def test_rename_both_stores_writes_each(cursor_env: tuple[Path, str]):
    root, _ = cursor_env
    provider = CursorProvider()
    ok, _msg = provider.rename(BOTH_STORES, "Unified title")
    assert ok
    slug = next(iter((root / "projects").iterdir())).name
    sidecar = (
        root / "projects" / slug / "agent-transcripts" / BOTH_STORES / TITLE_SIDECAR
    )
    assert json.loads(sidecar.read_text())["title"] == "Unified title"
    bucket = next((root / "chats").iterdir())
    assert read_cli_meta(bucket / BOTH_STORES / "store.db")["name"] == "Unified title"


def test_delete_removes_both_stores(cursor_env: tuple[Path, str]):
    root, _ = cursor_env
    provider = CursorProvider()
    plan = provider.plan_delete(BOTH_STORES)
    assert plan.can_delete
    assert len(plan.inventory.deletable) == 2

    dry = provider.execute_deletes([plan], dry_run=True)
    assert all(line.startswith("would remove") for line in dry)
    assert (root / "chats" / next((root / "chats").iterdir()).name).exists()

    provider.execute_deletes([plan], dry_run=False)
    remaining = {
        s.session_id for p in provider.discover() for s in p.sessions
    }
    assert BOTH_STORES not in remaining
    assert remaining == {AGENT_ONLY, CLI_ONLY}


def test_delete_missing_session_is_blocked(cursor_env: tuple[Path, str]):
    plan = CursorProvider().plan_delete("00000000-0000-0000-0000-000000000000")
    assert plan.blocked_reason == "session not found"


def test_move_sessions_relocates_both_stores(
    cursor_env: tuple[Path, str], tmp_path: Path
):
    root, _ = cursor_env
    provider = CursorProvider()
    dest = tmp_path / "work" / "other"
    dest.mkdir(parents=True)

    session = {s.session_id: s for s in provider.discover()[0].sessions}[BOTH_STORES]
    result = provider.move_sessions([session], str(dest))
    assert result.ok, result.message

    dest_slug = slugify_path(dest)
    assert (
        root / "projects" / dest_slug / "agent-transcripts" / BOTH_STORES
    ).is_dir()
    assert (root / "chats" / cwd_bucket(dest) / BOTH_STORES).is_dir()

    moved = {p.cwd_guess: p for p in provider.discover()}
    assert str(dest) in moved


def test_move_project_renames_store_dirs(cursor_env: tuple[Path, str], tmp_path: Path):
    root, _ = cursor_env
    provider = CursorProvider()
    dest = tmp_path / "work" / "renamed"

    project = provider.discover()[0]
    result = provider.move_projects([project], str(dest))
    assert result.ok, result.message
    assert (root / "projects" / slugify_path(dest)).is_dir()
    assert (root / "chats" / cwd_bucket(dest)).is_dir()


def test_project_delete_clears_workspace(cursor_env: tuple[Path, str]):
    root, _ = cursor_env
    provider = CursorProvider()
    project = provider.discover()[0]

    plan = provider.plan_project_delete(project.slug)
    assert plan.can_delete
    assert plan.extra_paths, "CLI chat bucket should be part of the plan"

    provider.execute_project_delete(plan, dry_run=False, force=True)
    assert provider.discover() == []
    assert not (root / "projects" / project.slug).exists()


def test_unsupported_operations_are_explicit(cursor_env: tuple[Path, str]):
    provider = CursorProvider()
    assert provider.capabilities.memory_merge is False
    assert provider.capabilities.purge_project is False
    rc, msg = provider.purge_project("/anything")
    assert rc == 1
    assert "purge" in msg
