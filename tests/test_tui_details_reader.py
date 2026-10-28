"""The facts-first details pane and the full-screen conversation reader."""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path

import pytest
from conftest import write_jsonl
from textual.widgets import DataTable

import axism.tui.app as tui
from axism.details import SessionDetails, TranscriptUnavailable
from axism.discover import SessionMeta
from axism.providers.claude_code import ClaudeCodeProvider
from axism.tui.app import SessionManagerApp
from axism.tui.details_view import readable_time, session_details_text, trim_block
from axism.tui.footer import ContextFooter
from axism.tui.reader import ReaderScreen

CWD = "/home/alice/src/demo"
SLUG = "-home-alice-src-demo"
SID_A = "aaaaaaaa-0000-0000-0000-000000000001"
SID_B = "aaaaaaaa-0000-0000-0000-000000000002"
SIZES = [(80, 24), (120, 40)]


def _run(coro):
    return asyncio.run(coro)


def _user(sid: str, text: str, ts: str) -> dict:
    return {
        "type": "user", "sessionId": sid, "cwd": CWD, "isSidechain": False,
        "message": {"role": "user", "content": text},
        "uuid": f"u-{ts}", "timestamp": ts,
    }


def _assistant(sid: str, text: str, ts: str, model: str | None = None) -> dict:
    msg: dict = {"role": "assistant", "content": [{"type": "text", "text": text}]}
    if model:
        msg["model"] = model
    return {
        "type": "assistant", "sessionId": sid, "cwd": CWD, "isSidechain": False,
        "message": msg, "uuid": f"a-{ts}", "timestamp": ts,
    }


def _conversation(sid: str, title: str, pairs: list[tuple[str, str]], model=None) -> list:
    rows: list[dict] = [{"type": "custom-title", "customTitle": title, "sessionId": sid}]
    for i, (question, answer) in enumerate(pairs):
        rows.append(_user(sid, question, f"2026-01-01T00:{i:02d}:00.000Z"))
        rows.append(_assistant(sid, answer, f"2026-01-01T00:{i:02d}:30.000Z", model))
    return rows


def _settings(tmp_path: Path, monkeypatch, active: str, providers: dict) -> None:
    cfg = tmp_path / "axcfg"
    cfg.mkdir(exist_ok=True)
    base = {
        "claude_code": {"enabled": False, "config_dir": None},
        "cursor": {"enabled": False, "config_dir": None},
        "hermes": {"enabled": False, "config_dir": None},
        "opencode": {"enabled": False, "config_dir": None},
    }
    base.update(providers)
    (cfg / "settings.json").write_text(
        json.dumps({"active_provider": active, "providers": base})
    )
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("HOME", "/home/alice")
    real = tui.federate_merge_live
    monkeypatch.setattr(
        tui, "federate_merge_live", lambda provs, use_cli=True, **kw: real(provs, use_cli=False, **kw)
    )


def _setup_claude(
    tmp_path: Path, monkeypatch, *, pairs=None, model="model-x", second=True
) -> Path:
    claude = tmp_path / "claude"
    pairs = pairs or [("first question", "first answer"), ("second question", "second answer")]
    write_jsonl(
        claude / "projects" / SLUG / f"{SID_A}.jsonl",
        _conversation(SID_A, "Demo chat", pairs, model),
    )
    if second:
        write_jsonl(
            claude / "projects" / SLUG / f"{SID_B}.jsonl",
            _conversation(SID_B, "Other chat", [("other question", "other answer")]),
        )
        # Session A is the newest, so it is highlighted first.
        os.utime(claude / "projects" / SLUG / f"{SID_B}.jsonl", (1_000_000, 1_000_000))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    _settings(
        tmp_path, monkeypatch, "claude_code",
        {"claude_code": {"enabled": True, "config_dir": str(claude)}},
    )
    return claude


async def _settle(app: SessionManagerApp, pilot) -> None:
    """Let the details load (debounced worker) finish and the pane re-render."""
    await pilot.pause(0.25)
    await app.workers.wait_for_complete()
    await pilot.pause()
    await pilot.pause()


