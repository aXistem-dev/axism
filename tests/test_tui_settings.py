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
    # Status and detection never depend on the host PATH.
    for mod in ("claude_code", "cursor", "hermes", "opencode"):
        monkeypatch.setattr(f"axism.providers.{mod}.binary_on_path", lambda *a: False, raising=False)
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
            assert "no sessions · command not on PATH" in rows["hermes"]
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
            assert "fix or remove it, then restart aXism" in message
            await pilot.press("enter")
            await pilot.pause()
            assert app.screen is screen
            message = _text(screen, "#settings-message")
            assert "settings.json could not be read" in message
            assert "fix or remove it, then restart aXism" in message
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
            assert "fix or remove it, then restart aXism" in _text(
                screen, "#settings-message"
            )

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


# ---- agent selection polish ------------------------------------------------------


class _FakeProvider:
    """Stand-in agent: a real folder, a configurable detect() and discover()."""

    def __init__(self, root: Path, *, detect=True, discover=None, is_dir_error=False):
        self._root = root
        self._detect = detect
        self._discover = discover or (lambda root: [])
        self._is_dir_error = is_dir_error

    def config_root(self) -> Path:
        return self._root

    def detect(self):
        if isinstance(self._detect, Exception):
            raise self._detect
        return self._detect

    def discover(self, root):
        return self._discover(root)


@pytest.mark.parametrize("size", SIZES)
def test_screen_opens_before_discovery_returns(tmp_path, monkeypatch, size):
    import threading

    from axism.tui import settings_screen as ss

    _setup(tmp_path, monkeypatch)
    release = threading.Event()
    started = threading.Event()
    folder = tmp_path / "fake-root"
    folder.mkdir()

    def slow(root):
        started.set()
        release.wait(10)
        return []

    monkeypatch.setattr(
        ss, "get_provider", lambda pid, config_dir=None: _FakeProvider(folder, discover=slow)
    )

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            screen = await _open(app, pilot)
            try:
                assert isinstance(app.screen, SettingsScreen)
                assert all("checking…" in row for row in _rows(screen))
                # The screen is usable while discovery is blocked.
                await pilot.press("j")
                await pilot.pause()
                assert started.wait(5)
            finally:
                release.set()
            for _ in range(50):
                await pilot.pause()
                if not any("checking…" in row for row in _rows(screen)):
                    break
            rows = _rows(screen)
            assert not any("checking…" in row for row in rows), rows
            assert all("found · 0 sessions" in row for row in rows), rows
            await pilot.press("escape")

    _run(main())


def test_status_wording_for_each_case(tmp_path):
    from axism.tui import settings_screen as ss

    folder = tmp_path / "there"
    folder.mkdir()
    screen = SettingsScreen(Settings_for(), session_counts={})

    def status(provider):
        orig = ss.get_provider
        ss.get_provider = lambda pid, config_dir=None: provider
        try:
            return screen._status_for("cursor")
        finally:
            ss.get_provider = orig

    assert status(_FakeProvider(folder, detect=False)) == "no sessions · command not on PATH"
    assert status(_FakeProvider(folder, detect=OSError("denied"))) == "folder unreadable"

    def boom(root):
        raise OSError("denied")

    assert status(_FakeProvider(folder, discover=boom)) == "folder unreadable"
    assert status(_FakeProvider(tmp_path / "nope")) == "folder missing"
    assert status(_FakeProvider(folder)) == "found · 0 sessions"


def Settings_for():
    from axism.settings import Settings

    return Settings()


def test_unreadable_folder_when_the_root_cannot_be_stat(tmp_path, monkeypatch):
    from axism.tui import settings_screen as ss

    class Denied(_FakeProvider):
        def config_root(self):
            return _Root()

    class _Root:
        def is_dir(self):
            raise PermissionError("denied")

    monkeypatch.setattr(ss, "get_provider", lambda pid, config_dir=None: Denied(tmp_path))
    assert SettingsScreen(Settings_for())._status_for("cursor") == "folder unreadable"


