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


def _session(path: Path, sid: str, cwd: str, provider: str) -> SessionMeta:
    return SessionMeta(
        session_id=sid,
        project_slug="-home-alice-src-demo",
        cwd=cwd,
        title="Demo cross",
        first_prompt=None,
        transcript_path=path,
        mtime=1.0,
        size_bytes=10,
        provider=provider,
    )


def test_hermes_export_failure_leaves_destination_untouched(
    tmp_path: Path, monkeypatch
):
    from axism.providers.hermes_store import ops as hermes_ops

    monkeypatch.setattr(
        hermes_ops, "run_hermes", lambda *a, **k: (1, "hermes exploded")
    )
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    claude_home = tmp_path / "claude"
    claude_home.mkdir()
    source = get_provider("hermes", config_dir=hermes_home)
    dest = get_provider("claude_code", config_dir=claude_home)
    sess = _session(tmp_path / "x", "20260101_000000_abcdef", "/home/alice/src/demo", "hermes")

    result = transfer_session(source, dest, sess, "/home/alice/src/other")

    assert result.ok is False
    assert "hermes exploded" in result.message
    assert not list(claude_home.rglob("*"))


def test_relative_destination_resolves_against_cwd(tmp_path: Path, monkeypatch):
    sid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    path = tmp_path / "src.jsonl"
    _sample_claude_jsonl(path, sid, "/home/alice/src/demo")
    sess = _session(path, sid, "/home/alice/src/demo", "claude_code")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    cursor_home = tmp_path / "cursor"
    cursor_home.mkdir()
    claude_home = tmp_path / "claude"
    claude_home.mkdir()
    source = get_provider("claude_code", config_dir=claude_home)
    dest = get_provider("cursor", config_dir=cursor_home)

    result = transfer_session(source, dest, sess, "sub/project")

    assert result.ok, result.message
    assert result.dest_cwd == str((work / "sub" / "project").resolve())


def test_empty_destination_is_an_error(tmp_path: Path):
    sid = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    path = tmp_path / "src.jsonl"
    _sample_claude_jsonl(path, sid, "/home/alice/src/demo")
    sess = _session(path, sid, "/home/alice/src/demo", "claude_code")
    claude_home = tmp_path / "claude"
    claude_home.mkdir()
    cursor_home = tmp_path / "cursor"
    cursor_home.mkdir()
    source = get_provider("claude_code", config_dir=claude_home)
    dest = get_provider("cursor", config_dir=cursor_home)

    for empty in ("", "   "):
        result = transfer_session(source, dest, sess, empty)
        assert result.ok is False
        assert "destination" in result.message.lower()
    assert not list(cursor_home.rglob("*"))