async def _focus_sessions(app: SessionManagerApp, pilot) -> DataTable:
    await pilot.pause()
    await pilot.pause()
    table = app.query_one("#sessions", DataTable)
    table.focus()
    await _settle(app, pilot)
    return table


def _detail_text(app: SessionManagerApp) -> str:
    return str(app.query_one("#detail-body").render())


# ---- pure helpers -------------------------------------------------------------


def test_readable_time_format():
    now = datetime(2026, 10, 28, 7, 1).astimezone()
    ts = lambda *a: datetime(*a).astimezone().timestamp()
    assert readable_time(ts(2026, 10, 28, 5, 1), now) == "today 05:01 (2 h ago)"
    assert readable_time(ts(2026, 10, 27, 18, 22), now) == "yesterday 18:22 (12 h ago)"
    assert readable_time(ts(2026, 10, 3, 9, 15), now) == "3 Oct 2026 09:15 (3 weeks ago)"
    assert readable_time(ts(2026, 10, 28, 7, 0, 30), now) == "today 07:00 (just now)"
    assert readable_time(ts(2026, 10, 28, 6, 41), now) == "today 06:41 (20 min ago)"
    assert readable_time(ts(2026, 10, 25, 7, 1), now) == "25 Oct 2026 07:01 (3 days ago)"
    assert readable_time(ts(2026, 6, 1, 12, 0), now) == "1 Jun 2026 12:00 (4 months ago)"
    assert readable_time(ts(2024, 1, 2, 3, 4), now) == "2 Jan 2024 03:04 (2 years ago)"
    # A clock that runs ahead never shows a negative age.
    assert readable_time(ts(2026, 10, 28, 7, 5), now) == "today 07:05 (just now)"


def test_trim_block_caps_lines_and_characters():
    text = "\n".join(f"line {i}" for i in range(20))
    trimmed = trim_block(text, max_lines=6)
    assert trimmed.splitlines()[:6] == [f"line {i}" for i in range(6)]
    assert trimmed.endswith("…")
    assert len(trimmed.splitlines()) == 7
    long = "x" * 5000
    out = trim_block(long, max_lines=6)
    assert len(out) < 700 and out.endswith("…")
    assert trim_block("\n\n  short  \n\n") == "short"
    assert trim_block("a\n\n\n\nb") == "a\n\nb"


def _meta(**kw) -> SessionMeta:
    base: dict = {
        "session_id": SID_A, "project_slug": SLUG, "cwd": CWD, "title": "Demo chat",
        "first_prompt": None, "transcript_path": Path("/home/alice/x.jsonl"),
        "mtime": datetime(2026, 10, 28, 6, 0).astimezone().timestamp(),
        "size_bytes": 2048, "provider": "claude_code",
    }
    base.update(kw)
    return SessionMeta(**base)


def _text(meta: SessionMeta, details: SessionDetails | None, *, database=False) -> str:
    return session_details_text(
        meta, details, agent="Claude Code", state="not running",
        database=database, now=datetime(2026, 10, 28, 7, 1).astimezone(), home="/home/alice",
    ).plain


def test_storage_line_variants():
    assert "2.0KB · transcript" in _text(_meta(), SessionDetails())
    out = _text(_meta(subagent_count=3, has_subdir=True), SessionDetails())
    assert "2.0KB · transcript + 3 sub-agent transcripts" in out
    out = _text(_meta(subagent_count=1, has_subdir=True), SessionDetails())
    assert "+ 1 sub-agent transcript" in out and "transcripts" not in out
    out = _text(_meta(), SessionDetails(), database=True)
    assert "2.0KB · stored in the agent's database" in out


def test_details_text_keeps_brackets_and_uses_tilde():
    meta = _meta(title="[b]not bold[/b]", cwd="/home/alice/src/[x]")
    out = _text(meta, SessionDetails(last_prompt="see [red]this[/red]"))
    assert "[b]not bold[/b]" in out
    assert "~/src/[x]" in out and "/home/alice/src" not in out
    assert "see [red]this[/red]" in out


