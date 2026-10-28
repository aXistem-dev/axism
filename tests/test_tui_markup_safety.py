"""Session titles, folders, paths and messages are shown as written.

Square brackets, ``$`` and backslashes in anything that comes from a session,
an agent, a folder or the user must never be read as markup: they could
crash the app (``[/]``), vanish (``[x]``) or turn into live actions
(``[@click=…]``).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from conftest import write_jsonl
from rich.text import Text
from textual.content import Content
from textual.visual import visualize
from textual.widgets import DataTable, Input, OptionList

import axism.tui.app as tui
from axism.tui.app import SessionManagerApp
from axism.tui.dialogs import (
    AgentPickerScreen,
    ConfirmDeleteScreen,
    MoveTargetScreen,
    RenameSessionScreen,
)

SIZES = [(80, 24), (120, 40)]

TITLE = "fix [/] bug [x] [$accent] [@click=app.quit] \\"
# A Claude Code project folder name cannot hold "/", so this one has the rest.
ODD_SLUG = "-home-alice-src-[x]-[$accent]-[@click=app.quit]demo"
ODD_FOLDER = "/home/alice/src/[x]/[$accent]/[@click=app.quit]demo"
ODD_CWD = "/home/alice/src/[/]demo"
PLAIN_SLUG = "-home-alice-src-plain"
SID_ODD = "aaaaaaaa-0000-0000-0000-000000000001"
SID_PLAIN = "aaaaaaaa-0000-0000-0000-000000000002"
DEST = "/home/alice/dest/[/]x [b]y\\"


def _run(coro):
    return asyncio.run(coro)


def _conversation(sid: str, title: str, cwd: str) -> list[dict]:
    return [
        {"type": "custom-title", "customTitle": title, "sessionId": sid},
        {
            "type": "user", "sessionId": sid, "cwd": cwd, "isSidechain": False,
            "message": {"role": "user", "content": "q [/] " + title},
            "uuid": f"u-{sid}", "timestamp": "2026-01-01T00:00:00.000Z",
        },
    ]


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A Claude Code folder whose own path holds ``[/]``, one project with an
    odd folder and title, and one plain project."""
    claude = tmp_path / "cl[" / "]aude"
    write_jsonl(
        claude / "projects" / ODD_SLUG / f"{SID_ODD}.jsonl",
        _conversation(SID_ODD, TITLE, ODD_CWD),
    )
    write_jsonl(
        claude / "projects" / PLAIN_SLUG / f"{SID_PLAIN}.jsonl",
        _conversation(SID_PLAIN, "plain title", "/home/alice/src/plain"),
    )
    cfg = tmp_path / "axcfg"
    cfg.mkdir()
    (cfg / "settings.json").write_text(
        json.dumps(
            {
                "active_provider": "claude_code",
                "providers": {
                    "claude_code": {"enabled": True, "config_dir": str(claude)},
                    "cursor": {"enabled": False},
                    "hermes": {"enabled": False},
                    "opencode": {"enabled": False},
                },
            }
        )
    )
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setenv("HOME", "/home/alice")
    real = tui.federate_merge_live
    monkeypatch.setattr(
        tui, "federate_merge_live", lambda provs, use_cli=True: real(provs, use_cli=False)
    )
    return claude


def _cell_text(cell: object) -> str:
    """What a DataTable shows for ``cell`` (strings are read as markup there)."""
    if isinstance(cell, str):
        return Text.from_markup(cell).plain
    assert isinstance(cell, Text), type(cell)
    return cell.plain


def _option_texts(option_list: OptionList) -> list[str]:
    return [
        str(visualize(option_list, option.prompt, markup=option_list._markup))
        for option in option_list.options
    ]


def _shown(app_or_screen, selector: str) -> str:
    return str(app_or_screen.query_one(selector).render())


async def _start(app: SessionManagerApp, pilot) -> None:
    await pilot.pause()
    await pilot.pause()


async def _highlight_project(app: SessionManagerApp, pilot, slug: str) -> None:
    table = app.query_one("#projects", DataTable)
    keys = [p.key for p in app.projects]
    match = next(k for k in keys if k.endswith(slug))
    table.focus()
    table.move_cursor(row=keys.index(match), animate=False)
    await pilot.pause()
    await pilot.pause()


async def _focus_sessions_of(app: SessionManagerApp, pilot, slug: str) -> DataTable:
    await _highlight_project(app, pilot, slug)
    sessions = app.query_one("#sessions", DataTable)
    sessions.focus()
    await pilot.pause()
    await pilot.pause()
    return sessions


