from __future__ import annotations

from pathlib import Path

from axism.fragments import Fragment
from axism.providers.opencode_store import discover, fragments, paths
from tests.opencode_fixtures import make_opencode_db


def _base(root: Path, **kw):
    sessions = kw.pop(
        "sessions",
        [
            {"id": "ses_a1", "project_id": "p1", "parent_id": None,
             "directory": "/home/alice/one", "title": "First",
             "time_created": 1_000_000, "time_updated": 5_000_000},
            {"id": "ses_b1", "project_id": "p2", "parent_id": None,
             "directory": "/home/alice/two", "title": "Second",
             "time_created": 1_000_000, "time_updated": 9_000_000},
            {"id": "ses_c1", "project_id": "p1", "parent_id": "ses_a1",
             "directory": "/home/alice/one", "title": "child",
             "time_created": 1_000_000, "time_updated": 2_000_000},
        ],
    )
    projects = kw.pop(
        "projects",
        [{"id": "p1", "worktree": "/home/alice/one"},
         {"id": "p2", "worktree": "/home/alice/two"}],
    )
    messages = kw.pop(
        "messages",
        [
            {"session_id": "ses_a1", "type": "user", "seq": 1,
             "data": {"text": "hello  world"}},
            {"session_id": "ses_a1", "type": "compaction", "seq": 2,
             "data": {"x": 1}},
            {"session_id": "ses_a1", "type": "assistant", "seq": 3,
             "data": {"content": [{"type": "text", "text": "hi"},
                                  {"type": "tool"}]}},
            {"session_id": "ses_c1", "type": "user", "seq": 1,
             "data": {"text": "child work"}},
            {"session_id": "ses_b1", "type": "user", "seq": 1,
             "data": {"text": "other"}},
        ],
    )
    return make_opencode_db(
        root, sessions=sessions, projects=projects, messages=messages, **kw
    )


def test_default_data_dir_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    assert paths.default_data_dir() == tmp_path / "d" / "opencode"
    assert paths.default_state_dir() == tmp_path / "s" / "opencode"
    assert paths.is_default_root(tmp_path / "d" / "opencode")
    assert not paths.is_default_root(tmp_path / "other")
    monkeypatch.delenv("XDG_DATA_HOME")
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    assert paths.default_data_dir() == (
        Path.home() / ".local" / "share" / "opencode"
    )
    assert paths.default_state_dir() == (
        Path.home() / ".local" / "state" / "opencode"
    )
    assert paths.db_path(tmp_path) == tmp_path / "opencode.db"


def test_layout_missing_unsupported_ok(tmp_path):
    assert discover.layout_status(tmp_path / "none") == "missing"
    bad = tmp_path / "bad"
    _base(bad, drop_columns={"session_v2": {"directory"}})
    assert discover.layout_status(bad) == "unsupported"
    assert discover.discover_all(bad) == []
    ok = tmp_path / "ok"
    _base(ok)
    assert discover.layout_status(ok) == "ok"
    junk = tmp_path / "junk"
    junk.mkdir()
    (junk / "opencode.db").write_text("not sqlite at all")
    assert discover.layout_status(junk) == "unsupported"


def test_discover_groups_by_project_and_folds_children(tmp_path):
    _base(tmp_path)
    projects = discover.discover_all(tmp_path)
    assert [p.slug for p in projects] == ["p2", "p1"]
    p1 = projects[1]
    assert p1.cwd_guess == "/home/alice/one"
    assert [s.session_id for s in p1.sessions] == ["ses_a1"]
    s = p1.sessions[0]
    assert s.subagent_count == 1
    assert s.mtime == 5000.0
    assert s.cwd == "/home/alice/one"
    assert s.title == "First"
    assert s.provider is None
    assert s.transcript_path == tmp_path / "opencode.db"
    import sqlite3

    con = sqlite3.connect(tmp_path / "opencode.db")
    expected = con.execute(
        "SELECT SUM(LENGTH(data)) FROM session_message "
        "WHERE session_id IN ('ses_a1','ses_c1')"
    ).fetchone()[0]
    assert s.size_bytes == expected


def test_missing_project_row_uses_session_directory(tmp_path):
    _base(tmp_path, projects=[{"id": "p2", "worktree": "/home/alice/two"}])
    by_slug = {p.slug: p for p in discover.discover_all(tmp_path)}
    assert by_slug["p1"].cwd_guess == "/home/alice/one"