def test_enabling_an_agent_keeps_the_stored_default(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    data = _file(tmp_path)
    data["active_provider"] = "cursor"  # switched off: Claude Code is the default
    _settings_file(tmp_path).write_text(json.dumps(data), encoding="utf-8")

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await _open(app, pilot)
            assert _text(screen, "#settings-default") == "CLI default: Claude Code"
            await _highlight(screen, pilot, "cursor")
            await pilot.press("space")
            await pilot.pause()
            assert _text(screen, "#settings-default") == "CLI default: Claude Code"
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            if isinstance(app.screen, ConfirmDeleteScreen):
                await pilot.press("y")
                await pilot.pause()

    _run(main())
    saved = _file(tmp_path)
    assert saved["providers"]["cursor"]["enabled"] is True
    assert saved["active_provider"] == "claude_code"


@pytest.mark.parametrize("size", SIZES)
def test_missing_folder_confirm_is_warning_toned(tmp_path, monkeypatch, size):
    _setup(tmp_path, monkeypatch)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=size) as pilot:
            screen = await _open(app, pilot)
            await _highlight(screen, pilot, "hermes")
            await pilot.press("tab")
            screen.query_one("#settings-path", Input).value = str(tmp_path / "nowhere")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            dialog = app.screen
            assert isinstance(dialog, ConfirmDeleteScreen)
            box = dialog.query_one("#confirm-dialog")
            assert box.has_class("-warning") and not box.has_class("-project-danger")
            footer = dialog.query_one(ContextFooter)
            plain = footer.render_for(
                footer.scope,
                footer._available,
                footer.content_region.width or 80,
                footer._hidden,
                footer._labels,
            ).plain
            assert plain.startswith("y Save anyway · n Cancel"), plain
            # Enter and other keys do not confirm.
            await pilot.press("enter", "x")
            await pilot.pause()
            assert app.screen is dialog
            await pilot.press("Y")
            await pilot.pause()
            await pilot.pause()
            assert not isinstance(app.screen, ConfirmDeleteScreen)

    _run(main())


def test_delete_confirm_is_not_warning_toned():
    dialog = ConfirmDeleteScreen("Delete?", title="Delete")
    assert dialog.warning is False


@pytest.mark.parametrize("size", SIZES)
def test_run_overrides_are_noted(tmp_path, monkeypatch, size):
    _setup(tmp_path, monkeypatch)
    other = tmp_path / "other-her"
    other.mkdir()

    async def main(**kw):
        app = SessionManagerApp(**kw)
        async with app.run_test(size=size) as pilot:
            screen = await _open(app, pilot)
            note = _text(screen, "#settings-run-note")
            await pilot.press("escape")
            return note

    assert _run(main()) == ""
    assert _run(main(run_provider="hermes")) == "This run: only Hermes"
    assert (
        _run(main(run_provider="hermes", run_config_dir=str(other)))
        == f"This run: only Hermes · Hermes folder: {other} (this run, not saved)"
    )
    assert (
        _run(main(run_config_dir=str(other)))
        == f"Claude Code folder: {other} (this run, not saved)"
    )


def test_typed_folders_are_stored_absolute(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "rel").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await _open(app, pilot)
            await _highlight(screen, pilot, "hermes")
            await pilot.press("tab")
            screen.query_one("#settings-path", Input).value = "rel/../rel"
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()

    _run(main())
    assert _file(tmp_path)["providers"]["hermes"]["config_dir"] == str(
        (tmp_path / "rel").resolve()
    )


def test_untouched_folders_keep_their_recorded_text(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "her")
    data = _file(tmp_path)
    data["providers"]["hermes"]["config_dir"] = str(link)
    _settings_file(tmp_path).write_text(json.dumps(data), encoding="utf-8")

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await _open(app, pilot)
            await _highlight(screen, pilot, "hermes")
            await _highlight(screen, pilot, "opencode")
            await _highlight(screen, pilot, "claude_code")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()

    _run(main())
    assert _file(tmp_path)["providers"]["hermes"]["config_dir"] == str(link)


# ---- status probing of real providers ------------------------------------------


def _real_status(tmp_path, pid, folder):
    from axism.settings import ProviderPrefs, Settings

    settings = Settings(providers={pid: ProviderPrefs(config_dir=str(folder))})
    return SettingsScreen(settings, session_counts={})._status_for(pid)


@pytest.mark.skipif(hasattr(__import__("os"), "geteuid") and __import__("os").geteuid() == 0,
                    reason="permissions do not apply to root")
