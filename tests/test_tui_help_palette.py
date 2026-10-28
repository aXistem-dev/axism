"""Help generated from the key registry, the command palette and quit confirm."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from conftest import build_fake_config
from textual.command import CommandList, CommandPalette
from textual.containers import VerticalScroll
from textual.widgets import Input

import axism.tui.app as tui
from axism.tui import keys
from axism.tui.app import SessionManagerApp
from axism.tui.dialogs import QuitConfirmScreen
from axism.tui.help import HelpScreen, help_text

SIZES = [(80, 24), (120, 40)]


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    claude = tmp_path / "claude"
    claude.mkdir()
    build_fake_config(claude)
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


def _run(coro):
    return asyncio.run(coro)


async def _focus(app: SessionManagerApp, pilot, selector: str) -> None:
    await pilot.pause()
    await pilot.pause()
    app.query_one(selector).focus()
    await pilot.pause()


def _toast(app: SessionManagerApp) -> str:
    return str(app.query_one("#toast").render())


async def _palette_options(app: SessionManagerApp, pilot) -> list[str]:
    for _ in range(40):
        await pilot.pause(0.05)
        command_list = app.screen.query_one(CommandList)
        if command_list.option_count:
            break
    command_list = app.screen.query_one(CommandList)
    return [
        # The first line is the command's name; the keys follow as help text.
        str(command_list.get_option_at_index(i).prompt).split("\n")[0]
        for i in range(command_list.option_count)
    ]


# ---- help --------------------------------------------------------------------


def test_help_lists_every_registry_key():
    text = help_text()
    for action in keys.REGISTRY:
        row = " / ".join(keys.key_display(key) for key in action.keys)
        assert row in text, (action.id, row)
        assert action.description in text, action.id
    # Each action is listed once, under its group.
    listed = [a.id for _group, actions in keys.help_sections() for a in actions]
    assert sorted(listed) == sorted(a.id for a in keys.REGISTRY)


def test_help_groups_in_order():
    sections = keys.help_sections()
    names = [name for name, _actions in sections]
    assert names == [g for g in keys.GROUPS if g in names]
    for name, actions in sections:
        assert actions, name
        assert all(a.group == name for a in actions)
    text = help_text()
    positions = [text.index(f"\n{name}\n") for name in names]
    assert positions == sorted(positions)
    # The notes worth keeping from the old legend.
    assert "per pane" in text
    assert "different agents" in text
    assert "G" in text and "T" in text and "Uppercase" in text


@pytest.mark.parametrize("size", SIZES)
def test_help_scrolls_and_closes(tmp_path, monkeypatch, size):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await _focus(app, pilot, "#sessions")
            await pilot.press("question_mark")
            await pilot.pause()
            assert isinstance(app.screen, HelpScreen)
            scroll = app.screen.query_one("#help-scroll", VerticalScroll)
            assert app.screen.focused is scroll
            assert scroll.max_scroll_y > 0
            await pilot.press("j")
            await pilot.pause()
            assert scroll.scroll_y == 1
            await pilot.press("G")
            await pilot.pause()
            assert scroll.scroll_y == scroll.max_scroll_y
            await pilot.press("g")
            await pilot.pause()
            assert scroll.scroll_y == 0
            await pilot.press("ctrl+d")
            await pilot.pause()
            assert scroll.scroll_y > 1
            await pilot.press("ctrl+u")
            await pilot.pause()
            assert scroll.scroll_y == 0
            await pilot.press("pagedown")
            await pilot.pause()
            assert scroll.scroll_y > 1
            await pilot.press("end")
            await pilot.pause()
            assert scroll.scroll_y == scroll.max_scroll_y
            await pilot.press("home")
            await pilot.pause()
            assert scroll.scroll_y == 0
            # The close hint stays on screen below the scroll view.
            hint = app.screen.query_one("#help-hint")
            assert hint.region.bottom <= size[1]
            assert "Esc" in str(hint.render())
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, HelpScreen)
            for key in ("q", "question_mark"):
                await pilot.press("question_mark")
                await pilot.pause()
                assert isinstance(app.screen, HelpScreen)
                await pilot.press(key)
                await pilot.pause()
                assert not isinstance(app.screen, HelpScreen)
            assert app.is_running

    _run(main())


# ---- command palette ---------------------------------------------------------


@pytest.mark.parametrize("size", SIZES)
def test_palette_lists_actions(tmp_path, monkeypatch, size):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await _focus(app, pilot, "#sessions")
            await pilot.press("ctrl+p")
            await pilot.pause()
            assert isinstance(app.screen, CommandPalette)
            listed = await _palette_options(app, pilot)
            # Only our registry actions: Textual's own system commands are hidden.
            expected = [a.description for a in keys.REGISTRY if a.palette]
            assert sorted(listed) == sorted(expected)
            await pilot.press(*"delete")
            options = await _palette_options(app, pilot)
            assert keys.find("delete_items").description in options
            await pilot.press("escape")
            await pilot.pause()

    _run(main())


def test_palette_opens_from_the_filter_and_typing_still_types(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await _focus(app, pilot, "#sessions-filter")
            await pilot.press("q", "j", "question_mark")
            await pilot.pause()
            assert app.query_one("#sessions-filter", Input).value == "qj?"
            assert app.is_running
            await pilot.press("ctrl+p")
            await pilot.pause()
            assert isinstance(app.screen, CommandPalette)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, CommandPalette)

    _run(main())


@pytest.mark.parametrize("size", SIZES)
def test_palette_unavailable_shows_reason(tmp_path, monkeypatch, size):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await _focus(app, pilot, "#projects")
            await pilot.press("ctrl+p")
            await pilot.pause()
            await pilot.press(*"open the highlighted session")
            options = await _palette_options(app, pilot)
            assert options[0] == keys.find("resume_session").description
            await pilot.press("down", "enter")
            for _ in range(10):
                await pilot.pause(0.05)
            assert not isinstance(app.screen, CommandPalette)
            assert app.is_running
            assert "Open works in the Sessions pane" in _toast(app)

    _run(main())


def test_palette_runs_an_available_action(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await _focus(app, pilot, "#sessions")
            await pilot.press("ctrl+p")
            await pilot.pause()
            await pilot.press(*"show every key")
            options = await _palette_options(app, pilot)
            assert options[0] == keys.find("show_help").description
            await pilot.press("down", "enter")
            for _ in range(10):
                await pilot.pause(0.05)
            assert isinstance(app.screen, HelpScreen)

    _run(main())


# ---- quit --------------------------------------------------------------------


@pytest.mark.parametrize("size", SIZES)
def test_quit_with_marks_asks(tmp_path, monkeypatch, size):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        exits: list[bool] = []
        monkeypatch.setattr(app, "exit", lambda *a, **k: exits.append(True))
        async with app.run_test(size=size) as pilot:
            await _focus(app, pilot, "#sessions")
            await pilot.press("space")
            await pilot.pause()
            assert len(app.selected_session_ids) == 1
            await pilot.press("q")
            await pilot.pause()
            assert isinstance(app.screen, QuitConfirmScreen)
            assert "Quit and drop 1 mark?" in str(
                app.screen.query_one("#quit-prompt").render()
            )
            await pilot.press("n")
            await pilot.pause()
            assert not isinstance(app.screen, QuitConfirmScreen)
            assert exits == [] and app.selected_session_ids
            await pilot.press("Q")
            await pilot.pause()
            assert isinstance(app.screen, QuitConfirmScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert exits == []
            await _focus(app, pilot, "#projects")
            await pilot.press("space")
            await pilot.pause()
            await pilot.press("q")
            await pilot.pause()
            assert "Quit and drop 2 marks?" in str(
                app.screen.query_one("#quit-prompt").render()
            )
            await pilot.press("y")
            await pilot.pause()
            assert exits == [True]

    _run(main())


@pytest.mark.parametrize("size", SIZES)
def test_quit_without_marks_exits(tmp_path, monkeypatch, size):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        exits: list[bool] = []
        monkeypatch.setattr(app, "exit", lambda *a, **k: exits.append(True))
        async with app.run_test(size=size) as pilot:
            await _focus(app, pilot, "#sessions")
            await pilot.press("q")
            await pilot.pause()
            assert not isinstance(app.screen, QuitConfirmScreen)
            assert exits == [True]

    _run(main())


def test_palette_lists_footer_actions_first(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await _focus(app, pilot, "#sessions")
            await pilot.press("ctrl+p")
            listed = await _palette_options(app, pilot)
            footer = sorted(
                (a for a in keys.REGISTRY if a.palette and a.footer_priority is not None),
                key=lambda a: a.footer_priority,
            )
            assert listed[: len(footer)] == [a.description for a in footer]
            await pilot.press("escape")

    _run(main())


def test_help_note_says_what_esc_does_in_text_fields():
    text = " ".join(help_text().split())
    assert "Esc leaves it" not in text
    assert "In the sessions filter, Esc clears the filter and leaves it" in text
    assert "Esc cancels the dialog" in text