def test_projects_without_sessions_not_listed(tmp_path):
    _base(tmp_path, projects=[{"id": "p1", "worktree": "/a"},
                              {"id": "p2", "worktree": "/b"},
                              {"id": "p9", "worktree": "/c"}])
    assert "p9" not in [p.slug for p in discover.discover_all(tmp_path)]


def test_archived_badge(tmp_path):
    sessions = [
        {"id": "ses_x", "project_id": "p1", "parent_id": None,
         "directory": "/home/alice/one", "title": "t",
         "time_created": 1, "time_updated": 2, "time_archived": 3},
        {"id": "ses_y", "project_id": "p1", "parent_id": None,
         "directory": "/home/alice/one", "title": "t",
         "time_created": 1, "time_updated": 2},
    ]
    _base(tmp_path, sessions=sessions, messages=[])
    by_id = {s.session_id: s for s in discover.discover_all(tmp_path)[0].sessions}
    assert by_id["ses_x"].entrypoint == "archived"
    assert by_id["ses_y"].entrypoint is None
    # Optional column absent: still lists.
    other = tmp_path / "o"
    _base(other, drop_columns={"session_v2": {"time_archived"}})
    assert discover.layout_status(other) == "ok"
    assert discover.discover_all(other)


def test_first_prompt_and_turns(tmp_path):
    _base(tmp_path)
    s = discover.discover_all(tmp_path)[1].sessions[0]
    assert s.first_prompt == "hello world"
    assert discover.transcript_turns(tmp_path, "ses_a1") == [
        ("user", "hello  world"),
        ("assistant", "hi"),
    ]


def test_malformed_message_json_skipped(tmp_path):
    msgs = [
        {"session_id": "ses_a1", "type": "user", "seq": 1, "data": "{not json"},
        {"session_id": "ses_a1", "type": "user", "seq": 2, "data": {"text": "ok"}},
        {"session_id": "ses_a1", "type": "assistant", "seq": 3, "data": "[1]"},
    ]
    _base(tmp_path, messages=msgs)
    assert discover.transcript_turns(tmp_path, "ses_a1") == [("user", "ok")]
    s = discover.discover_all(tmp_path)[1].sessions[0]
    assert s.first_prompt == "ok"


def test_session_row_and_children(tmp_path):
    _base(tmp_path)
    row = discover.session_row(tmp_path, "ses_a1")
    assert row is not None
    assert row.project_id == "p1"
    assert row.parent_id is None
    assert row.child_ids == ["ses_c1"]
    assert row.time_updated == 5_000_000
    assert discover.session_row(tmp_path, "ses_zzz") is None
    assert discover.session_row(tmp_path / "nope", "ses_a1") is None


def test_is_session_id():
    for ok in ("ses_abc", "ses_A1b2"):
        assert paths.is_session_id(ok)
    for bad in ("ses_", "ses_a/b", "ses_a\n", "abc", ""):
        assert not paths.is_session_id(bad)
    assert paths.short_id("ses_abcdefghijklmnop") == "ses_abcdefgh"


def test_db_name_protected(tmp_path):
    for name in ("opencode.db", "opencode.db-wal", "opencode.db-shm"):
        assert Fragment(kind="session_rows", path=tmp_path / name,
                        size_bytes=1).is_protected()


def test_collect_fragments_never_deletable(tmp_path):
    db = _base(tmp_path)
    inv = fragments.collect_fragments(tmp_path, "ses_a1", project_slug="p1")
    assert len(inv.fragments) == 1
    f = inv.fragments[0]
    assert f.kind == "session_rows"
    assert f.path == db.resolve()
    assert f.is_protected()
    assert f.size_bytes > 0
    empty = fragments.collect_fragments(tmp_path / "nope", "ses_a1")
    assert empty.fragments == []


def test_family_map_walks_whole_descendant_tree(tmp_path):
    def s(i, parent):
        return {"id": i, "project_id": "p1", "parent_id": parent,
                "directory": "/home/alice/one", "title": i,
                "time_created": 1, "time_updated": 2}

    make_opencode_db(
        tmp_path,
        sessions=[s("ses_t1", None), s("ses_k1", "ses_t1"), s("ses_g1", "ses_k1"),
                  s("ses_t2", None)],
        projects=[{"id": "p1", "worktree": "/home/alice/one"}],
        messages=[],
    )
    fam = discover.family_map(tmp_path)
    assert fam == {"ses_t1": ["ses_g1", "ses_k1"], "ses_t2": []}
    assert discover.family_map(tmp_path / "none") == {}