@pytest.mark.parametrize("pid", ["claude_code", "hermes", "opencode", "cursor"])
def test_unreadable_real_folder_is_reported(tmp_path, pid):
    folder = tmp_path / "locked"
    folder.mkdir()
    folder.chmod(0)
    try:
        assert _real_status(tmp_path, pid, folder) == "folder unreadable"
    finally:
        folder.chmod(0o700)


@pytest.mark.skipif(hasattr(__import__("os"), "geteuid") and __import__("os").geteuid() == 0,
                    reason="permissions do not apply to root")
def test_folder_behind_an_unreachable_parent_is_unreadable(tmp_path):
    parent = tmp_path / "gate"
    (parent / "inner").mkdir(parents=True)
    parent.chmod(0)
    try:
        assert _real_status(tmp_path, "hermes", parent / "inner") == "folder unreadable"
    finally:
        parent.chmod(0o700)
    assert _real_status(tmp_path, "hermes", tmp_path / "a" / "b") == "folder missing"


def test_a_failing_agent_does_not_stall_later_rows(tmp_path, monkeypatch):
    from axism.tui import settings_screen as ss

    _setup(tmp_path, monkeypatch)
    folder = tmp_path / "ok"
    folder.mkdir()

    def fake(pid, config_dir=None):
        if pid == "cursor":
            return _FakeProvider(folder, detect=RuntimeError("boom"))
        return _FakeProvider(folder)

    monkeypatch.setattr(ss, "get_provider", fake)

    async def main():
        app = SessionManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await _open(app, pilot)
            for _ in range(50):
                await pilot.pause()
                if not any("checking…" in r for r in _rows(screen)):
                    break
            rows = dict(zip(IDS, _rows(screen), strict=True))
            assert "status unknown" in rows["cursor"]
            assert "found · 0 sessions" in rows["hermes"]
            assert "found · 0 sessions" in rows["opencode"]
            await pilot.press("escape")

    _run(main())


# ---- save check on folders that cannot be read ------------------------------------

_SKIP_ROOT = pytest.mark.skipif(
    hasattr(__import__("os"), "geteuid") and __import__("os").geteuid() == 0,
    reason="permissions do not apply to root",
)


@_SKIP_ROOT
def test_save_with_a_folder_behind_an_unreadable_parent_asks_instead_of_crashing(
    tmp_path, monkeypatch
):
    _setup(tmp_path, monkeypatch)
    gate = tmp_path / "gate"
    gate.mkdir()
    typed = gate / "sub" / "hermes"
    before = _settings_file(tmp_path).read_bytes()
    gate.chmod(0)
    try:

        async def main():
            app = SessionManagerApp()
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await _open(app, pilot)
                await _highlight(screen, pilot, "hermes")
                await pilot.press("tab")
                screen.query_one("#settings-path", Input).value = str(typed)
                await pilot.press("enter")
                await pilot.pause()
                await pilot.pause()
                dialog = app.screen
                assert isinstance(dialog, ConfirmDeleteScreen)
                lines = "\n".join(dialog.warnings)
                assert f"Hermes: {typed} is unreadable" in lines, lines
                assert "does not exist" not in lines
                await pilot.press("n")
                await pilot.pause()
                assert app.screen is screen

        _run(main())
    finally:
        gate.chmod(0o700)
    assert _settings_file(tmp_path).read_bytes() == before


@_SKIP_ROOT
def test_save_check_lists_missing_and_unreadable_folders_separately(tmp_path):
    from axism.settings import ProviderPrefs, Settings

    locked = tmp_path / "locked"
    locked.mkdir()
    readable = tmp_path / "ok"
    readable.mkdir()
    settings = Settings(
        providers={
            "claude_code": ProviderPrefs(enabled=True, config_dir=str(readable)),
            "cursor": ProviderPrefs(enabled=True, config_dir=str(tmp_path / "gone")),
            "hermes": ProviderPrefs(enabled=True, config_dir=str(locked / "inner")),
            "opencode": ProviderPrefs(enabled=False, config_dir=str(locked / "inner")),
        }
    )
    locked.chmod(0)
    try:
        lines = SettingsScreen(settings, session_counts={}).missing_folders(settings)
    finally:
        locked.chmod(0o700)
    assert lines == [
        f"Cursor: {tmp_path / 'gone'} does not exist",
        f"Hermes: {locked / 'inner'} is unreadable",
    ]
