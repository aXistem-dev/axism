"""Context footer and the reasons shown for unavailable keys."""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

import pytest
from conftest import build_fake_config
from rich.text import Text
from textual.widgets import DataTable, Footer

import axism.tui.app as tui
from axism.tui import keys
from axism.tui.app import SessionManagerApp
from axism.tui.dialogs import ConfirmDeleteScreen, MoveTargetScreen
from axism.tui.footer import ContextFooter

SIZES = [(80, 24), (120, 40)]
MAIN_SCOPES = ("main", "projects", "sessions", "details")


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
                },
            }
        )
    )
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    real = tui.federate_merge_live
    monkeypatch.setattr(
        tui, "federate_merge_live", lambda provs, use_cli=True, **kw: real(provs, use_cli=False, **kw)
    )
    return claude


def _run(coro):
    return asyncio.run(coro)


def _render(scope: str, available=lambda _aid: True, width: int = 80) -> Text:
    return ContextFooter().render_for(scope, available, width)


def _entry_style(text: Text, entry: str):
    start = text.plain.index(entry)
    styles = [
        span.style
        for span in text.spans
        if span.start <= start < span.end or start <= span.start < start + len(entry)
    ]
    return styles


def _is_dim(text: Text, entry: str) -> bool:
    styles = _entry_style(text, entry)
    return bool(styles) and all(
        "dim" in str(style) or getattr(style, "dim", False) for style in styles
    )


def _footer_text(app: SessionManagerApp) -> Text:
    footer = app.query_one(ContextFooter)
    rendered = footer.render()
    assert isinstance(rendered, Text)
    return rendered


async def _focus(app: SessionManagerApp, pilot, selector: str) -> None:
    await pilot.pause()
    await pilot.pause()
    app.query_one(selector).focus()
    await pilot.pause()


# ---- footer rendering --------------------------------------------------------


@pytest.mark.parametrize("scope", MAIN_SCOPES + ("filter",))
def test_footer_fits_80_columns_for_every_main_scope(scope):
    text = _render(scope)
    assert text.cell_len <= 80, (scope, text.plain)
    if scope != "filter":
        # Help and quit always survive the cut at 80 columns.
        assert "? Help" in text.plain
        assert "q Quit" in text.plain


def test_footer_orders_by_priority_and_truncates_whole_entries():
    wide = _render("sessions", width=200).plain
    entries = wide.split(" · ")
    shown = [
        a for a in keys.REGISTRY
        if a.footer_priority_in("sessions") is not None
        and ("sessions" in a.scopes or "main" in a.scopes)
    ]
    shown.sort(key=lambda a: a.footer_priority)
    expected = [f"{keys.key_display(a.keys[0])} {a.label}" for a in shown]
    assert entries == expected
    narrow = _render("sessions", width=30).plain
    assert len(narrow) <= 30
    assert narrow and wide.startswith(narrow)
    # Only whole entries: the cut never ends in the middle of a label.
    assert narrow.split(" · ") == entries[: len(narrow.split(" · "))]


def test_footer_combines_pane_and_global_actions_without_duplicates():
    plain = _render("details", width=400).plain
    entries = plain.split(" · ")
    assert len(entries) == len(set(entries))
    assert "Esc Back" in entries
    assert "o Open" in entries
    assert "? Help" in entries


def test_footer_dims_unavailable_instead_of_hiding():
    text = _render("projects", available=lambda aid: aid != "resume_session")
    assert "o Open" in text.plain
    assert _is_dim(text, "o Open")
    assert not _is_dim(text, "d Delete")


@pytest.mark.parametrize("size", SIZES)
def test_footer_dims_open_in_the_projects_pane(tmp_path, monkeypatch, size):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            assert not app.query(Footer), "Textual's footer is replaced"
            await _focus(app, pilot, "#projects")
            text = _footer_text(app)
            assert "o Open" in text.plain
            assert _is_dim(text, "o Open")
            assert text.cell_len <= app.query_one(ContextFooter).size.width
            await _focus(app, pilot, "#sessions")
            text = _footer_text(app)
            assert "o Open" in text.plain
            assert not _is_dim(text, "o Open")
            assert _is_dim(text, "s Stop")  # nothing is running

    _run(main())


def test_filter_mode_footer():
    assert _render("filter").plain == "Enter done · Esc clear"


@pytest.mark.parametrize("size", SIZES)
def test_filter_mode_footer_in_the_app(tmp_path, monkeypatch, size):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await _focus(app, pilot, "#sessions")
            await pilot.press("slash")
            await pilot.pause()
            assert _footer_text(app).plain == "Enter done · Esc clear"
            await pilot.press("escape")
            await pilot.pause()
            assert "o Open" in _footer_text(app).plain

    _run(main())


# ---- availability ------------------------------------------------------------


@pytest.mark.parametrize("size", SIZES)
def test_unavailable_key_shows_reason(tmp_path, monkeypatch, size):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await _focus(app, pilot, "#projects")
            assert app.check_action("resume_session", ()) is True
            assert app.action_available("resume_session") == (
                False,
                "Open works in the Sessions pane",
            )
            await pilot.press("o")
            await pilot.pause()
            assert app.is_running and app.return_code is None
            assert "Open works in the Sessions pane" in str(
                app.query_one("#toast").render()
            )
            await _focus(app, pilot, "#sessions")
            await pilot.press("s")
            await pilot.pause()
            assert type(app.screen).__name__ != "ConfirmDeleteScreen"
            assert "Nothing to stop" in str(app.query_one("#toast").render())

    _run(main())


