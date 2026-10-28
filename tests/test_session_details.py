"""Provider API: transcript() and session_details()."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from axism.details import SessionDetails, TranscriptUnavailable
from axism.discover import SessionMeta
from axism.providers import get_provider
from tests.opencode_fixtures import make_opencode_db

SID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CWD = "/home/alice/src/demo"


def _rows(models: tuple[str | None, str | None] = (None, None)) -> list[dict]:
    def asst(text: str, model: str | None) -> dict:
        msg: dict = {"role": "assistant", "content": [{"type": "text", "text": text}]}
        if model:
            msg["model"] = model
        return {"type": "assistant", "message": msg}

    return [
        {"type": "user", "message": {"role": "user", "content": "first question"}},
        asst("first answer", models[0]),
        {"type": "user", "message": {"role": "user", "content": "second question"}},
        asst("second answer", models[1]),
    ]


def _meta(path: Path, provider: str, sid: str = SID) -> SessionMeta:
    return SessionMeta(
        session_id=sid, project_slug="-home-alice-src-demo", cwd=CWD, title=None,
        first_prompt=None, transcript_path=path, mtime=1.0,
        size_bytes=path.stat().st_size if path.exists() else 0, provider=provider,
    )


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("name", ["claude_code", "cursor"])
def test_default_transcript_and_details(tmp_path: Path, name: str):
    prov = get_provider(name, config_dir=tmp_path / "cfg")
    meta = _meta(_write(tmp_path / f"{SID}.jsonl", _rows()), name)
    turns = prov.transcript(meta)
    assert [(t.role, t.content) for t in turns] == [
        ("user", "first question"), ("assistant", "first answer"),
        ("user", "second question"), ("assistant", "second answer"),
    ]
    d = prov.session_details(meta)
    assert d.turns == 4
    assert d.first_prompt == "first question"
    assert d.last_prompt == "second question"
    assert d.last_reply == "second answer"
    assert d.note == ""
    if name == "cursor":
        assert (d.model, d.cost_usd, d.tokens_in, d.tokens_out) == (None,) * 4


def test_claude_model_is_last_assistant_record_with_one(tmp_path: Path):
    prov = get_provider("claude_code", config_dir=tmp_path / "cfg")
    path = _write(tmp_path / f"{SID}.jsonl", _rows(("model-a", "model-b")))
    assert prov.session_details(_meta(path, "claude_code")).model == "model-b"
    path = _write(tmp_path / f"{SID}.jsonl", _rows(("model-a", None)))
    assert prov.session_details(_meta(path, "claude_code")).model == "model-a"
    path = _write(tmp_path / f"{SID}.jsonl", _rows())
    assert prov.session_details(_meta(path, "claude_code")).model is None


def test_corrupt_and_missing_transcript_never_raise(tmp_path: Path):
    prov = get_provider("claude_code", config_dir=tmp_path / "cfg")
    bad = tmp_path / f"{SID}.jsonl"
    bad.write_bytes(b"\xff\x00 not json {{{\n")
    assert isinstance(prov.session_details(_meta(bad, "claude_code")), SessionDetails)
    gone = tmp_path / "gone.jsonl"
    assert isinstance(prov.session_details(_meta(gone, "claude_code")), SessionDetails)


def test_transcript_unavailable_sets_note(tmp_path: Path, monkeypatch):
    from axism.providers import transfer
    from axism.providers.transfer import CanonicalTranscript

    prov = get_provider("cursor", config_dir=tmp_path / "cfg")
    meta = _meta(tmp_path / "x", "cursor")
    monkeypatch.setattr(
        transfer, "export_session",
        lambda p, s: CanonicalTranscript(
            "t", None, "cursor", SID, [], "Export failed: boom"
        ),
    )
    with pytest.raises(TranscriptUnavailable) as exc:
        prov.transcript(meta)
    assert "boom" in exc.value.reason
    d = prov.session_details(meta)
    assert d.turns is None and d.last_reply is None
    assert "boom" in d.note
    assert (d.model, d.cost_usd, d.tokens_in, d.tokens_out, d.created) == (None,) * 5


def test_transcript_exception_becomes_unavailable(tmp_path: Path, monkeypatch):
    from axism.providers import transfer

    def boom(p, s):
        raise RuntimeError("kaput")

    monkeypatch.setattr(transfer, "export_session", boom)
    prov = get_provider("cursor", config_dir=tmp_path / "cfg")
    with pytest.raises(TranscriptUnavailable):
        prov.transcript(_meta(tmp_path / "x", "cursor"))


# -- OpenCode ---------------------------------------------------------------

OC = "ses_a1AliceDemo0001"


def _oc(tmp_path: Path, **usage) -> tuple[Path, SessionMeta]:
    root = tmp_path / "data" / "opencode"
    make_opencode_db(
        root,
        sessions=[
            {"id": OC, "project_id": "p1", "parent_id": None, "directory": CWD,
             "title": "Demo", "time_created": 1_700_000_000_000,
             "time_updated": 1_700_000_500_000, **usage},
        ],
        projects=[{"id": "p1", "worktree": CWD}],
        messages=[
            {"session_id": OC, "type": "user", "seq": 1, "data": {"text": "hi"}},
            {"session_id": OC, "type": "assistant", "seq": 2,
             "data": {"content": [{"type": "text", "text": "hello"}]}},
        ],
    )
    return root, _meta(root / "opencode.db", "opencode", OC)


def test_opencode_details_from_session_v2(tmp_path: Path):
    root, meta = _oc(
        tmp_path,
        model={"providerID": "anthropic", "id": "sonnet-x"},
        cost=0.0421, tokens_input=1200, tokens_output=345,
    )
    d = get_provider("opencode", config_dir=root).session_details(meta)
    assert d.created == 1_700_000_000.0
    assert d.model == "anthropic/sonnet-x"
    assert d.cost_usd == pytest.approx(0.0421)
    assert (d.tokens_in, d.tokens_out) == (1200, 345)
    assert (d.turns, d.first_prompt, d.last_reply) == (2, "hi", "hello")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("openai/gpt-x", "openai/gpt-x"),
        ("plainmodel", "plainmodel"),
        ({"provider": "p", "modelID": "m"}, "p/m"),
        ({"id": "only-id"}, "only-id"),
        ([1, 2], None),
        ({"weird": True}, None),
        ("", None),
        (None, None),
    ],
)
def test_opencode_model_shapes(tmp_path: Path, raw, expected):
    root, meta = _oc(tmp_path, model=raw, cost=None)
    d = get_provider("opencode", config_dir=root).session_details(meta)
    assert d.model == expected
    assert d.cost_usd is None


def test_opencode_without_usage_columns(tmp_path: Path):
    root, meta = _oc(tmp_path)
    d = get_provider("opencode", config_dir=root).session_details(meta)
    assert d.created == 1_700_000_000.0
    assert d.model is None and d.cost_usd is None and d.tokens_in is None
    assert d.turns == 2


def test_opencode_unreadable_db(tmp_path: Path):
    root = tmp_path / "data" / "opencode"
    root.mkdir(parents=True)
    (root / "opencode.db").write_bytes(b"not a database")
    meta = _meta(root / "opencode.db", "opencode", OC)
    prov = get_provider("opencode", config_dir=root)
    with pytest.raises(TranscriptUnavailable):
        prov.transcript(meta)
    d = prov.session_details(meta)
    assert d.note and d.turns is None


# -- tool-using sessions: text turns only -----------------------------------


def _tool_rows(tool_text: str = "") -> list[dict]:
    asst_tool = [{"type": "tool_use", "name": "Bash", "input": {}}]
    if tool_text:
        asst_tool.insert(0, {"type": "text", "text": tool_text})
    return [
        {"type": "user", "message": {"role": "user", "content": "run the build"}},
        {"type": "assistant", "message": {"role": "assistant", "content": asst_tool}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Build finished"}]}},
    ]


@pytest.mark.parametrize("name", ["claude_code", "cursor"])
def test_tool_use_does_not_pollute_turns(tmp_path: Path, name: str):
    prov = get_provider(name, config_dir=tmp_path / "cfg")
    meta = _meta(_write(tmp_path / f"{SID}.jsonl", _tool_rows("Running it")), name)
    assert [(t.role, t.content) for t in prov.transcript(meta)] == [
        ("user", "run the build"),
        ("assistant", "Running it\nBuild finished"),
    ]
    d = prov.session_details(meta)
    assert (d.turns, d.last_prompt, d.first_prompt) == (2, "run the build", "run the build")
    assert d.last_reply == "Running it\nBuild finished"


def test_tool_only_assistant_turn_is_dropped(tmp_path: Path):
    prov = get_provider("claude_code", config_dir=tmp_path / "cfg")
    meta = _meta(_write(tmp_path / f"{SID}.jsonl", _tool_rows()), "claude_code")
    d = prov.session_details(meta)
    assert d.turns == 2 and d.last_reply == "Build finished"


# -- Hermes: read-only from state.db, no CLI --------------------------------


def test_hermes_transcript_reads_database_not_cli(tmp_path: Path, monkeypatch):
    import sqlite3

    from axism.providers.hermes_store import ops as hermes_ops

    def no_cli(*a, **k):
        raise AssertionError("hermes CLI must not run")

    monkeypatch.setattr(hermes_ops, "run_hermes", no_cli)
    root = tmp_path / "hermes"
    root.mkdir()
    con = sqlite3.connect(root / "state.db")
    with con:
        con.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT,"
            " role TEXT, content TEXT)"
        )
        con.executemany(
            "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
            [(SID, "user", "hello"), (SID, "tool", "ignored"),
             (SID, "assistant", "hi there"), (SID, "user", "bye"),
             (SID, "assistant", "[ran tool: X]"), ("other", "user", "nope")],
        )
    con.close()
    prov = get_provider("hermes", config_dir=root)
    meta = _meta(root / "state.db", "hermes")
    assert [(t.role, t.content) for t in prov.transcript(meta)] == [
        ("user", "hello"), ("assistant", "hi there"), ("user", "bye")]
    d = prov.session_details(meta)
    assert (d.turns, d.last_prompt, d.last_reply) == (3, "bye", "hi there")
    missing = get_provider("hermes", config_dir=tmp_path / "none")
    with pytest.raises(TranscriptUnavailable):
        missing.transcript(meta)
