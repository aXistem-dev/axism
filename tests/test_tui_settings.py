"""Settings: explicit CLI default, per-agent status, folder check, fits small terminals."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest
from conftest import write_jsonl
from textual.content import Content
from textual.screen import Screen
from textual.widgets import Input, OptionList, Static

import axism.tui.app as tui
from axism.tui import keys
from axism.tui.app import SessionManagerApp
from axism.tui.dialogs import ConfirmDeleteScreen
from axism.tui.footer import ContextFooter
from axism.tui.help import help_text
from axism.tui.settings_screen import SettingsScreen

SIZES = [(80, 24), (120, 40)]
CWD = "/home/alice/src/demo"
IDS = ["claude_code", "cursor", "hermes", "opencode"]


def _settings_file(tmp_path: Path) -> Path:
    return tmp_path / "axcfg" / "settings.json"


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Claude Code on with 3 sessions; Hermes on with an empty folder; the rest off."""
    claude = tmp_path / "claude"
    slug = CWD.replace("/", "-")
    for i in range(3):
        sid = str(uuid.UUID(int=i + 1))
        write_jsonl(
            claude / "projects" / slug / f"{sid}.jsonl",
            [
                {"type": "custom-title", "customTitle": f"s{i}", "sessionId": sid},
                {
                    "type": "user",
                    "sessionId": sid,
                    "cwd": CWD,
                    "isSidechain": False,
                    "message": {"role": "user", "content": "hello"},
                    "uuid": str(uuid.uuid4()),
                    "timestamp": "2026-01-01T00:00:00.000Z",
                },
            ],
        )
    hermes = tmp_path / "her"
    hermes.mkdir()
    cfg = tmp_path / "axcfg"
    cfg.mkdir()
    _settings_file(tmp_path).write_text(
        json.dumps(
            {
                "active_provider": "claude_code",
                "providers": {
                    "claude_code": {"enabled": True, "config_dir": str(claude)},
                    "cursor": {"enabled": False, "config_dir": str(tmp_path / "cur")},
                    "hermes": {"enabled": True, "config_dir": str(hermes)},
                    "opencode": {"enabled": False, "config_dir": str(tmp_path / "oc")},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    # Agent homes used when no folder is recorded (detection): never the real ones.
    monkeypatch.setenv("CURSOR_DATA_DIR", str(tmp_path / "cursor-home"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    real = tui.federate_merge_live
    monkeypatch.setattr(
        tui, "federate_merge_live", lambda provs, use_cli=True: real(provs, use_cli=False)
    )
    return {"claude": claude, "hermes": hermes}


def _run(coro):
    return asyncio.run(coro)


async def _open(app: SessionManagerApp, pilot) -> SettingsScreen:
    await pilot.pause()
    await pilot.press("comma")
    await pilot.pause()
    await pilot.pause()
    screen = app.screen
    assert isinstance(screen, SettingsScreen)
    return screen


async def _highlight(screen: SettingsScreen, pilot, pid: str) -> None:
    option_list = screen.query_one("#settings-providers", OptionList)
    option_list.highlighted = IDS.index(pid)
    await pilot.pause()


def _rows(screen: SettingsScreen) -> list[str]:
    option_list = screen.query_one("#settings-providers", OptionList)
    rows = []
    for i in range(option_list.option_count):
        prompt = option_list.get_option_at_index(i).prompt
        rows.append(Content.from_markup(prompt).plain if isinstance(prompt, str) else str(prompt))
    return rows


def _text(screen: Screen, selector: str) -> str:
    return str(screen.query_one(selector, Static).render())


def _file(tmp_path: Path) -> dict:
    return json.loads(_settings_file(tmp_path).read_text(encoding="utf-8"))


def _footer_plain(screen: Screen) -> str:
    footer = screen.query_one(ContextFooter)
    return footer.render_for(
        footer.scope, footer._available, footer.content_region.width or 80, footer._hidden
    ).plain


# ---- tests ------------------------------------------------------------------------


def test_ids_visible(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await _open(app, pilot)
            rows = _rows(screen)
            assert len(rows) == 4
            for pid, row in zip(IDS, rows, strict=True):
                assert pid in row, row
            assert "Claude Code" in rows[0] and "OpenCode" in rows[3]
            await pilot.press("escape")

    _run(main())


def test_status_lines(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await _open(app, pilot)
            rows = dict(zip(IDS, _rows(screen), strict=True))
            assert "found · 3 sessions" in rows["claude_code"]
            assert "folder missing" in rows["cursor"]
            assert "command not installed" in rows["hermes"]
            assert "folder missing" in rows["opencode"]
            await pilot.press("escape")

    _run(main())


def test_status_is_computed_once_when_opened(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    calls: list[str] = []
    real = SettingsScreen._status_for

    def counting(self, pid):
        calls.append(pid)
        return real(self, pid)

    monkeypatch.setattr(SettingsScreen, "_status_for", counting)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await _open(app, pilot)
            await pilot.press("j", "j", "space", "k")
            await pilot.pause()
            assert sorted(calls) == sorted(IDS)
            assert screen is app.screen
            await pilot.press("escape")

    _run(main())


def test_enter_saves_without_changing_default(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await _open(app, pilot)
            await _highlight(screen, pilot, "hermes")
            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen, SettingsScreen)
            toast = _text(app.screen, "#toast")
            assert toast.startswith("Saved")
            assert "CLI default" not in toast

    _run(main())
    data = _file(tmp_path)
    assert data["active_provider"] == "claude_code"
    assert data["providers"]["hermes"]["enabled"] is True
    assert data["providers"]["cursor"]["enabled"] is False


def test_star_sets_default_and_line_updates(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await _open(app, pilot)
            assert _text(screen, "#settings-default") == "CLI default: Claude Code"
            assert _rows(screen)[0].startswith("*")
            await _highlight(screen, pilot, "hermes")
            await pilot.press("asterisk")
            await pilot.pause()
            assert _text(screen, "#settings-default") == "CLI default: Hermes"
            rows = _rows(screen)
            assert rows[2].startswith("*") and not rows[0].startswith("*")
            await pilot.press("enter")
            await pilot.pause()
            toast = _text(app.screen, "#toast")
            assert "CLI default: Hermes" in toast
            assert app._axism_settings.active_provider == "hermes"

    _run(main())
    assert _file(tmp_path)["active_provider"] == "hermes"


def test_star_refused_for_disabled_agent(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await _open(app, pilot)
            await _highlight(screen, pilot, "cursor")
            await pilot.press("asterisk")
            await pilot.pause()
            assert _text(screen, "#settings-default") == "CLI default: Claude Code"
            assert "Cursor is switched off" in _text(screen, "#settings-message")
            # Switch it on, then it can become the default.
            await pilot.press("space", "asterisk")
            await pilot.pause()
            assert _text(screen, "#settings-default") == "CLI default: Cursor"
            await pilot.press("escape")
            await pilot.pause()

    _run(main())
    assert _file(tmp_path)["active_provider"] == "claude_code"


def test_space_refuses_last_enabled(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await _open(app, pilot)
            await _highlight(screen, pilot, "hermes")
            await pilot.press("space")
            await pilot.pause()
            assert "☐" in _rows(screen)[2]
            await _highlight(screen, pilot, "claude_code")
            await pilot.press("space")
            await pilot.pause()
            assert "☑" in _rows(screen)[0]
            assert "only agent switched on" in _text(screen, "#settings-message")

    _run(main())


def test_space_refuses_switching_off_the_default(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await _open(app, pilot)
            await _highlight(screen, pilot, "claude_code")
            await pilot.press("space")
            await pilot.pause()
            assert "☑" in _rows(screen)[0]
            assert "Claude Code is the CLI default" in _text(screen, "#settings-message")

    _run(main())


def test_missing_folder_warns_before_save(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    missing = tmp_path / "nowhere"
    before = _settings_file(tmp_path).read_bytes()

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(80, 24)) as pilot:
            screen = await _open(app, pilot)
            await _highlight(screen, pilot, "hermes")
            await pilot.press("tab")
            await pilot.pause()
            path = screen.query_one("#settings-path", Input)
            assert screen.focused is path
            path.value = str(missing)
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            warning = app.screen
            assert isinstance(warning, ConfirmDeleteScreen)
            assert str(missing) in warning.body + "\n".join(warning.warnings)
            assert _settings_file(tmp_path).read_bytes() == before
            await pilot.press("n")
            await pilot.pause()
            assert app.screen is screen
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, ConfirmDeleteScreen)
            await pilot.press("y")
            await pilot.pause()
            await pilot.pause()
            assert not isinstance(app.screen, (SettingsScreen, ConfirmDeleteScreen))

    _run(main())
    assert _file(tmp_path)["providers"]["hermes"]["config_dir"] == str(missing)


def test_settings_uses_app_settings_object(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            # An in-memory value (as detection leaves it) that is not on disk.
            app._axism_settings.provider_prefs("opencode").enabled = True
            screen = await _open(app, pilot)
            assert screen._settings is app._axism_settings
            assert "☑" in _rows(screen)[3]
            await pilot.press("escape")

    _run(main())


def test_unreadable_settings_are_never_overwritten(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _settings_file(tmp_path).write_text("{not json", encoding="utf-8")

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert _settings_file(tmp_path).read_text(encoding="utf-8") == "{not json"
            screen = await _open(app, pilot)
            # Detection switched Claude Code on (its folder has sessions).
            assert "☑" in _rows(screen)[0]
            message = _text(screen, "#settings-message")
            assert "settings.json could not be read (not valid JSON)" in message
            assert "fix or remove it first" in message
            await pilot.press("enter")
            await pilot.pause()
            assert app.screen is screen
            message = _text(screen, "#settings-message")
            assert "settings.json could not be read" in message
            assert "fix or remove it first" in message
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, SettingsScreen)

    _run(main())
    assert _settings_file(tmp_path).read_text(encoding="utf-8") == "{not json"


@pytest.mark.parametrize("size", SIZES)
def test_settings_refuses_to_replace_a_file_with_a_trailing_comma(
    tmp_path, monkeypatch, size
):
    _setup(tmp_path, monkeypatch)
    original = (
        '{"active_provider": "hermes", "providers": '
        '{"hermes": {"config_dir": "/home/alice/h"},}}'
    )
    _settings_file(tmp_path).write_text(original, encoding="utf-8")

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            screen = await _open(app, pilot)
            await _highlight(screen, pilot, "cursor")
            await pilot.press("space")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            assert app.screen is screen
            assert "fix or remove it first" in _text(screen, "#settings-message")

    _run(main())
    assert _settings_file(tmp_path).read_text(encoding="utf-8") == original


@pytest.mark.parametrize("size", SIZES)
def test_settings_fits_and_shows_its_keys(tmp_path, monkeypatch, size):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            screen = await _open(app, pilot)
            dialog = screen.query_one(".dialog")
            assert screen.region.contains_region(dialog.region)
            foot = screen.query_one(".dialog-foot")
            assert dialog.region.contains_region(foot.region)
            footer = screen.query_one(ContextFooter)
            assert foot.region.contains_region(footer.region)
            assert footer.region.height == 1
            plain = _footer_plain(screen)
            for piece in ("Space On/off", "* Default", "Enter Save", "Tab Folder", "Esc Cancel"):
                assert piece in plain, plain
            default_line = screen.query_one("#settings-default")
            assert dialog.region.contains_region(default_line.region)
            body = screen.query_one("#settings-providers", OptionList)
            assert body.region.height >= 4
            assert body.region.bottom <= foot.region.y
            path = screen.query_one("#settings-path", Input)
            assert foot.region.contains_region(path.region)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, SettingsScreen)

    _run(main())


def test_typing_in_the_folder_field_never_triggers_keys(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(80, 24)) as pilot:
            screen = await _open(app, pilot)
            await pilot.press("tab")
            await pilot.pause()
            path = screen.query_one("#settings-path", Input)
            path.value = ""
            await pilot.press("asterisk", "space", "j", "k", "q", "G", "g")
            await pilot.pause()
            assert app.screen is screen
            assert path.value == "* jkqGg"
            assert _text(screen, "#settings-default") == "CLI default: Claude Code"
            await pilot.press("escape")

    _run(main())


def test_settings_keys_come_from_the_registry_and_help_lists_them():
    expected = {(b.key, b.action) for b in keys.bindings_for("settings")}
    actual = {(b.key, b.action) for b in SettingsScreen.BINDINGS}
    assert actual == expected
    for _key, action in actual:
        assert callable(getattr(SettingsScreen, f"action_{action}", None)), action
    bound = {key for key, _action in actual}
    assert {"space", "asterisk", "enter", "escape", "tab"} <= bound
    assert keys.find("save").label == "Save"
    text = help_text()
    assert "Make the highlighted agent the CLI default" in text
    assert "Settings: go to the folder field" in text


def test_run_config_dir_stays_on_its_agent_after_the_default_changes(tmp_path, monkeypatch):
    roots = _setup(tmp_path, monkeypatch)
    override = tmp_path / "claude-override"
    override.mkdir()

    def folders(app: SessionManagerApp) -> dict[str, Path]:
        return {p.name: p.config_root() for p in app.providers}

    async def main():
        app = SessionManagerApp(run_config_dir=str(override))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert folders(app) == {
                "claude_code": override.resolve(),
                "hermes": roots["hermes"].resolve(),
            }
            screen = await _open(app, pilot)
            await _highlight(screen, pilot, "hermes")
            await pilot.press("asterisk", "enter")
            await pilot.pause()
            await pilot.pause()
            assert app._axism_settings.active_provider == "hermes"
            assert folders(app) == {
                "claude_code": override.resolve(),
                "hermes": roots["hermes"].resolve(),
            }

    _run(main())
    data = _file(tmp_path)
    assert data["active_provider"] == "hermes"
    assert data["providers"]["claude_code"]["config_dir"] == str(roots["claude"])
    assert data["providers"]["hermes"]["config_dir"] == str(roots["hermes"])
    assert str(override) not in _settings_file(tmp_path).read_text(encoding="utf-8")
