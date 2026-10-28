"""Pilot tests for vim-style and gap navigation keys on the main screen."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest
from conftest import write_jsonl
from textual.widgets import DataTable, Input

import axism.tui.app as tui
from axism.tui.app import ConfirmDeleteScreen, SessionManagerApp

N_SESSIONS = 40
SIZES = [(80, 24), (120, 40)]


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    claude = tmp_path / "claude"
    for pi in range(3):
        cwd = f"/home/alice/src/nav{pi}"
        slug = cwd.replace("/", "-")
        count = N_SESSIONS if pi == 0 else 2
        for i in range(count):
            sid = str(uuid.UUID(int=pi * 1000 + i + 1))
            write_jsonl(
                claude / "projects" / slug / f"{sid}.jsonl",
                [
                    {"type": "custom-title", "customTitle": f"nav{pi}-{i:02d}", "sessionId": sid},
                    {
                        "type": "user",
                        "sessionId": sid,
                        "cwd": cwd,
                        "isSidechain": False,
                        "message": {"role": "user", "content": "hello"},
                        "uuid": str(uuid.uuid4()),
                        "timestamp": "2026-01-01T00:00:00.000Z",
                    },
                ],
            )
    cfg = tmp_path / "axcfg"
    cfg.mkdir()
    (cfg / "settings.json").write_text(
        json.dumps(
            {
                "active_provider": "claude_code",
                "providers": {
                    "claude_code": {"enabled": True, "config_dir": str(claude)},
                    "cursor": {"enabled": False, "config_dir": str(tmp_path / "cur")},
                    "hermes": {"enabled": False, "config_dir": str(tmp_path / "her")},
                },
            }
        )
    )
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    real = tui.federate_merge_live
    monkeypatch.setattr(
        tui, "federate_merge_live", lambda provs, use_cli=True: real(provs, use_cli=False)
    )
    return claude


def _run(coro):
    return asyncio.run(coro)


async def _start(app: SessionManagerApp, pilot, *, pane: str = "#sessions") -> DataTable:
    await pilot.pause()
    await pilot.pause()
    # Make sure the big project is selected so the sessions list is long.
    projects = app.query_one("#projects", DataTable)
    big = next(i for i, p in enumerate(app.projects) if len(p.sessions) == N_SESSIONS)
    projects.move_cursor(row=big)
    await pilot.pause()
    table = app.query_one(pane, DataTable)
    table.focus()
    await pilot.pause()
    return table


@pytest.mark.parametrize("size", SIZES)
def test_j_k_and_arrows_move_the_sessions_cursor(tmp_path, monkeypatch, size):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            table = await _start(app, pilot)
            assert table.cursor_row == 0
            await pilot.press("j")
            assert table.cursor_row == 1
            await pilot.press("J")
            assert table.cursor_row == 2
            await pilot.press("down")
            assert table.cursor_row == 3
            await pilot.press("k")
            assert table.cursor_row == 2
            await pilot.press("K")
            assert table.cursor_row == 1
            await pilot.press("up")
            assert table.cursor_row == 0

    _run(main())


@pytest.mark.parametrize("size", SIZES)
def test_g_home_G_end_jump(tmp_path, monkeypatch, size):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            table = await _start(app, pilot)
            last = table.row_count - 1
            assert last == N_SESSIONS - 1
            await pilot.press("G")
            assert table.cursor_row == last
            await pilot.press("g")
            assert table.cursor_row == 0
            await pilot.press("end")
            assert table.cursor_row == last
            await pilot.press("home")
            assert table.cursor_row == 0

    _run(main())


@pytest.mark.parametrize("size", SIZES)
def test_half_and_full_page_moves(tmp_path, monkeypatch, size):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            table = await _start(app, pilot)
            await pilot.press("ctrl+d")
            half = table.cursor_row
            assert half >= 1
            await pilot.press("ctrl+u")
            assert table.cursor_row == 0
            await pilot.press("pagedown")
            full = table.cursor_row
            assert full > half
            await pilot.press("pageup")
            assert table.cursor_row == 0
            # Half a page is about half of a full page.
            assert abs(full - 2 * half) <= 2

    _run(main())


def test_h_l_switch_panes(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await _start(app, pilot)
            assert app._focus_pane() == "sessions"
            await pilot.press("h")
            assert app._focus_pane() == "projects"
            await pilot.press("l")
            assert app._focus_pane() == "sessions"
            await pilot.press("l")
            assert app._focus_pane() == "detail"
            await pilot.press("H")
            assert app._focus_pane() == "sessions"
            await pilot.press("L")
            assert app._focus_pane() == "detail"

    _run(main())


def test_tab_cycles_projects_sessions_details(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await _start(app, pilot, pane="#projects")
            seen = [app._focus_pane()]
            for _ in range(3):
                await pilot.press("tab")
                seen.append(app._focus_pane())
            assert seen == ["projects", "sessions", "detail", "projects"]
            await pilot.press("shift+tab")
            assert app._focus_pane() == "detail"
            await pilot.press("shift+tab")
            assert app._focus_pane() == "sessions"

    _run(main())


def test_enter_moves_into_the_next_pane_and_esc_returns(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await _start(app, pilot, pane="#projects")
            await pilot.press("enter")
            assert app._focus_pane() == "sessions"
            assert isinstance(app.focused, DataTable) and app.focused.id == "sessions"
            await pilot.press("enter")
            assert app._focus_pane() == "detail"
            await pilot.press("enter")
            assert app._focus_pane() == "detail"
            toast = str(app.query_one("#toast").render())
            assert "Reader arrives in the details pane" in toast
            await pilot.press("escape")
            assert app._focus_pane() == "sessions"
            assert app.focused.id == "sessions"

    _run(main())


def test_details_pane_scrolls_with_navigation_keys(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await _start(app, pilot)
            await pilot.press("enter")
            detail = app.query_one("#detail")
            assert app.focused is detail
            row_before = app.query_one("#sessions", DataTable).cursor_row
            await pilot.press("j", "G", "g", "ctrl+d", "pagedown")
            # Keys in the details pane scroll it; they never move the list.
            assert app.query_one("#sessions", DataTable).cursor_row == row_before
            assert app.focused is detail

    _run(main())


def test_delete_key_opens_delete_dialog_for_highlighted_session(tmp_path, monkeypatch):
    claude = _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await _start(app, pilot)
            await pilot.press("j", "j", "j")
            await pilot.pause()
            target = app.selected_session
            assert target is not None
            await pilot.press("delete")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmDeleteScreen)
            assert target.display_title in app.screen.body
            await pilot.press("escape")
            await pilot.pause()
            return target.session_id

    sid = _run(main())
    assert list(claude.rglob(f"{sid}.jsonl"))


def test_typing_in_filter_only_changes_its_text(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await _start(app, pilot)
            await pilot.press("slash")
            flt = app.query_one("#sessions-filter", Input)
            assert app.focused is flt
            await pilot.press("h", "j", "k", "q", "d", "question_mark")
            await pilot.pause()
            assert flt.value == "hjkqd?"
            assert app.focused is flt
            assert len(app.screen_stack) == 1
            assert app.is_running
            # Esc clears the filter and returns to the list.
            await pilot.press("escape")
            await pilot.pause()
            assert flt.value == ""
            assert app.focused.id == "sessions"

    _run(main())


def test_gap_keys_stay_with_the_filter_input(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await _start(app, pilot)
            await pilot.press("slash")
            flt = app.query_one("#sessions-filter", Input)
            await pilot.press("a", "b", "c")
            await pilot.press("home")
            assert flt.cursor_position == 0
            await pilot.press("delete")
            assert flt.value == "bc"
            await pilot.press("end")
            assert flt.cursor_position == 2
            assert app.focused is flt
            assert len(app.screen_stack) == 1

    _run(main())


def test_narrow_terminal_skips_the_hidden_details_pane(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(60, 24)) as pilot:
            await _start(app, pilot, pane="#projects")
            assert not app.query_one("#detail").display
            await pilot.press("tab")
            assert app._focus_pane() == "sessions"
            await pilot.press("tab")
            assert app._focus_pane() == "projects"
            await pilot.press("l", "l")
            assert app._focus_pane() == "sessions"
            await pilot.press("enter")
            assert app._focus_pane() == "sessions"
            assert "hidden" in str(app.query_one("#toast").render())

    _run(main())