def test_export_reads_custom_title_key(tmp_path: Path):
    sid = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
    path = tmp_path / "src.jsonl"
    rows = [
        {"type": "custom-title", "customTitle": "From customTitle", "sessionId": sid},
        {"type": "user", "message": {"role": "user", "content": "hi"}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    sess = _session(path, sid, "/home/alice/src/demo", "claude_code")
    assert export_claude_jsonl(path, session=sess).title == "From customTitle"


# -- OpenCode ---------------------------------------------------------------

OC_ID = "ses_a1AliceDemo0001"


def _oc_root(tmp_path: Path) -> Path:
    from tests.opencode_fixtures import make_opencode_db

    root = tmp_path / "data" / "opencode"
    make_opencode_db(
        root,
        sessions=[
            {"id": OC_ID, "project_id": "p1", "parent_id": None,
             "directory": "/home/alice/one", "title": "Oc demo",
             "time_created": 1_000_000, "time_updated": 5_000_000},
        ],
        projects=[{"id": "p1", "worktree": "/home/alice/one"}],
        messages=[
            {"session_id": OC_ID, "type": "user", "seq": 1,
             "data": {"text": "hello oc"}},
            {"session_id": OC_ID, "type": "assistant", "seq": 2,
             "data": {"content": [{"type": "text", "text": "hi alice"}]}},
        ],
    )
    return root


def _oc_meta(root: Path) -> SessionMeta:
    return _session(root / "opencode.db", OC_ID, "/home/alice/one", "opencode")


def test_export_opencode_turns_and_title(tmp_path: Path):
    from axism.providers.transfer import export_session

    root = _oc_root(tmp_path)
    prov = get_provider("opencode", config_dir=root)
    canonical = export_session(prov, _oc_meta(root))
    assert canonical.export_error == ""
    assert canonical.title == "Oc demo"
    assert canonical.source_provider == "opencode"
    assert [(t.role, t.content) for t in canonical.turns] == [
        ("user", "hello oc"),
        ("assistant", "hi alice"),
    ]


def test_export_opencode_unreadable_db_sets_error(tmp_path: Path):
    from axism.providers.transfer import export_session

    root = tmp_path / "data" / "opencode"
    root.mkdir(parents=True)
    (root / "opencode.db").write_bytes(b"not a database")
    prov = get_provider("opencode", config_dir=root)
    canonical = export_session(prov, _oc_meta(root))
    assert canonical.export_error
    dest = get_provider("claude_code", config_dir=tmp_path / "claude")
    result = transfer_session(prov, dest, _oc_meta(root), "/home/alice/x")
    assert result.ok is False
    assert result.message == canonical.export_error


def _oc_dest(tmp_path: Path):
    root = tmp_path / "ocdest" / "opencode"
    root.mkdir(parents=True)
    return get_provider("opencode", config_dir=root)


def _canon(title: str = "Demo cross"):
    from axism.providers.transfer import CanonicalTranscript, Turn

    return CanonicalTranscript(
        title=title,
        cwd="/home/alice/src/demo",
        source_provider="claude_code",
        source_session_id="s1",
        turns=[Turn("user", "Hello"), Turn("assistant", "Hi there")],
    )


def test_import_opencode_writes_transfer_json_and_reports_new_id(
    tmp_path: Path, monkeypatch
):
    from axism.providers.opencode_store import ops

    seen: dict = {}

    def fake_import(root, transfer_file, directory, *, session_id):
        seen["data"] = json.loads(Path(transfer_file).read_text(encoding="utf-8"))
        seen["file"] = Path(transfer_file)
        seen["dir"] = directory
        seen["id"] = session_id
        return True, "imported", session_id

    from axism.providers.opencode_store import discover as oc_discover
    from axism.providers.opencode_store.discover import SessionRow

    monkeypatch.setattr(ops, "import_session", fake_import)
    monkeypatch.setattr(ops, "rename_session", lambda *a, **k: (True, "renamed"))
    monkeypatch.setattr(
        oc_discover,
        "session_row",
        lambda root, sid: SessionRow(
            id=sid, project_id="p", parent_id=None, directory="/home/alice/src/other",
            title="Demo cross", time_created=1, time_updated=1,
        ),
    )
    dest = _oc_dest(tmp_path)
    result = import_to_provider(dest, _canon(), "/home/alice/src/other")

    assert result.ok, result.message
    assert result.new_session_id == seen["id"]
    assert result.new_session_id.startswith("ses_")
    assert result.dest_provider == "opencode"
    assert result.dest_cwd == "/home/alice/src/other"
    assert seen["dir"] == "/home/alice/src/other"
    data = seen["data"]
    assert data["info"]["id"] == seen["id"]
    assert data["info"]["title"] == "Demo cross"
    assert data["info"]["location"] == {"directory": "/home/alice/src/other"}
    msgs = data["messages"]
    assert [m["type"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["text"] == "Hello"
    assert msgs[1]["content"] == [{"type": "text", "text": "Hi there"}]
    assert all(m["id"].startswith("msg_") for m in msgs)
    assert msgs[0]["id"] < msgs[1]["id"]
    assert msgs[1]["agent"] and msgs[1]["model"]["id"] and msgs[1]["model"]["providerID"]
    assert "completed" in msgs[1]["time"]
    assert not seen["file"].exists()


def test_import_opencode_failure_is_not_ok(tmp_path: Path, monkeypatch):
    from axism.providers.opencode_store import ops

    files: list[Path] = []

    def fake_import(root, transfer_file, directory, *, session_id):
        files.append(Path(transfer_file))
        return False, "error: import into OpenCode failed: boom", ""

    monkeypatch.setattr(ops, "import_session", fake_import)
    result = import_to_provider(_oc_dest(tmp_path), _canon(), "/home/alice/src/other")
    assert result.ok is False
    assert "boom" in result.message
    assert result.new_session_id == ""
    assert not files[0].exists()


def test_import_opencode_removes_temp_file_on_exception(tmp_path: Path, monkeypatch):
    from axism.providers.opencode_store import ops

    files: list[Path] = []

    def fake_import(root, transfer_file, directory, *, session_id):
        files.append(Path(transfer_file))
        raise OSError("disk gone")

    monkeypatch.setattr(ops, "import_session", fake_import)
    result = import_to_provider(_oc_dest(tmp_path), _canon(), "/home/alice/src/other")
    assert result.ok is False
    assert "disk gone" in result.message
    assert not files[0].exists()


def test_import_opencode_renames_when_title_lost(tmp_path: Path, monkeypatch):
    from axism.providers.opencode_store import discover as oc_discover
    from axism.providers.opencode_store import ops
    from axism.providers.opencode_store.discover import SessionRow

    monkeypatch.setattr(
        ops, "import_session", lambda r, f, d, *, session_id: (True, "ok", session_id)
    )
    monkeypatch.setattr(
        oc_discover,
        "session_row",
        lambda root, sid: SessionRow(
            id=sid, project_id="p", parent_id=None, directory="/x",
            title="Other title", time_created=1, time_updated=1,
        ),
    )
    calls: list[str] = []
    monkeypatch.setattr(
        ops, "rename_session", lambda root, row, title: (calls.append(title), (True, "r"))[1]
    )
    result = import_to_provider(_oc_dest(tmp_path), _canon(), "/home/alice/src/other")
    assert result.ok
    assert calls == ["Demo cross"]


def test_import_opencode_missing_row_is_an_error(tmp_path: Path, monkeypatch):
    from axism.actions import is_failure_line
    from axism.providers.opencode_store import ops
    from axism.providers.opencode_store.paths import short_id

    monkeypatch.setattr(
        ops,
        "import_session",
        lambda r, f, d, *, session_id: (True, f"imported {short_id(session_id)}", session_id),
    )

    def no_rename(*a, **k):
        raise AssertionError("must not rename")

    monkeypatch.setattr(ops, "rename_session", no_rename)
    result = import_to_provider(_oc_dest(tmp_path), _canon(), "/home/alice/src/other")
    assert result.ok is False
    sid = result.new_session_id
    assert result.message == (
        f"error: imported session {short_id(sid)} is not in the OpenCode database aXism lists"
    )
    assert is_failure_line(result.message)
    assert f"imported {short_id(sid)}" in result.actions


def test_import_opencode_custom_root_refused(tmp_path: Path):
    root = tmp_path / "custom"
    root.mkdir()
    dest = get_provider("opencode", config_dir=root)
    result = import_to_provider(dest, _canon(), "/home/alice/src/other")
    assert result.ok is False
    assert result.message.startswith("refused: ")


def test_roundtrip_claude_to_opencode_dry_run(tmp_path: Path):
    sid = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    path = tmp_path / "src.jsonl"
    _sample_claude_jsonl(path, sid, "/home/alice/src/demo")
    sess = _session(path, sid, "/home/alice/src/demo", "claude_code")
    claude_home = tmp_path / "claude"
    claude_home.mkdir()
    source = get_provider("claude_code", config_dir=claude_home)
    dest = _oc_dest(tmp_path)
    result = transfer_session(source, dest, sess, "/home/alice/src/other", dry_run=True)
    assert result.ok
    assert "dry-run" in result.message.lower()
    assert not list((tmp_path / "ocdest").rglob("*.db"))


def test_failed_import_cli_names_the_leftover_id(tmp_path: Path, monkeypatch, capsys):
    from axism import cli
    from axism.providers import transfer
    from axism.providers.transfer import TransferResult

    leftover = TransferResult(
        ok=False, message="error: boom", new_session_id="ses_left1", dest_provider="opencode"
    )
    monkeypatch.setattr(transfer, "transfer_session", lambda *a, **k: leftover)
    root = _oc_root(tmp_path)
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(tmp_path / "cfg"))
    argv = ["export", OC_ID, "--to-provider", "claude_code", "--to", "/home/alice/x",
            "--provider", "opencode", "--config-dir", str(root)]
    try:
        cli.main(argv)
    except SystemExit as exc:
        assert exc.code == 1
    out = capsys.readouterr().out
    assert "imported_session_id (not listed): ses_left1" in out
    assert "new_session_id:" not in out

    ok = TransferResult(ok=True, message="done", new_session_id="ses_new1")
    monkeypatch.setattr(transfer, "transfer_session", lambda *a, **k: ok)
    try:
        cli.main(argv)
    except SystemExit:
        pass
    assert "new_session_id: ses_new1" in capsys.readouterr().out