@pytest.mark.parametrize("size", SIZES)
def test_odd_title_and_folders_show_verbatim_in_lists_and_details(
    tmp_path, monkeypatch, size
):
    claude = _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await _start(app, pilot)
            # Status line names the folder that holds "[/]".
            assert str(claude) in _shown(app, "#status")
            # Projects table: the odd folder, cut to the column, as written.
            projects = app.query_one("#projects", DataTable)
            odd = next(p for p in app.projects if p.slug == ODD_SLUG)
            label = _cell_text(projects.get_cell(odd.key, "Project"))
            assert label.rstrip("…").lstrip("…/") in ODD_FOLDER, label
            # Project details (projects pane focused).
            await _highlight_project(app, pilot, ODD_SLUG)
            detail = _shown(app, "#detail-body")
            assert ODD_FOLDER.replace("/home/alice", "~", 1) in detail
            # The project name (last folder component) is shown as written.
            assert "[@click=app.quit]demo" in detail
            # Sessions table: the title, cut to the column, as written.
            sessions = await _focus_sessions_of(app, pilot, ODD_SLUG)
            shown = _cell_text(sessions.get_cell(app.selected_session.key, "Title"))
            assert TITLE.startswith(shown.rstrip("…")), shown
            # Marks: projects pane marked list, then sessions marked list.
            await _highlight_project(app, pilot, ODD_SLUG)
            await pilot.press("a")
            await pilot.pause()
            marked = _shown(app, "#detail-body")
            assert ODD_FOLDER[:36] in marked
            await pilot.press("c")
            await _focus_sessions_of(app, pilot, ODD_SLUG)
            app.selected_session_ids.add(
                next(p for p in app.projects if p.slug == PLAIN_SLUG).sessions[0].key
            )
            await pilot.press("a")
            await pilot.pause()
            assert TITLE[:40] in _shown(app, "#detail-body")

    _run(main())


@pytest.mark.parametrize("size", SIZES)
def test_filter_text_with_brackets_is_shown_as_typed(tmp_path, monkeypatch, size):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await _start(app, pilot)
            await _focus_sessions_of(app, pilot, ODD_SLUG)
            await pilot.press("slash")
            await pilot.press(*"[/]")
            await pilot.pause()
            assert app.query_one("#sessions-filter", Input).value == "[/]"
            assert 'filter="[/]"' in _shown(app, "#sessions-meta")
            # The odd title matches the filter and is listed.
            assert app.query_one("#sessions", DataTable).row_count == 1

    _run(main())


@pytest.mark.parametrize("size", SIZES)
def test_delete_dialogs_show_titles_and_folders_verbatim(tmp_path, monkeypatch, size):
    claude = _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await _start(app, pilot)
            await _focus_sessions_of(app, pilot, ODD_SLUG)
            # Both sessions: the odd title, and the plain one whose dry-run
            # lines name its files under the folder that holds "[/]".
            plain = next(p for p in app.projects if p.slug == PLAIN_SLUG)
            app.selected_session_ids.update({app.selected_session.key, plain.sessions[0].key})
            await pilot.press("d")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmDeleteScreen)
            body = _shown(app.screen, "#confirm-body")
            assert TITLE[:44] in body
            assert str(claude) in body
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, ConfirmDeleteScreen)
            await _highlight_project(app, pilot, ODD_SLUG)
            await pilot.press("d")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmDeleteScreen)
            body = _shown(app.screen, "#confirm-body")
            assert ODD_SLUG in body and ODD_FOLDER in body
            await pilot.press("escape")
            await pilot.pause()

    _run(main())


@pytest.mark.parametrize("size", SIZES)
def test_move_dialogs_show_folders_and_typed_destination_verbatim(
    tmp_path, monkeypatch, size
):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await _start(app, pilot)
            # Session move from the plain project: the odd folder is a choice.
            await _focus_sessions_of(app, pilot, PLAIN_SLUG)
            await pilot.press("m")
            await pilot.pause()
            assert isinstance(app.screen, MoveTargetScreen)
            choices = _option_texts(app.screen.query_one("#move-list", OptionList))
            assert any(ODD_FOLDER in c for c in choices), choices
            await pilot.press("tab")
            app.screen.query_one("#move-input", Input).value = DEST
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmDeleteScreen)
            body = _shown(app.screen, "#confirm-body")
            assert DEST in body and "plain title" in body
            await pilot.press("escape")
            await pilot.pause()
            # Project move of the odd project: its folder is a source line.
            await _highlight_project(app, pilot, ODD_SLUG)
            await pilot.press("m")
            await pilot.pause()
            assert isinstance(app.screen, MoveTargetScreen)
            await pilot.press("tab")
            app.screen.query_one("#move-input", Input).value = DEST
            await pilot.press("enter")
            await pilot.pause()
            body = _shown(app.screen, "#confirm-body")
            assert DEST in body and ODD_FOLDER in body
            await pilot.press("escape")
            await pilot.pause()

    _run(main())