def test_details_text_while_loading_and_on_odd_numbers():
    assert "Loading the conversation" in _text(_meta(), None)
    # Odd values from an agent's own records never break the pane.
    odd = SessionDetails(cost_usd="n/a", tokens_in="many", tokens_out=None)  # type: ignore[arg-type]
    out = _text(_meta(), odd)
    assert "cost" in out and "n/a" in out
    assert "many in" in out


# ---- details pane ---------------------------------------------------------------


@pytest.mark.parametrize("size", SIZES)
def test_details_shows_facts_and_last_turns(tmp_path, monkeypatch, size):
    long_reply = "\n".join(f"reply line {i}" for i in range(20))
    _setup_claude(
        tmp_path, monkeypatch,
        pairs=[("first question", "first answer"), ("second question", long_reply)],
    )

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await _focus_sessions(app, pilot)
            assert app.selected_session.session_id == SID_A
            text = _detail_text(app)
            assert "Demo chat" in text
            assert "Claude Code" in text
            assert "not running" in text
            assert "~/src/demo" in text and "/home/alice/src/demo" not in text
            assert "created" in text and "updated" in text
            assert " ago)" in text
            assert "· transcript" in text
            assert re.search(r"turns\s+4 messages \(2 prompts\)", text)
            assert "model-x" in text
            assert "First prompt" in text and "first question" in text
            assert "Last prompt" in text and "second question" in text
            assert "Last reply" in text and "reply line 5" in text
            assert "reply line 6" not in text
            # No paths, ids or mark state in the facts.
            assert ".jsonl" not in text
            assert SID_A not in text
            assert "selected" not in text
            assert "Fragments" not in text

    _run(main())


def test_details_hides_empty_optional_fields(tmp_path, monkeypatch):
    _setup_claude(tmp_path, monkeypatch, model=None, pairs=[("only question", "only answer")])

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await _focus_sessions(app, pilot)
            text = _detail_text(app)
            assert "only question" in text and "only answer" in text
            for word in ("model", "cost", "tokens"):
                assert word not in text, word
            # One prompt only: it is the last prompt, not repeated as the first.
            assert "First prompt" not in text
            assert "Last prompt" in text

    _run(main())


OC = "ses_a1AliceDetails01"


def test_details_db_agent_storage_line(tmp_path, monkeypatch):
    from tests.opencode_fixtures import make_opencode_db

    root = tmp_path / "oc" / "opencode"
    make_opencode_db(
        root,
        sessions=[
            {"id": OC, "project_id": "p1", "parent_id": None, "directory": CWD,
             "title": "Database chat", "time_created": 1_700_000_000_000,
             "time_updated": 1_700_000_500_000,
             "model": {"providerID": "anthropic", "id": "sonnet-x"},
             "cost": 0.0421, "tokens_input": 1200, "tokens_output": 345},
        ],
        projects=[{"id": "p1", "worktree": CWD}],
        messages=[
            {"session_id": OC, "type": "user", "seq": 1, "data": {"text": "hi there"}},
            {"session_id": OC, "type": "assistant", "seq": 2,
             "data": {"content": [{"type": "text", "text": "hello back"}]}},
        ],
    )
    _settings(
        tmp_path, monkeypatch, "opencode",
        {"opencode": {"enabled": True, "config_dir": str(root)}},
    )

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await _focus_sessions(app, pilot)
            text = _detail_text(app)
            assert "Database chat" in text
            assert "stored in the agent's database" in text
            assert "· transcript" not in text
            assert "anthropic/sonnet-x" in text
            assert "$0.04" in text
            assert "1,200 in" in text and "345 out" in text
            created = datetime.fromtimestamp(1_700_000_000).astimezone()
            assert f"{created.day} {created:%b} 2023" in text  # the agent's own record
            assert "hello back" in text

    _run(main())


