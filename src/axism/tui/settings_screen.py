"""Settings: which agents are on, which one is the CLI default, and their folders.

Space switches the highlighted agent on or off, ``*`` makes it the CLI default
(the agent commands use without ``--provider``), Tab goes to its folder field,
Enter saves and Esc cancels. Each row shows the agent's id and a status
worked out in the background after the screen opens (``checking…`` until
then): ``found · N sessions``, ``folder missing``, ``folder unreadable`` or
``no sessions · command not on PATH``. A settings.json that could not be read
is never replaced: the screen says why and Enter saves nothing.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from axism.providers import (
    PROVIDER_LABELS,
    UnsupportedOperation,
    default_provider_id,
    default_refusal,
    disable_refusal,
    get_provider,
    provider_config_hint,
)
from axism.settings import DEFAULT_PROVIDER, ProviderPrefs, Settings
from axism.tui.dialogs import ConfirmDeleteScreen, DialogScreen
from axism.tui.keys import bindings_for
from axism.tui.scrolling import OptionListKeys

MARK_ON = "☑"
MARK_OFF = "☐"

# While the folder field has focus only these act (Inputs win).
SETTINGS_INPUT_ACTIONS = frozenset({"cancel", "save", "focus_folder", "focus_list"})

FIX_HINT = "fix or remove it, then restart aXism"
UNREADABLE_REFUSAL = f"settings.json could not be read — {FIX_HINT}"

CHECKING = "checking…"
STATUS_UNKNOWN = "status unknown"
FOLDER_MISSING = "folder missing"
FOLDER_UNREADABLE = "folder unreadable"
COMMAND_MISSING = "no sessions · command not on PATH"


def folder_state(root: Path) -> str:
    """``dir`` (readable folder), ``missing`` or ``unreadable``.

    Probes the folder itself, and for an absent one the nearest existing
    parent, so a folder behind an unreachable parent is not called missing.
    """
    try:
        if root.is_dir():
            with os.scandir(root):
                pass
            return "dir"
    except OSError:
        return "unreadable"
    parent = root
    while True:
        parent = parent.parent
        try:
            with os.scandir(parent):
                pass
            if not os.access(parent, os.X_OK):
                return "unreadable"
            return "missing"
        except (FileNotFoundError, NotADirectoryError):
            if parent == parent.parent:
                return "missing"
        except OSError:
            return "unreadable"


def found_status(count: int | None) -> str:
    """``found · N sessions`` (``found`` alone when the count is unknown)."""
    if count is None:
        return "found"
    noun = "session" if count == 1 else "sessions"
    return f"found · {count} {noun}"


def _label(pid: str) -> str:
    return PROVIDER_LABELS.get(pid, pid)


class SettingsScreen(OptionListKeys, DialogScreen[Settings | None]):
    """Switch agents on/off, pick the CLI default, set each agent's folder.

    Opens with the app's own settings object (detected values included) and
    returns a new :class:`Settings` on save, ``None`` on cancel.
    """

    SCOPE = "settings"
    BINDINGS = bindings_for(SCOPE)

    DEFAULT_CSS = """
    SettingsScreen #settings-providers {
        margin-top: 1;
        border: solid $primary;
    }
    SettingsScreen #settings-default {
        text-style: bold;
    }
    SettingsScreen #settings-path-label {
        margin-top: 1;
        color: $text-muted;
    }
    SettingsScreen #settings-run-note {
        color: $text-muted;
    }
    SettingsScreen #settings-run-note.-empty {
        display: none;
    }
    SettingsScreen #settings-message {
        color: $warning;
    }
    """

    def __init__(
        self,
        settings: Settings,
        *,
        session_counts: Mapping[tuple[str, str], int] | None = None,
        run_provider: str | None = None,
        run_config_dir: str | None = None,
        run_config_agent: str | None = None,
    ) -> None:
        super().__init__()
        self._run_provider = run_provider
        self._run_config_dir = run_config_dir
        self._run_config_agent = run_config_agent
        self._settings = settings
        self._provider_ids = list(PROVIDER_LABELS.keys())
        self._enabled = {
            pid: self._prefs(pid).enabled for pid in self._provider_ids
        }
        self._config_dirs = {
            pid: self._prefs(pid).config_dir for pid in self._provider_ids
        }
        # The default as it stands now; enabling an agent never moves it.
        self._default = default_provider_id(settings)
        self._path_for: str | None = None
        # (agent id, folder) -> sessions the app already found there.
        self._counts = dict(session_counts or {})
        self._status = {pid: CHECKING for pid in self._provider_ids}
        self._leaving = threading.Event()

    # ---- state -------------------------------------------------------------

    def _prefs(self, pid: str) -> ProviderPrefs:
        """Recorded prefs for ``pid`` without adding an entry to the settings."""
        return self._settings.providers.get(pid) or ProviderPrefs()

    def _status_for(self, pid: str) -> str:
        """Found with a session count, or why not (runs off the UI thread)."""
        try:
            provider = get_provider(pid, config_dir=self._prefs(pid).config_dir)
            root = provider.config_root()
        except (KeyError, UnsupportedOperation):
            return FOLDER_MISSING
        except OSError:
            return FOLDER_UNREADABLE
        try:
            state = folder_state(root)
            if state != "dir":
                return FOLDER_MISSING if state == "missing" else FOLDER_UNREADABLE
            if not provider.detect():
                # The folder holds no sessions and the command is not on PATH.
                return COMMAND_MISSING
        except OSError:
            return FOLDER_UNREADABLE
        count = self._counts.get((pid, str(root)))
        if count is None:
            try:
                count = sum(len(project.sessions) for project in provider.discover(root))
            except OSError:
                return FOLDER_UNREADABLE
            except Exception:
                count = None
        return found_status(count)

    def _check_statuses(self) -> None:
        """Worker thread: work out each agent's status, update its row when done."""
        for pid in self._provider_ids:
            if self._leaving.is_set():
                return
            try:
                status = self._status_for(pid)
            except Exception:
                status = STATUS_UNKNOWN
            if self._leaving.is_set():
                return
            try:
                self.app.call_from_thread(self._set_status, pid, status)
            except Exception:
                return  # the screen or app is gone

    def _set_status(self, pid: str, status: str) -> None:
        self._status[pid] = status
        if self.is_attached:
            self._redraw()

    def effective_default(self) -> str:
        """The CLI default as the choices stand (Claude Code when unknown or off)."""
        if self._default in self._provider_ids and self._enabled.get(self._default):
            return self._default
        return DEFAULT_PROVIDER

    def _row(self, pid: str) -> str:
        star = "*" if pid == self.effective_default() else " "
        mark = MARK_ON if self._enabled.get(pid) else MARK_OFF
        return f"{star} {mark} {_label(pid):<12} {pid:<12} {self._status[pid]}"

    def _run_note(self) -> str:
        """One line about this run's ``--provider`` / ``--config-dir`` (not saved)."""
        parts = []
        if self._run_provider:
            parts.append(f"This run: only {_label(self._run_provider)}")
        if self._run_config_dir:
            agent = self._run_config_agent or self._run_provider or DEFAULT_PROVIDER
            parts.append(
                f"{_label(agent)} folder: {self._run_config_dir} (this run, not saved)"
            )
        return " · ".join(parts)

    def _default_line(self) -> str:
        return f"CLI default: {_label(self.effective_default())}"

    # ---- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        options = [Option(self._row(pid), id=pid) for pid in self._provider_ids]
        yield Vertical(
            Vertical(
                Static(
                    "[b]Settings[/]  ·  agents and their folders",
                    id="settings-title",
                    classes="dialog-title",
                ),
                Static(self._default_line(), id="settings-default", markup=False),
                Static(
                    self._run_note(),
                    id="settings-run-note",
                    markup=False,
                    classes="" if self._run_note() else "-empty",
                ),
                classes="dialog-head",
            ),
            Vertical(
                Static("", id="settings-path-label", markup=False),
                Input(id="settings-path"),
                Static("", id="settings-message", markup=False),
                self.footer(),
                classes="dialog-foot",
            ),
            OptionList(
                *options, id="settings-providers", classes="dialog-body", markup=False
            ),
            id="settings-dialog",
            classes="dialog",
        )

    def option_list(self) -> OptionList:
        return self.query_one("#settings-providers", OptionList)

    def on_mount(self) -> None:
        option_list = self.option_list()
        start = self.effective_default()
        idx = self._provider_ids.index(start) if start in self._provider_ids else 0
        option_list.highlighted = idx
        self._load_path_input(self._provider_ids[idx])
        option_list.focus()
        self.run_worker(self._check_statuses, thread=True, exit_on_error=False)
        reason = self._settings.load_error()
        if reason:
            self._say(
                f"settings.json could not be read ({reason}) — {FIX_HINT}"
            )

    def check_action(
        self, action: str, parameters: tuple[object, ...]
    ) -> bool | None:
        if isinstance(self.focused, Input) and action not in SETTINGS_INPUT_ACTIONS:
            # Inputs win: every other key edits the folder text.
            return False
        return True

    # ---- helpers -----------------------------------------------------------

    def _say(self, message: str) -> None:
        self.query_one("#settings-message", Static).update(message)

    def _highlighted(self) -> str | None:
        option_list = self.option_list()
        index = option_list.highlighted
        if index is None:
            return None
        option = option_list.get_option_at_index(index)
        return str(option.id) if option.id is not None else None

    def _redraw(self) -> None:
        option_list = self.option_list()
        for pid in self._provider_ids:
            option_list.replace_option_prompt(pid, self._row(pid))
        self.query_one("#settings-default", Static).update(self._default_line())

    def _stash_path_input(self) -> None:
        if self._path_for is None:
            return
        typed = self.query_one("#settings-path", Input).value.strip()
        if typed == (self._config_dirs.get(self._path_for) or ""):
            return  # untouched: keep the recorded value as it is
        self._config_dirs[self._path_for] = (
            str(Path(typed).expanduser().resolve()) if typed else None
        )

    def _load_path_input(self, pid: str) -> None:
        inp = self.query_one("#settings-path", Input)
        inp.value = self._config_dirs.get(pid) or ""
        inp.placeholder = provider_config_hint(pid)
        self.query_one("#settings-path-label", Static).update(
            f"Folder for {_label(pid)} (blank = default)"
        )
        self._path_for = pid

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        if event.option_list.id != "settings-providers" or event.option.id is None:
            return
        self._stash_path_input()
        self._load_path_input(str(event.option.id))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        # Enter is the save key; a click only highlights.
        event.stop()

    # ---- actions -----------------------------------------------------------

    def action_toggle_enabled(self) -> None:
        pid = self._highlighted()
        if not pid:
            return
        if self._enabled.get(pid):
            refusal = disable_refusal(self._enabled, self.effective_default(), pid)
            if refusal:
                self._say(refusal)
                return
            self._enabled[pid] = False
            self._say(f"{_label(pid)} switched off")
        else:
            self._enabled[pid] = True
            self._say(f"{_label(pid)} switched on")
        self._redraw()

    def action_set_default(self) -> None:
        pid = self._highlighted()
        if not pid:
            return
        refusal = default_refusal(self._enabled, pid)
        if refusal:
            self._say(refusal)
            return
        if pid == self.effective_default():
            self._say(f"{_label(pid)} is already the CLI default")
            return
        self._default = pid
        self._say(f"{_label(pid)} becomes the CLI default when you save")
        self._redraw()

    def action_focus_folder(self) -> None:
        self.query_one("#settings-path", Input).focus()

    def action_focus_list(self) -> None:
        self.option_list().focus()

    def on_unmount(self) -> None:
        self._leaving.set()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cancel_picker(self) -> None:
        self.dismiss(None)

    def result(self) -> Settings:
        """The settings as chosen on screen (agents this screen does not list are kept)."""
        self._stash_path_input()
        providers = {
            name: ProviderPrefs(enabled=prefs.enabled, config_dir=prefs.config_dir)
            for name, prefs in self._settings.providers.items()
        }
        for pid in self._provider_ids:
            providers[pid] = ProviderPrefs(
                enabled=bool(self._enabled.get(pid)),
                config_dir=self._config_dirs.get(pid),
            )
        # The stored default changes only through ``*``.
        return Settings(active_provider=self._default, providers=providers)

    def missing_folders(self, settings: Settings) -> list[str]:
        """``Label: folder …`` for every switched-on agent whose folder is missing or unreadable."""
        lines = []
        for pid in self._provider_ids:
            prefs = settings.providers[pid]
            if not (prefs.enabled and prefs.config_dir):
                continue
            state = folder_state(Path(prefs.config_dir))
            if state == "missing":
                lines.append(f"{_label(pid)}: {prefs.config_dir} does not exist")
            elif state == "unreadable":
                lines.append(f"{_label(pid)}: {prefs.config_dir} is unreadable")
        return lines

    def action_save(self) -> None:
        if self._settings.load_error():
            # The same rule as the CLI: a file that could not be read is never replaced.
            self._say(UNREADABLE_REFUSAL)
            return
        updated = self.result()
        missing = self.missing_folders(updated)
        if not missing:
            self.dismiss(updated)
            return

        def _after(go_ahead: bool | None) -> None:
            if go_ahead:
                self.dismiss(updated)
            else:
                self._say("Not saved: fix the folder or press Enter again")

        unreadable = any(line.endswith(" is unreadable") for line in missing)
        self.app.push_screen(
            ConfirmDeleteScreen(
                "Save anyway? An agent shows no sessions until its folder exists"
                + (" and can be read." if unreadable else "."),
                title="Folder not usable" if unreadable else "Folder not found",
                warnings=missing,
                confirm_verb="Save anyway",
                warning=True,
            ),
            _after,
        )