@pytest.mark.parametrize("size", SIZES)
def test_rename_to_odd_title_shows_it_and_the_app_starts_again(
    tmp_path, monkeypatch, size
):
    _setup(tmp_path, monkeypatch)
    new_title = "new [/] title [x] [@click=app.quit]"

    async def rename():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await _start(app, pilot)
            await _focus_sessions_of(app, pilot, PLAIN_SLUG)
            await pilot.press("n")
            await pilot.pause()
            assert isinstance(app.screen, RenameSessionScreen)
            app.screen.query_one("#rename-input", Input).value = new_title
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            assert new_title in _shown(app, "#toast")

    async def restart():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await _start(app, pilot)
            await _focus_sessions_of(app, pilot, PLAIN_SLUG)
            table = app.query_one("#sessions", DataTable)
            cell = _cell_text(table.get_cell(app.selected_session.key, "Title"))
            assert new_title.startswith(cell.rstrip("…"))
            # Exactly as typed: the toast of a stop names the title the same way.
            app._show_toast(f"Stopped: {new_title}", success=True)
            await pilot.pause()
            assert f"Stopped: {new_title}" in _shown(app, "#toast")

    _run(rename())
    _run(restart())


def test_stop_dialog_body_keeps_titles_verbatim():
    from axism.live import LiveSession

    meta = type("M", (), {})()
    meta.display_title = TITLE
    meta.session_id = SID_ODD
    live = LiveSession(session_id=SID_ODD, kind="background", state="[/]busy",
                       daemon_short="[x]abc")
    body = tui._format_stop_confirm([(meta, live)])
    assert isinstance(body, Content)
    text = str(body)
    assert TITLE[:44] in text and "[/]busy" in text and "[x]abc" in text
    assert "1 live session(s)" in text


@pytest.mark.parametrize("size", SIZES)
def test_plain_text_dialog_parts_are_never_markup(tmp_path, monkeypatch, size):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await _start(app, pilot)
            app.push_screen(
                ConfirmDeleteScreen(
                    "plain [/] body", title="t [/] title", warnings=["warn [/] here"],
                    project_danger=True,
                )
            )
            await pilot.pause()
            assert "plain [/] body" in _shown(app.screen, "#confirm-body")
            assert "t [/] title" in _shown(app.screen, "#confirm-title")
            assert "warn [/] here" in _shown(app.screen, "#confirm-warn")
            await pilot.press("escape")
            await pilot.pause()
            app.push_screen(AgentPickerScreen("Pick [/]", [("a", "Agent [/] [x]a")]))
            await pilot.pause()
            assert "Pick [/]" in _shown(app.screen, "#agent-picker-title")
            assert _option_texts(app.screen.query_one("#agent-list", OptionList)) == [
                "Agent [/] [x]a"
            ]
            await pilot.press("escape")
            await pilot.pause()
            app.push_screen(RenameSessionScreen("t", "[/]abc"))
            await pilot.pause()
            assert "[/]abc" in _shown(app.screen, "#rename-title")
            await pilot.press("escape")
            await pilot.pause()
            app.push_screen(MoveTargetScreen("Move [/]", [("/a/[/]", "/a/[/] [x]")]))
            await pilot.pause()
            assert "Move [/]" in _shown(app.screen, "#move-title")
            assert _option_texts(app.screen.query_one("#move-list", OptionList)) == [
                "/a/[/] [x]"
            ]
            await pilot.press("escape")
            await pilot.pause()

    _run(main())


def test_version_dialog_shows_folders_verbatim(tmp_path, monkeypatch):
    claude = _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await _start(app, pilot)
            app.action_show_version()
            await pilot.pause()
            assert str(claude) in _shown(app.screen, "#version-body")

    _run(main())


def test_empty_inventory_names_the_folder_verbatim(tmp_path, monkeypatch):
    claude = _setup(tmp_path, monkeypatch)
    for child in (claude / "projects").iterdir():
        for f in child.iterdir():
            f.unlink()
        child.rmdir()

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await _start(app, pilot)
            text = _shown(app, "#detail-body")
            assert "No projects found" in text and str(claude) in text

    _run(main())


def test_settings_missing_folder_warning_is_verbatim(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    from axism.tui.settings_screen import SettingsScreen

    missing = "/home/alice/[x]/[/]missing\\"

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await _start(app, pilot)
            await pilot.press("comma")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, SettingsScreen)
            await pilot.press("tab")
            screen.query_one("#settings-path", Input).value = missing
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmDeleteScreen)
            warn = _shown(app.screen, "#confirm-warn")
            assert f"Claude Code: {missing} does not exist" in warn, warn
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

    _run(main())


def test_detail_pane_takes_content_only(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await _start(app, pilot)
            with pytest.raises(TypeError):
                app.query_one("#detail", tui.DetailPane).update("[/]")

    _run(main())