def test_details_unreadable_transcript_note(tmp_path, monkeypatch):
    _setup_claude(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ClaudeCodeProvider, "session_details",
        lambda self, s: SessionDetails(note="Cannot read conversation: damaged file"),
    )

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await _focus_sessions(app, pilot)
            text = _detail_text(app)
            assert "Demo chat" in text and "~/src/demo" in text
            assert "Cannot read conversation: damaged file" in text
            assert "Last prompt" not in text and "Last reply" not in text

    _run(main())


def test_details_survive_provider_errors(tmp_path, monkeypatch):
    _setup_claude(tmp_path, monkeypatch)

    def boom(self, s):
        raise ValueError("odd number\nsecond line")

    monkeypatch.setattr(ClaudeCodeProvider, "session_details", boom)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(80, 24)) as pilot:
            table = await _focus_sessions(app, pilot)
            await pilot.press("j")
            await _settle(app, pilot)
            await pilot.press("k")
            await _settle(app, pilot)
            assert app.is_running
            assert table.cursor_row == 0
            text = _detail_text(app)
            assert "Demo chat" in text and "Claude Code" in text
            assert "Details unavailable: odd number" in text
            assert "second line" not in text

    _run(main())


def test_details_cached_until_mtime_changes(tmp_path, monkeypatch):
    claude = _setup_claude(tmp_path, monkeypatch)
    calls: list[str] = []
    real = ClaudeCodeProvider.session_details

    def counting(self, s):
        calls.append(s.session_id)
        return real(self, s)

    monkeypatch.setattr(ClaudeCodeProvider, "session_details", counting)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await _focus_sessions(app, pilot)
            assert calls.count(SID_A) == 1
            await pilot.press("j")
            await _settle(app, pilot)
            assert "other answer" in _detail_text(app)
            await pilot.press("k")
            await _settle(app, pilot)
            await pilot.press("r")
            await _settle(app, pilot)
            assert calls.count(SID_A) == 1
            assert calls.count(SID_B) == 1
            assert "second answer" in _detail_text(app)
            path = claude / "projects" / SLUG / f"{SID_A}.jsonl"
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(_user(SID_A, "third question", "2026-01-02T00:00:00Z")))
                fh.write("\n")
                fh.write(json.dumps(_assistant(SID_A, "third answer", "2026-01-02T00:01:00Z")))
                fh.write("\n")
            later = path.stat().st_mtime + 60
            os.utime(path, (later, later))
            await pilot.press("r")
            await _settle(app, pilot)
            assert calls.count(SID_A) == 2
            assert "third answer" in _detail_text(app)

    _run(main())


def test_details_scroll_kept_on_rerender_reset_on_new_session(tmp_path, monkeypatch):
    long_reply = "\n".join(f"reply line {i}" for i in range(6))
    _setup_claude(
        tmp_path, monkeypatch,
        pairs=[(f"question {i}\n" * 6, long_reply) for i in range(3)],
    )

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await _focus_sessions(app, pilot)
            await pilot.press("enter")
            await _settle(app, pilot)
            detail = app.query_one("#detail")
            assert app.focused is detail
            assert detail.max_scroll_y > 0
            await pilot.press("G")
            await pilot.pause()
            bottom = detail.scroll_y
            assert bottom > 0
            # Re-rendering the same session (refresh) keeps the position.
            await pilot.press("r")
            await _settle(app, pilot)
            assert detail.scroll_y == bottom
            # Another session starts at the top.
            app.query_one("#sessions").focus()
            await pilot.press("j")
            await _settle(app, pilot)
            assert detail.scroll_y == 0

    _run(main())


# ---- reader ---------------------------------------------------------------------