def test_availability_reasons(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await _focus(app, pilot, "#sessions")
            assert app.action_available("resume_session") == (True, "")
            assert app.action_available("clear_select") == (False, "Nothing marked")
            app.selected_session = None
            assert app.action_available("resume_session") == (
                False,
                "Select a session first",
            )
            assert app.action_available("move_items") == (
                False,
                "Nothing marked to move",
            )
            await _focus(app, pilot, "#detail")
            ok, reason = app.action_available("toggle_select")
            assert not ok and reason
            await _focus(app, pilot, "#projects")
            ok, reason = app.action_available("cycle_session_sort")
            assert not ok and "Sessions pane" in reason
            ok, reason = app.action_available("stop_sessions")
            assert not ok and "Sessions pane" in reason

    _run(main())


def test_every_registry_action_is_guarded():
    # Main-screen actions; dialog keys belong to their dialog screens.
    bound = {binding.action for binding in SessionManagerApp.BINDINGS}
    for action in keys.REGISTRY:
        if action.id not in bound:
            continue
        method = getattr(SessionManagerApp, f"action_{action.id}")
        source = inspect.getsource(method)
        assert f'self._guard("{action.id}"' in source, action.id


def test_modal_and_input_rules_still_hold(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await _focus(app, pilot, "#sessions-filter")
            assert app.check_action("delete_items", ()) is False
            assert app.check_action("back", ()) is True
            await _focus(app, pilot, "#sessions")
            await pilot.press("d")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmDeleteScreen)
            assert app.check_action("delete_items", ()) is False
            await pilot.press("escape")
            await pilot.pause()

    _run(main())


@pytest.mark.parametrize("size", SIZES)
def test_available_actions_still_work(tmp_path, monkeypatch, size):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await _focus(app, pilot, "#sessions")
            sessions = app.query_one("#sessions", DataTable)
            assert sessions.row_count >= 2
            # Delete: the confirm dialog opens; cancel leaves the files alone.
            await pilot.press("d")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmDeleteScreen)
            await pilot.press("escape")
            await pilot.pause()
            # Move: the destination picker opens.
            await pilot.press("m")
            await pilot.pause()
            assert isinstance(app.screen, MoveTargetScreen)
            await pilot.press("escape")
            await pilot.pause()
            # Mark + clear.
            await pilot.press("space")
            await pilot.pause()
            assert len(app.selected_session_ids) == 1
            await pilot.press("c")
            await pilot.pause()
            assert not app.selected_session_ids
            # Project delete from the projects pane.
            await _focus(app, pilot, "#projects")
            await pilot.press("d")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmDeleteScreen)
            await pilot.press("escape")
            await pilot.pause()

    _run(main())


def test_stop_still_opens_its_confirm_when_a_session_is_running(tmp_path, monkeypatch):
    from test_tui_pilot import _setup_opencode

    _setup_opencode(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await _focus(app, pilot, "#sessions")
            assert not _is_dim(_footer_text(app), "s Stop")
            await pilot.press("s")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmDeleteScreen)
            # Cancel: never stop anything in a test.
            await pilot.press("escape")
            await pilot.pause()

    _run(main())


@pytest.mark.parametrize("size", SIZES)
def test_esc_with_nothing_to_back_out_of_is_silent(tmp_path, monkeypatch, size):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            for pane in ("#projects", "#sessions"):
                await _focus(app, pilot, pane)
                app._hide_toast()
                await pilot.press("escape")
                await pilot.pause()
                assert str(app.query_one("#toast").render()).strip() == "", pane
                assert app.focused is app.query_one(pane)
            # Esc still backs out of the filter and the details pane.
            await _focus(app, pilot, "#sessions-filter")
            await pilot.press("escape")
            await pilot.pause()
            assert app.focused is app.query_one("#sessions")
            if app.query_one("#detail").display:
                await _focus(app, pilot, "#detail")
                await pilot.press("escape")
                await pilot.pause()
                assert app.focused is app.query_one("#sessions")

    _run(main())


# ---- theme colours ------------------------------------------------------------


def _footer_background(theme: str, size=(120, 40)):
    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            app.theme = theme
            await pilot.pause()
            footer = app.query_one(ContextFooter)
            text = footer.render()
            key_style = footer.get_component_rich_style("context-footer--key")
            return footer.styles.background, key_style.color, text

    return _run(main())


def test_footer_follows_the_theme(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    dark_bg, dark_key, _ = _footer_background("textual-dark")
    light_bg, light_key, text = _footer_background("textual-light")
    assert dark_bg != light_bg
    # A light theme gets a light footer, a dark one a dark footer.
    assert light_bg.brightness > 0.5 > dark_bg.brightness
    assert dark_key != light_key
    assert text.plain


def test_footer_text_has_no_fixed_colours():
    text = _render("sessions")
    assert not any("#" in str(span.style) for span in text.spans)


def test_no_unused_theme_action():
    assert not hasattr(SessionManagerApp, "action_prev_theme")
    assert hasattr(SessionManagerApp, "action_next_theme")
