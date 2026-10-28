"""Dialogs fit small terminals, list their keys and keep typed text in their inputs."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import write_jsonl
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Input, OptionList, Static

import axism.tui.app as tui
from axism.tui import keys
from axism.tui.app import SessionManagerApp, SettingsScreen
from axism.tui.dialogs import (
    AgentPickerScreen,
    ConfirmDeleteScreen,
    MoveTargetScreen,
    QuitConfirmScreen,
    RenameSessionScreen,
    ThemePickerScreen,
    VersionScreen,
)
from axism.tui.footer import ContextFooter
from axism.tui.help import HelpScreen, help_text

SIZES = [(80, 24), (120, 40)]
N_SESSIONS = 40
BIG_CWD = "/home/alice/src/big"

DIALOG_SCOPES = {
    ConfirmDeleteScreen: "dialog:confirm",
    QuitConfirmScreen: "dialog:quit",
    RenameSessionScreen: "dialog:rename",
    MoveTargetScreen: "dialog:move",
    AgentPickerScreen: "dialog:agent",
    ThemePickerScreen: "dialog:theme",
    VersionScreen: "dialog:version",
}


# ---- fixtures ---------------------------------------------------------------


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Three projects; the first has 40 sessions and a project memory folder."""
    claude = tmp_path / "claude"
    for pi, cwd in enumerate([BIG_CWD, "/home/alice/src/two", "/home/alice/src/three"]):
        slug = cwd.replace("/", "-")
        count = N_SESSIONS if pi == 0 else 2
        for i in range(count):
            sid = str(uuid.UUID(int=pi * 1000 + i + 1))
            write_jsonl(
                claude / "projects" / slug / f"{sid}.jsonl",
                [
                    {"type": "custom-title", "customTitle": f"s{pi}-{i:02d}", "sessionId": sid},
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
    mem = claude / "projects" / BIG_CWD.replace("/", "-") / "memory"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("# memory\n", encoding="utf-8")
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
                    "opencode": {"enabled": False, "config_dir": str(tmp_path / "oc")},
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


def _big_dir(claude: Path) -> Path:
    return claude / "projects" / BIG_CWD.replace("/", "-")


def _run(coro):
    return asyncio.run(coro)


async def _start(app: SessionManagerApp, pilot, pane: str) -> None:
    await pilot.pause()
    await pilot.pause()
    projects = app.query_one("#projects", DataTable)
    big = next(i for i, p in enumerate(app.projects) if len(p.sessions) == N_SESSIONS)
    projects.move_cursor(row=big)
    await pilot.pause()
    app.query_one(pane).focus()
    await pilot.pause()


def _footer_plain(screen: Screen) -> str:
    footer = screen.query_one(ContextFooter)
    return footer.render_for(
        footer.scope, footer._available, footer.content_region.width or 80,
        footer._hidden,
    ).plain


def _assert_fits(screen: Screen, size: tuple[int, int]) -> None:
    """The dialog is on screen; its body never overlaps the pinned key line."""
    width, height = size
    whole = screen.region
    assert whole.width == width and whole.height == height
    dialog = screen.query_one(".dialog")
    assert whole.contains_region(dialog.region), (dialog.region, size)
    assert dialog.region.height <= int(height * 0.9) + 1, (dialog.region, size)
    foot = screen.query_one(".dialog-foot")
    footer = screen.query_one(ContextFooter)
    assert dialog.region.contains_region(foot.region), (foot.region, dialog.region)
    assert foot.region.contains_region(footer.region)
    assert footer.region.height == 1
    assert _footer_plain(screen).strip()
    for body in screen.query(".dialog-body"):
        if body.region.height:
            assert body.region.bottom <= foot.region.y, (body.region, foot.region)
    for head in screen.query(".dialog-head"):
        for body in screen.query(".dialog-body"):
            if body.region.height:
                assert head.region.bottom <= body.region.y


# ---- registry ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("screen_cls", "scope"),
    [*DIALOG_SCOPES.items(), (HelpScreen, "help"), (SettingsScreen, "settings")],
)
def test_dialog_keys_come_from_the_registry(screen_cls, scope):
    expected = {(b.key, b.action) for b in keys.bindings_for(scope)}
    actual = {(b.key, b.action) for b in screen_cls.BINDINGS}
    assert actual == expected
    for _key, action in actual:
        assert callable(getattr(screen_cls, f"action_{action}", None)), action


def test_help_lists_filter_dialog_and_settings_keys():
    names = [name for name, _actions in keys.help_sections()]
    assert names == list(keys.GROUPS)
    text = help_text()
    assert text.index("\nFilter\n") < text.index("\nDialogs\n") < text.index("\nSettings\n")
    for action in keys.find("toggle_memory"), keys.find("confirm"), keys.find("decline"):
        assert action.group == "Dialogs"
    assert keys.find("toggle_memory").keys == ("m", "M")
    assert "m / M" in text


def test_dialog_actions_stay_out_of_the_palette():
    main = {"main", "projects", "sessions", "details", "filter"}
    for action in keys.REGISTRY:
        if action.palette:
            assert action.scopes & main, action.id


@pytest.mark.parametrize("scope", sorted({*DIALOG_SCOPES.values(), "settings"}))
def test_dialog_footer_fits_and_lists_only_dialog_keys(scope):
    footer = ContextFooter()
    text = footer.render_for(scope, lambda _aid: True, 66).plain
    assert text and len(text) <= 66, (scope, text)
    assert "? Help" not in text and "q Quit" not in text
    for action in keys.actions_for(scope):
        if action.footer_priority is not None:
            assert f"{keys.key_display(action.keys[0])} {action.label}" in text, (
                scope,
                action.id,
            )


# ---- fit at 80x24 and 120x40 ------------------------------------------------


def _long(n: int) -> str:
    return "\n".join(f"line {i:03d} of a long dialog body" for i in range(n))


def _memory(keep: bool) -> tuple[str, str]:
    return _long(120), tui._memory_line(1, keep)


DIALOG_FACTORIES: dict[str, Callable[[], Screen]] = {
    "session delete": lambda: ConfirmDeleteScreen(
        _long(120), title="Delete 40 sessions", warnings=["One running session is stopped."]
    ),
    "project delete": lambda: ConfirmDeleteScreen(
        _long(120),
        title="Delete project",
        warnings=[
            "Deletes via Claude Code.",
            "Deletes the backend's project directory and linked session data.",
            "Source workspace files on disk are not moved or removed.",
        ],
        project_danger=True,
        memory_toggle=_memory,
    ),
    "quit": lambda: QuitConfirmScreen(3),
    "rename": lambda: RenameSessionScreen("A session title", "abcdef12"),
    "move": lambda: MoveTargetScreen(
        "Move 1 session",
        [(f"/home/alice/src/p{i:02d}", f"p{i:02d}  /home/alice/src/p{i:02d}") for i in range(30)],
    ),
    "agent": lambda: AgentPickerScreen(
        "Memory merge — choose agent", [(f"a{i}", f"Agent {i}") for i in range(14)]
    ),
    "theme": lambda: ThemePickerScreen([f"theme-{i:02d}" for i in range(30)], "theme-05"),
    "version": lambda: VersionScreen(_long(80)),
}


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("name", list(DIALOG_FACTORIES))
def test_dialog_fits_and_shows_its_keys(tmp_path, monkeypatch, size, name):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            app.push_screen(DIALOG_FACTORIES[name]())
            await pilot.pause()
            await pilot.pause()
            _assert_fits(app.screen, size)
            prompt = app.screen.query("#confirm-prompt")
            if prompt:
                region = prompt.first().region
                assert app.screen.query_one(".dialog-foot").region.contains_region(region)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, type(DIALOG_FACTORIES[name]()))

    _run(main())