@pytest.mark.parametrize("size", SIZES)
def test_enter_in_details_opens_reader_and_esc_returns(tmp_path, monkeypatch, size):
    _setup_claude(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await _focus_sessions(app, pilot)
            await pilot.press("enter")
            assert app._focus_pane() == "detail"
            await pilot.press("enter")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, ReaderScreen)
            header = str(screen.query_one("#reader-header").render())
            assert "Demo chat · Claude Code" in header
            body = str(screen.query_one("#reader-body").render())
            for piece in ("You", "Agent", "first question", "first answer",
                          "second question", "second answer"):
                assert piece in body, piece
            assert body.index("first question") < body.index("second answer")
            footer = screen.query_one(ContextFooter)
            assert footer.scope == "reader"
            plain = str(footer.render())
            for piece in ("j/k Scroll", "g/G Top/End", "PgDn/PgUp Page", "Esc Back"):
                assert piece in plain, plain
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, ReaderScreen)
            assert app._focus_pane() == "detail"
            # q closes the reader too; it never quits the app from there.
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ReaderScreen)
            await pilot.press("q")
            await pilot.pause()
            assert not isinstance(app.screen, ReaderScreen)
            assert app.is_running

    _run(main())


@pytest.mark.parametrize("size", SIZES)
def test_reader_scrolls_with_vim_keys(tmp_path, monkeypatch, size):
    pairs = [(f"question {i}", f"answer {i}\nmore {i}") for i in range(60)]
    _setup_claude(tmp_path, monkeypatch, pairs=pairs)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await _focus_sessions(app, pilot)
            await pilot.press("enter", "enter")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, ReaderScreen)
            view = screen.scroll_view()
            assert app.focused is view
            assert view.max_scroll_y > 0
            assert view.scroll_y == 0
            await pilot.press("j")
            await pilot.pause()
            assert view.scroll_y == 1
            await pilot.press("G")
            await pilot.pause()
            assert view.scroll_y == view.max_scroll_y
            await pilot.press("g")
            await pilot.pause()
            assert view.scroll_y == 0
            await pilot.press("ctrl+d")
            await pilot.pause()
            assert view.scroll_y > 1
            await pilot.press("pagedown")
            await pilot.pause()
            assert view.scroll_y > 2
            assert "answer 59" in str(screen.query_one("#reader-body").render())

    _run(main())


@pytest.mark.parametrize(
    "error", [TranscriptUnavailable("Cannot read conversation: gone"), OSError("gone")]
)
def test_reader_shows_note_when_transcript_unreadable(tmp_path, monkeypatch, error):
    _setup_claude(tmp_path, monkeypatch)

    def fail(self, s):
        raise error

    monkeypatch.setattr(ClaudeCodeProvider, "transcript", fail)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await _focus_sessions(app, pilot)
            await pilot.press("enter", "enter")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, ReaderScreen)
            body = str(screen.query_one("#reader-body").render())
            assert "gone" in body
            assert len(body.strip().splitlines()) == 1
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, ReaderScreen)

    _run(main())


def test_reader_worker_error_shows_one_line_note(tmp_path, monkeypatch):
    _setup_claude(tmp_path, monkeypatch)

    def fail(self, s):
        raise RuntimeError("odd agent data")

    monkeypatch.setattr(ClaudeCodeProvider, "transcript", fail)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await _focus_sessions(app, pilot)
            await pilot.press("enter", "enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, ReaderScreen)
            body = str(screen.query_one("#reader-body").render())
            assert "odd agent data" in body
            assert len(body.strip().splitlines()) == 1
            assert app.is_running

    _run(main())


def test_enter_in_details_without_a_session_explains(tmp_path, monkeypatch):
    _setup_claude(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await _focus_sessions(app, pilot)
            app.selected_session = None
            app.query_one("#detail").focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen, ReaderScreen)
            assert "Select a session" in str(app.query_one("#toast").render())

    _run(main())


@pytest.mark.parametrize("size", SIZES)
def test_enter_on_the_marked_list_asks_to_clear_marks(tmp_path, monkeypatch, size):
    _setup_claude(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await _focus_sessions(app, pilot)
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert app._focus_pane() == "detail"
            assert "2 sessions selected" in _detail_text(app)
            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen, ReaderScreen)
            assert "Clear marks to read a single session" in str(
                app.query_one("#toast").render()
            )
            # With the marks cleared the pane shows one session, and Enter reads it.
            await pilot.press("c")
            await _settle(app, pilot)
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ReaderScreen)

    _run(main())


