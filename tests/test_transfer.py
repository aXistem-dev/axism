"""Cross-provider session transfer (copy/convert)."""

from __future__ import annotations

import json
from pathlib import Path

from axism.discover import SessionMeta
from axism.providers import get_provider
from axism.providers.transfer import (
    export_claude_jsonl,
    import_to_provider,
    transfer_session,
)


def _sample_claude_jsonl(path: Path, session_id: str, cwd: str) -> None:
    rows = [
        {
            "type": "custom-title",
            "title": "Demo cross",
            "sessionId": session_id,
            "cwd": cwd,
            "uuid": "11111111-1111-1111-1111-111111111111",
            "parentUuid": None,
        },
        {
            "type": "user",
            "sessionId": session_id,
            "cwd": cwd,
            "uuid": "22222222-2222-2222-2222-222222222222",
            "parentUuid": "11111111-1111-1111-1111-111111111111",
            "message": {"role": "user", "content": "Hello from Claude"},
        },
        {
            "type": "assistant",
            "sessionId": session_id,
            "cwd": cwd,
            "uuid": "33333333-3333-3333-3333-333333333333",
            "parentUuid": "22222222-2222-2222-2222-222222222222",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Hi there"}],
            },
        },
    ]
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


def test_export_claude_turns(tmp_path: Path):
    sid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    cwd = "/home/alice/src/demo"
    path = tmp_path / f"{sid}.jsonl"
    _sample_claude_jsonl(path, sid, cwd)
    sess = SessionMeta(
        session_id=sid,
        project_slug="-home-alice-src-demo",
        cwd=cwd,
        title=None,
        first_prompt=None,
        transcript_path=path,
        mtime=1.0,
        size_bytes=path.stat().st_size,
        provider="claude_code",
    )
    canonical = export_claude_jsonl(path, session=sess)
    assert canonical.title == "Demo cross"
    assert len(canonical.turns) == 2
    assert canonical.turns[0].role == "user"
    assert "Hello" in canonical.turns[0].content
    assert canonical.turns[1].role == "assistant"


def test_import_to_claude_and_cursor(tmp_path: Path):
    sid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    cwd = "/home/alice/src/demo"
    path = tmp_path / "src.jsonl"
    _sample_claude_jsonl(path, sid, cwd)
    sess = SessionMeta(
        session_id=sid,
        project_slug="-home-alice-src-demo",
        cwd=cwd,
        title="Demo cross",
        first_prompt=None,
        transcript_path=path,
        mtime=1.0,
        size_bytes=10,
        provider="claude_code",
    )
    canonical = export_claude_jsonl(path, session=sess)

    claude_home = tmp_path / "claude"
    cursor_home = tmp_path / "cursor"
    claude_home.mkdir()
    cursor_home.mkdir()
    claude = get_provider("claude_code", config_dir=claude_home)
    cursor = get_provider("cursor", config_dir=cursor_home)

    dest_cwd = "/home/alice/src/other"
    r1 = import_to_provider(claude, canonical, dest_cwd)
    assert r1.ok, r1.message
    assert r1.new_session_id
    written = list((claude_home / "projects").rglob("*.jsonl"))
    assert len(written) == 1

    r2 = import_to_provider(cursor, canonical, dest_cwd)
    assert r2.ok, r2.message
    assert r2.new_session_id
    assert list((cursor_home / "projects").rglob("*.jsonl"))

    dry = transfer_session(claude, cursor, sess, dest_cwd, dry_run=True)
    assert dry.ok
    assert "dry-run" in dry.message.lower() or "dry-run" in " ".join(dry.actions)