# ---- delete dialog ------------------------------------------------------------


@pytest.mark.parametrize("size", SIZES)
def test_delete_dialog_long_list_scrolls(tmp_path, monkeypatch, size):
    claude = _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await _start(app, pilot, "#sessions")
            await pilot.press("a")
            await pilot.pause()
            assert len(app.selected_session_ids) == N_SESSIONS
            await pilot.press("d")
            await pilot.pause()
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, ConfirmDeleteScreen)
            _assert_fits(screen, size)
            body = screen.query_one("#confirm-scroll", VerticalScroll)
            assert body.max_scroll_y > 0
            assert screen.focused is body
            await pilot.press("j")
            await pilot.pause()
            assert body.scroll_y == 1
            await pilot.press("G")
            await pilot.pause()
            assert body.scroll_y == body.max_scroll_y
            await pilot.press("k")
            await pilot.pause()
            assert body.scroll_y == body.max_scroll_y - 1
            await pilot.press("g")
            await pilot.pause()
            assert body.scroll_y == 0
            assert "y Yes" in _footer_plain(screen)
            assert "m Memory" not in _footer_plain(screen)
            await pilot.press("y")
            await pilot.pause()
            await pilot.pause()

    _run(main())
    assert not list(_big_dir(claude).glob("*.jsonl"))
    assert (_big_dir(claude) / "memory" / "MEMORY.md").is_file()


@pytest.mark.parametrize("size", SIZES)
def test_keep_memory_key_is_m(tmp_path, monkeypatch, size):
    claude = _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await _start(app, pilot, "#projects")
            await pilot.press("d")
            await pilot.pause()
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, ConfirmDeleteScreen)
            _assert_fits(screen, size)
            line = screen.query_one("#confirm-memory", Static)
            assert "DELETED" in str(line.render())
            assert "m Memory" in _footer_plain(screen)
            # k scrolls (vim up); it no longer toggles memory.
            body = screen.query_one("#confirm-scroll", VerticalScroll)
            assert body.max_scroll_y > 0
            await pilot.press("G", "k")
            await pilot.pause()
            assert body.scroll_y == body.max_scroll_y - 1
            assert "DELETED" in str(line.render())
            await pilot.press("m")
            await pilot.pause()
            text = str(line.render())
            assert "KEPT" in text
            assert "m to delete it too" in text
            await pilot.press("M")
            await pilot.pause()
            assert "DELETED" in str(line.render())
            assert "m to keep it" in str(line.render())
            await pilot.press("m")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            await pilot.pause()

    _run(main())
    assert (_big_dir(claude) / "memory" / "MEMORY.md").is_file()
    assert not list(_big_dir(claude).glob("*.jsonl"))