def test_claude_details_created_from_first_record(tmp_path):
    path = tmp_path / f"{SID_A}.jsonl"
    write_jsonl(path, _conversation(SID_A, "t", [("q", "a")]))
    meta = _meta(transcript_path=path, size_bytes=path.stat().st_size)
    d = ClaudeCodeProvider(config_dir=tmp_path).session_details(meta)
    assert d.created == datetime.fromisoformat("2026-01-01T00:00:00+00:00").timestamp()


# ---- agent text is shown verbatim, never parsed as markup ------------------------

TRICKY = (
    'echo "${arr[$i]}"',
    "Use [B]x[/B] or [$accent] ok",
    "[bold]not bold[/bold]",
    "line ends in \\",
)


def _assert_verbatim(rendered: str, pieces=TRICKY) -> None:
    for piece in pieces:
        assert piece in rendered, (piece, rendered)
    assert "\\\\" not in rendered


@pytest.mark.parametrize("size", SIZES)
def test_agent_text_is_verbatim_in_details_and_reader(tmp_path, monkeypatch, size):
    claude = tmp_path / "claude"
    rows = _conversation(
        SID_A, "[bold]not bold[/bold]",
        [
            ("first: " + TRICKY[1], "first reply " + TRICKY[0]),
            ("\n".join(TRICKY), "\n".join(TRICKY)),
        ],
        model="[$accent] model \\",
    )
    write_jsonl(claude / "projects" / SLUG / f"{SID_A}.jsonl", rows)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    _settings(
        tmp_path, monkeypatch, "claude_code",
        {"claude_code": {"enabled": True, "config_dir": str(claude)}},
    )

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await _focus_sessions(app, pilot)
            text = _detail_text(app)
            _assert_verbatim(text)
            assert "[$accent] model \\" in text
            assert "first: " + TRICKY[1] in text  # first prompt
            await pilot.press("enter", "enter")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, ReaderScreen)
            header = str(screen.query_one("#reader-header").render())
            assert "[bold]not bold[/bold] · Claude Code" in header
            body = str(screen.query_one("#reader-body").render())
            _assert_verbatim(body)
            assert "first reply " + TRICKY[0] in body

    _run(main())


def test_note_and_folder_are_verbatim(tmp_path, monkeypatch):
    note = "Cannot read [$accent] " + TRICKY[0] + " \\"
    _setup_claude(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ClaudeCodeProvider, "session_details", lambda self, s: SessionDetails(note=note)
    )

    def fail(self, s):
        raise TranscriptUnavailable(note)

    monkeypatch.setattr(ClaudeCodeProvider, "transcript", fail)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await _focus_sessions(app, pilot)
            assert note in _detail_text(app)
            await pilot.press("enter", "enter")
            await pilot.pause()
            assert note in str(app.screen.query_one("#reader-body").render())

    _run(main())
    meta = _meta(cwd="/home/alice/src/[$x]/[B]y[/B]\\")
    assert "~/src/[$x]/[B]y[/B]\\" in _text(meta, SessionDetails())


# ---- 0.2.1 follow-ups: retry, project view, turns, reader loading --------------


def test_turns_label_counts_messages_and_prompts():
    out = _text(_meta(), SessionDetails(turns=4, prompts=2))
    assert "4 messages (2 prompts)" in out
    out = _text(_meta(), SessionDetails(turns=1, prompts=1))
    assert "1 message (1 prompt)" in out


def test_provider_details_carry_prompt_count(tmp_path, monkeypatch):
    claude = _setup_claude(tmp_path, monkeypatch)
    prov = ClaudeCodeProvider(claude)
    meta = next(s for p in prov.discover() for s in p.sessions if s.session_id == SID_A)
    d = prov.session_details(meta)
    assert (d.turns, d.prompts) == (4, 2)