# ---- move dialog --------------------------------------------------------------


@pytest.mark.parametrize("size", SIZES)
def test_move_dialog_project_list_visible_at_80x24(tmp_path, monkeypatch, size):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            # The real flow: the other projects are listed and visible.
            await _start(app, pilot, "#sessions")
            await pilot.press("m")
            await pilot.pause()
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, MoveTargetScreen)
            _assert_fits(screen, size)
            option_list = screen.query_one("#move-list", OptionList)
            assert option_list.option_count == 2
            assert option_list.region.height >= option_list.option_count
            assert screen.query_one("#move-input", Input).region.height >= 1
            await pilot.press("escape")
            await pilot.pause()

            # A long list: at least five rows visible, j/G move the highlight
            # and keep it in view.
            app.push_screen(DIALOG_FACTORIES["move"]())
            await pilot.pause()
            screen = app.screen
            _assert_fits(screen, size)
            option_list = screen.query_one("#move-list", OptionList)
            assert option_list.region.height >= 5
            assert option_list.highlighted == 0
            await pilot.press("j")
            await pilot.pause()
            assert option_list.highlighted == 1
            await pilot.press("G")
            await pilot.pause()
            assert option_list.highlighted == 29
            assert option_list.scroll_y == option_list.max_scroll_y > 0
            await pilot.press("g")
            await pilot.pause()
            assert option_list.highlighted == 0
            await pilot.press("escape")
            await pilot.pause()

    _run(main())


def test_picker_lists_move_with_vim_keys(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    picked: list[str | None] = []

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.push_screen(DIALOG_FACTORIES["theme"](), picked.append)
            await pilot.pause()
            option_list = app.screen.query_one("#theme-list", OptionList)
            assert option_list.highlighted == 5
            await pilot.press("j", "j", "k")
            await pilot.pause()
            assert option_list.highlighted == 6
            await pilot.press("enter")
            await pilot.pause()

    _run(main())
    assert picked == ["theme-06"]


# ---- inputs win ---------------------------------------------------------------

TYPED = "jkmynqgGhlvtdo?"


def test_typing_in_rename_and_move_inputs_never_triggers_keys(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    results: list[str | None] = []

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await _start(app, pilot, "#sessions")
            title = app.selected_session.display_title

            # Rename: every printable key goes into the title.
            await pilot.press("n")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, RenameSessionScreen)
            _assert_fits(screen, (80, 24))
            field = screen.query_one("#rename-input", Input)
            assert screen.focused is field
            await pilot.press(*TYPED, "space", "Y", "N", "M", "Q")
            await pilot.pause()
            assert app.screen is screen
            assert field.value == f"{title}{TYPED} YNMQ"
            # Home / End edit the text instead of moving anything.
            await pilot.press("home")
            await pilot.pause()
            assert field.cursor_position == 0
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, RenameSessionScreen)
            assert app.selected_session.display_title == title

            # Move: the path field keeps typed keys too; Tab / Shift+Tab / Esc act.
            await pilot.press("m")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, MoveTargetScreen)
            option_list = screen.query_one("#move-list", OptionList)
            await pilot.press("tab")
            await pilot.pause()
            path = screen.query_one("#move-input", Input)
            assert screen.focused is path
            highlighted = option_list.highlighted
            await pilot.press("slash", *TYPED, "G", "Y")
            await pilot.pause()
            assert app.screen is screen
            assert path.value == f"/{TYPED}GY"
            assert option_list.highlighted == highlighted
            await pilot.press("home")
            await pilot.pause()
            assert path.cursor_position == 0
            assert option_list.highlighted == highlighted
            await pilot.press("shift+tab")
            await pilot.pause()
            assert screen.focused is option_list
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, MoveTargetScreen)

            # The quit dialog: y / n / Esc from the registry.
            app.push_screen(QuitConfirmScreen(1), results.append)
            await pilot.pause()
            assert "y Yes" in _footer_plain(app.screen)
            await pilot.press("N")
            await pilot.pause()

    _run(main())
    assert results == [False]


def test_typing_q_in_the_settings_path_does_not_close_settings(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("comma")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, SettingsScreen)
            await pilot.press("tab")
            await pilot.pause()
            path = screen.query_one("#settings-path", Input)
            assert screen.focused is path
            path.value = ""
            await pilot.press("q", "Q", "space", "x")
            await pilot.pause()
            assert app.screen is screen
            assert path.value == "qQ x"
            await pilot.press("escape")
            await pilot.pause()

    _run(main())