def test_refresh_retries_failed_details_only(tmp_path, monkeypatch):
    _setup_claude(tmp_path, monkeypatch)
    real = ClaudeCodeProvider.session_details
    calls: list[str] = []
    fail = {"on": True}

    def flaky(self, s):
        calls.append(s.session_id)
        if fail["on"] and s.session_id == SID_A:
            return SessionDetails(note="Cannot read conversation: busy")
        return real(self, s)

    monkeypatch.setattr(ClaudeCodeProvider, "session_details", flaky)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await _focus_sessions(app, pilot)
            assert "Cannot read conversation: busy" in _detail_text(app)
            await pilot.press("j")
            await _settle(app, pilot)
            await pilot.press("k")
            await _settle(app, pilot)
            assert calls.count(SID_A) == 1 and calls.count(SID_B) == 1
            fail["on"] = False
            await pilot.press("r")
            await _settle(app, pilot)
            assert calls.count(SID_A) == 2  # the failed one retried
            assert calls.count(SID_B) == 1  # the good one stayed cached
            assert "second answer" in _detail_text(app)

    _run(main())


def test_project_details_text_is_facts_style():
    from axism.tui.details_view import project_details_text

    out = project_details_text(
        name="[b]demo[/b]", folder="/home/alice/src/[b]demo[/b]", agent="Claude Code",
        sessions=3, running=1, size_bytes=2048,
        updated=datetime(2026, 10, 28, 6, 0).astimezone().timestamp(),
        now=datetime(2026, 10, 28, 7, 1).astimezone(), home="/home/alice",
    ).plain
    assert "[b]demo[/b]" in out and "~/src/[b]demo[/b]" in out
    assert "/home/alice" not in out
    assert "sessions" in out and "3" in out and "running" in out
    assert "2.0KB" in out and "today 06:00" in out
    assert "selected" not in out and "slug" not in out and "path" not in out


@pytest.mark.parametrize("size", SIZES)
def test_project_view_counts_service_only_running_sessions(tmp_path, monkeypatch, size):
    from axism.live import LiveSession

    _setup_claude(tmp_path, monkeypatch)
    # A live record with no pid, state running (as OpenCode service sessions).
    live = {SID_A: LiveSession(session_id=SID_A, pid=None, kind="interactive", state="running")}
    monkeypatch.setattr(tui, "federate_merge_live", lambda provs, use_cli=True, **_kw: live)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.query_one("#projects", DataTable).focus()
            await _settle(app, pilot)
            text = _detail_text(app)
            assert "demo" in text and "~/src/demo" in text
            assert re.search(r"sessions\s+2\b", text)
            assert re.search(r"running\s+1\b", text)
            assert "selected" not in text and "live/bg" not in text

    _run(main())


@pytest.mark.parametrize("size", SIZES)
def test_project_view_counts_sessions_running_only_via_can_stop(tmp_path, monkeypatch, size):
    from axism.live import LiveSession

    _setup_claude(tmp_path, monkeypatch)
    # No pid, state not "running": only the provider says it can be stopped.
    live = {SID_A: LiveSession(session_id=SID_A, pid=None, kind="interactive", state="idle")}
    monkeypatch.setattr(tui, "federate_merge_live", lambda provs, use_cli=True, **_kw: live)
    monkeypatch.setattr(ClaudeCodeProvider, "can_stop", lambda self, lv: lv is not None)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.query_one("#projects", DataTable).focus()
            await _settle(app, pilot)
            assert re.search(r"running\s+1\b", _detail_text(app))

    _run(main())


@pytest.mark.parametrize("size", SIZES)
def test_reader_loads_in_background_and_esc_works_while_loading(tmp_path, monkeypatch, size):
    import threading

    _setup_claude(tmp_path, monkeypatch)
    gate = threading.Event()
    real = ClaudeCodeProvider.transcript

    def slow(self, s):
        gate.wait(10)
        return real(self, s)

    monkeypatch.setattr(ClaudeCodeProvider, "transcript", slow)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await _focus_sessions(app, pilot)
            await pilot.press("enter")
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ReaderScreen)
            assert "Loading" in str(app.screen.query_one("#reader-body").render())
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, ReaderScreen)
            gate.set()
            await pilot.press("enter")
            await pilot.pause(0.3)
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, ReaderScreen)
            body = str(app.screen.query_one("#reader-body").render())
            assert "first question" in body and "Loading" not in body

    _run(main())
