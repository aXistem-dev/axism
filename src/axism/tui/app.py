"""Textual TUI for aXism — Agent Interactive Session Manager."""

from __future__ import annotations

import platform
import sys

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Key
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Input, OptionList, Static
from textual.widgets._header import HeaderClock, HeaderClockSpace
from textual.widgets.option_list import Option

from axism import __commit__, version_string
from axism.delete import (
    build_delete_plan,
    build_project_delete_plan,
    execute_deletes,
    execute_project_delete,
)
from axism.discover import ProjectInfo, SessionMeta
from axism.fragments import collect_fragments
from axism.live import LiveSession, exec_open
from axism.move import MoveResult, move_projects, move_sessions
from axism.providers import PROVIDER_LABELS, provider_from_settings
from axism.rename import rename_session
from axism.settings import (
    DEFAULT_PROVIDER,
    ProviderPrefs,
    Settings,
    load_settings,
    save_settings,
    settings_path,
)

# Avoid Rich markup: "[x]" is parsed as a style tag and renders blank.
MARK_ON = "☑"
MARK_OFF = "☐"

APP_BRAND = "aXism - Agent Interactive Session Manager"

# Dense operational terminal look — one muted teal voice, near-black ground.
# High-contrast chrome so header/footer stay readable on common terminals.
AXISM_THEME = Theme(
    name="axism",
    dark=True,
    primary="#4db8a8",
    secondary="#a8b8c8",
    accent="#6ed4c4",
    foreground="#e8eef5",
    background="#0a0e14",
    surface="#12161e",
    panel="#1a1f2a",
    boost="#222836",
    warning="#d4b84a",
    error="#e07a7a",
    success="#6ed49a",
    text_alpha=0.98,
    variables={
        # Solid muted (not auto+opacity) so labels stay legible on $panel.
        "text-muted": "#9aabbc",
        "footer-foreground": "#e8eef5",
        "footer-key-foreground": "#6ed4c4",
        "footer-description-foreground": "#e8eef5",
        "footer-background": "#1a1f2a",
    },
)

# (id, status label) — cycled with `.` in the sessions pane
SESSION_SORT_MODES: list[tuple[str, str]] = [
    ("updated_desc", "updated ↓"),
    ("updated_asc", "updated ↑"),
    ("title_asc", "title A→Z"),
    ("title_desc", "title Z→A"),
    ("size_desc", "size ↓"),
    ("size_asc", "size ↑"),
]


class AxisHeader(Widget):
    """Top bar: brand + version on the left, optional clock on the right."""

    def __init__(self, *, show_clock: bool = True) -> None:
        super().__init__()
        self._show_clock = show_clock

    def compose(self) -> ComposeResult:
        with Horizontal(id="header-left"):
            yield Static(APP_BRAND, id="header-brand")
            yield Static(f"v{version_string()}", id="header-version")
        yield HeaderClock() if self._show_clock else HeaderClockSpace()


def _fmt_size(n: int) -> str:
    x: float = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if x < 1024:
            return f"{x:.0f}{unit}" if unit == "B" else f"{x:.1f}{unit}"
        x /= 1024
    return f"{x:.1f}TB"


def _badges(sess: SessionMeta, live: LiveSession | None) -> str:
    tags: list[str] = []
    if live:
        if live.kind == "background":
            tags.append("bg")
        elif live.kind == "interactive":
            tags.append("interactive")
        if live.is_live:
            tags.append("live")
        if live.is_remote or sess.has_bridge:
            tags.append("remote")
        if live.state:
            tags.append(str(live.state))
    elif sess.has_bridge:
        tags.append("remote")
    if sess.subagent_count:
        tags.append(f"subagents:{sess.subagent_count}")
    return " ".join(tags) if tags else "-"


def _actions_failed(actions: list[str]) -> bool:
    return any(
        a.startswith(("aborted", "error ")) or " failed" in a.lower()
        for a in actions
    )


def _summarize_session_delete(
    targets: list[SessionMeta], actions: list[str]
) -> tuple[bool, str]:
    failed = _actions_failed(actions)
    if failed:
        err = next(
            (a for a in actions if a.startswith(("aborted", "error "))),
            actions[-1] if actions else "unknown error",
        )
        return False, f"Delete failed: {err}"
    if len(targets) == 1:
        s = targets[0]
        label = s.display_title.replace("\n", " ")[:48] or s.session_id[:8]
        return True, f"Deleted session: {label}"
    return True, f"Deleted {len(targets)} sessions"


def _summarize_project_delete(slugs: list[str], actions: list[str]) -> tuple[bool, str]:
    failed = _actions_failed(actions)
    if failed:
        err = next(
            (a for a in actions if a.startswith(("aborted", "error "))),
            actions[-1] if actions else "unknown error",
        )
        return False, f"Project delete failed: {err}"
    if len(slugs) == 1:
        return True, f"Deleted project: {slugs[0]}"
    return True, f"Deleted {len(slugs)} projects"


def _live_needs_stop(live: LiveSession | None) -> bool:
    if live is None:
        return False
    return live.is_killable


def _format_session_delete_confirm(
    targets: list[SessionMeta],
    plans: list,
    preview: list[str],
) -> tuple[str, list[str]]:
    """Return (body markup, warning lines) for the delete confirm dialog."""
    warnings: list[str] = []
    for s, plan in zip(targets, plans, strict=False):
        live = plan.live
        if not _live_needs_stop(live):
            continue
        pid = f" pid={live.pid}" if live.pid is not None else ""
        warnings.append(
            f"{s.session_id[:8]}…  {live.kind}/{live.state}{pid} — will stop/kill first"
        )

    lines: list[str] = [
        f"[b]{len(targets)} session(s)[/b] will be permanently removed from disk.",
        "Protected items (credentials, settings, skills, memory) are kept.",
        "",
        "[b]Targets[/b]",
    ]
    total = 0
    for s, plan in zip(targets, plans, strict=False):
        total += plan.inventory.total_bytes
        title = s.display_title.replace("\n", " ")[:44]
        nfrag = len(plan.inventory.deletable)
        live = plan.live
        live_bit = ""
        if _live_needs_stop(live):
            live_bit = f"  ·  live {live.kind}/{live.state}"
            if live.pid is not None:
                live_bit += f" pid={live.pid}"
        lines.append(
            f"  {s.session_id[:8]}…  {_fmt_size(plan.inventory.total_bytes)}"
            f"  {nfrag} fragments{live_bit}"
        )
        lines.append(f"    {title}")
    lines.append("")
    lines.append(f"[b]Total[/b]  {_fmt_size(total)} across {len(targets)} session(s)")
    lines.append("")
    lines.append("[b]Will[/b]")
    for a in preview[:40]:
        lines.append(f"  {a}")
    if len(preview) > 40:
        lines.append(f"  … +{len(preview) - 40} more")
    return "\n".join(lines), warnings


def _format_project_delete_confirm(
    projects: list[tuple[str, str, object, list[str]]],
) -> tuple[str, list[str]]:
    """Format confirm body for one or more projects.

    ``projects`` items are ``(slug, cwd, plan, preview_actions)``.
    """
    warnings: list[str] = []
    lines: list[str] = [
        f"[b]{len(projects)} project(s)[/b] will be permanently removed.",
        "Protected items (credentials, settings, skills, memory) are kept.",
        "",
    ]
    for slug, cwd, plan, preview in projects:
        live_n = 0
        for sp in getattr(plan, "session_plans", []) or []:
            if _live_needs_stop(sp.live):
                live_n += 1
                live = sp.live
                pid = f" pid={live.pid}" if live and live.pid is not None else ""
                warnings.append(
                    f"{slug}: {sp.session_id[:8]}…  "
                    f"{live.kind}/{live.state}{pid} — will stop/kill"
                )
        n_sess = len(getattr(plan, "session_plans", []) or [])
        lines.append(f"[b]Project[/b]  {slug}")
        lines.append(f"cwd: {cwd or '-'}")
        lines.append(f"Sessions inside: {n_sess}")
        if live_n:
            warnings.insert(
                0,
                f"{slug}: {live_n} live session(s) will be stopped/killed first",
            )
        lines.append("")
        lines.append("[b]Will[/b]")
        for a in preview[:40]:
            lines.append(f"  {a}")
        if len(preview) > 40:
            lines.append(f"  … +{len(preview) - 40} more")
        lines.append("")
    return "\n".join(lines).rstrip(), warnings


class ConfirmDeleteScreen(ModalScreen[bool]):
    """Confirm a destructive action with a clear summary and y/n prompt."""

    BINDINGS = [
        Binding("y", "confirm", "Yes", show=True, priority=True),
        Binding("n", "cancel", "No", show=True, priority=True),
        Binding("escape", "cancel", "Cancel", show=True, priority=True),
    ]

    def __init__(
        self,
        body: str,
        *,
        title: str,
        warnings: list[str] | None = None,
        confirm_verb: str = "Delete",
        project_danger: bool = False,
    ) -> None:
        super().__init__()
        self.body = body
        self._title = title
        self.warnings = warnings or []
        self.confirm_verb = confirm_verb
        self.project_danger = project_danger

    def compose(self) -> ComposeResult:
        warn = (
            "\n".join(f"!  {w}" for w in self.warnings)
            if self.warnings
            else ""
        )
        title = self._title
        if self.project_danger:
            title = f"[b $error]⚠ {self._title}[/]"
            if not self.warnings:
                warn = (
                    "!  This removes the whole Claude project dir and linked "
                    "session fragments (memory unless kept)."
                )
        dialog_classes = "-project-danger" if self.project_danger else ""
        yield Vertical(
            Static(title, id="confirm-title"),
            Static(
                warn,
                id="confirm-warn",
                classes="-empty" if not warn else "",
            ),
            VerticalScroll(
                Static(self.body, id="confirm-body"),
                id="confirm-scroll",
            ),
            Static(
                (
                    f"[b]y[/b]  {self.confirm_verb} forever     "
                    f"[b]n[/b] / Esc  Cancel"
                    if self.confirm_verb.lower() in {"delete"}
                    else (
                        f"[b]y[/b]  {self.confirm_verb}     "
                        f"[b]n[/b] / Esc  Cancel"
                    )
                ),
                id="confirm-prompt",
            ),
            id="confirm-dialog",
            classes=dialog_classes,
        )

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


# Full legend for the help screen (order = importance for this app).
HELP_LEGEND = """[b $accent]aXism keys[/]

[b $primary]Navigate[/]
  Tab / ← / →       Switch pane focus (projects → sessions → detail)
  ↑ ↓               Move within a list

[b $primary]Select[/] [dim](pane-aware)[/]
  Space             Projects: toggle project mark · Sessions: toggle session
  a / A             Projects: select all projects · Sessions/Detail: all sessions in project
  c / C             Clear ALL marks (projects + sessions, every project)

[b $success]Session[/] [dim](sessions/detail only)[/]
  o / O             Open focused session (exits axism)
                    background/live → claude attach · otherwise → claude --resume
  s / S             Stop background session(s) — same as /stop while attached
  n / N             Rename focused session (custom title)
  /                 Focus session filter
  .                 Cycle session sort (updated / title / size)
  Esc               Clear filter (when filter focused)

[b $primary]Move[/]
  m / M             Projects: move project(s) · Sessions/Detail: move session(s)
                    pick existing project or type a destination path
                    (Tab path field · project merges include memory/)

[b $error]Delete[/]
  d / D             Projects pane: delete project(s)
                    Sessions/Detail: delete session(s)
  y / n / Esc       Confirm or cancel in dialogs

[b $warning]App[/]
  r / R             Refresh session list
  t                 Theme picker
  T                 Cycle theme
  ,                 Settings (provider + config dir)
  v / V             Version details
  ? / h             Help (this legend)
  q / Q             Quit

[dim]Footer shows essentials only (pane · select · open · move · delete · settings · help · quit).
This help lists every key; bindings still hide when unusable in the current pane.[/]
"""


class HelpScreen(ModalScreen[None]):
    """Extended key legend."""

    BINDINGS = [
        Binding("escape", "close", "Close", show=True, priority=True),
        Binding("q", "close", "Close", show=True, priority=True),
        Binding("question_mark", "close", "Close", show=True, priority=True),
        Binding("h", "close", "Close", show=False, priority=True),
    ]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(HELP_LEGEND, id="help-body"),
            Static("[dim]Esc / q / ? / h  close[/]", id="help-hint"),
            id="help-dialog",
        )

    def action_close(self) -> None:
        self.dismiss(None)


class VersionScreen(ModalScreen[None]):
    """Detailed version / runtime info."""

    BINDINGS = [
        Binding("escape", "close", "Close", show=True, priority=True),
        Binding("q", "close", "Close", show=True, priority=True),
        Binding("v", "close", "Close", show=False, priority=True),
        Binding("V", "close", "Close", show=False, priority=True),
    ]

    def __init__(self, body: str) -> None:
        super().__init__()
        self.body = body

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("[b $accent]aXism version[/]", id="version-title"),
            VerticalScroll(Static(self.body, id="version-body"), id="version-scroll"),
            Static("[$text-muted]Esc / q / v  close[/]", id="version-hint"),
            id="version-dialog",
        )

    def action_close(self) -> None:
        self.dismiss(None)


class RenameSessionScreen(ModalScreen[str | None]):
    """Prompt for a new session title."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True, priority=True),
        Binding("enter", "apply", "Apply", show=True, priority=True),
    ]

    def __init__(self, current_title: str, session_short: str) -> None:
        super().__init__()
        self.current_title = current_title
        self.session_short = session_short

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(
                f"[b]Rename session[/b]  {self.session_short}…",
                id="rename-title",
            ),
            Static("Enter a new title (same as Claude /rename)", id="rename-hint"),
            Input(
                value=self.current_title,
                placeholder="Session title",
                id="rename-input",
            ),
            Static("Enter apply · Esc cancel", id="rename-footer"),
            id="rename-dialog",
        )

    def on_mount(self) -> None:
        inp = self.query_one("#rename-input", Input)
        inp.focus()
        inp.cursor_position = len(inp.value)

    def action_apply(self) -> None:
        value = self.query_one("#rename-input", Input).value.strip()
        self.dismiss(value if value else None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        value = event.value.strip()
        self.dismiss(value if value else None)


class ThemePickerScreen(ModalScreen[str | None]):
    """Pick a Textual theme from a scrollable list."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True, priority=True),
        Binding("q", "cancel", "Cancel", show=False, priority=True),
        Binding("enter", "apply", "Apply", show=True, priority=True),
    ]

    def __init__(self, theme_names: list[str], current: str) -> None:
        super().__init__()
        self.theme_names = theme_names
        self.current = current

    def compose(self) -> ComposeResult:
        options: list[Option] = []
        for name in self.theme_names:
            label = f"→ {name}" if name == self.current else f"  {name}"
            options.append(Option(label, id=name))
        yield Vertical(
            Static(
                f"[b]Themes[/b]  ({len(self.theme_names)})  ·  Enter apply · Esc cancel",
                id="theme-picker-title",
            ),
            OptionList(*options, id="theme-list"),
            id="theme-picker-dialog",
        )

    def on_mount(self) -> None:
        option_list = self.query_one("#theme-list", OptionList)
        try:
            idx = self.theme_names.index(self.current)
        except ValueError:
            idx = 0
        if self.theme_names:
            option_list.highlighted = idx
        option_list.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_apply(self) -> None:
        option_list = self.query_one("#theme-list", OptionList)
        highlighted = option_list.highlighted
        if highlighted is None:
            self.dismiss(None)
            return
        opt = option_list.get_option_at_index(highlighted)
        self.dismiss(str(opt.id) if opt.id is not None else None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option.id is not None:
            self.dismiss(str(event.option.id))


class SettingsScreen(ModalScreen[Settings | None]):
    """Choose active provider and per-provider config directory."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True, priority=True),
        Binding("q", "cancel", "Cancel", show=False, priority=True),
        Binding("enter", "apply", "Save", show=True, priority=True),
        Binding("tab", "focus_path", "Path", show=False, priority=True),
        Binding("shift+tab", "focus_list", "List", show=False, priority=True),
        Binding("space", "toggle_enabled", "Toggle", show=False),
    ]

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings
        self._provider_ids = list(PROVIDER_LABELS.keys())
        self._enabled = {
            pid: settings.provider_prefs(pid).enabled for pid in self._provider_ids
        }
        self._config_dirs = {
            pid: settings.provider_prefs(pid).config_dir for pid in self._provider_ids
        }
        self._path_for: str | None = None

    def compose(self) -> ComposeResult:
        active = self._settings.active_provider
        if active not in self._provider_ids:
            active = DEFAULT_PROVIDER
        options: list[Option] = []
        for pid in self._provider_ids:
            label = PROVIDER_LABELS.get(pid, pid)
            mark = MARK_ON if self._enabled.get(pid, True) else MARK_OFF
            arrow = "→" if pid == active else " "
            options.append(Option(f"{arrow} {mark} {label}  [{pid}]", id=pid))
        prefs = self._settings.provider_prefs(active)
        path_value = self._config_dirs.get(active) or prefs.config_dir or ""
        yield Vertical(
            Static("[b]Settings[/]  ·  provider + config dir", id="settings-title"),
            Static(
                "Space toggles enabled (future multi-provider). "
                "Only Claude Code is available today.",
                id="settings-hint",
            ),
            OptionList(*options, id="settings-providers"),
            Static(
                "Config dir for highlighted provider (blank = default)",
                id="settings-path-label",
            ),
            Input(
                value=path_value,
                placeholder="~/.claude  or  $CLAUDE_CONFIG_DIR",
                id="settings-path",
            ),
            Static(
                f"[$text-muted]Saved at {settings_path()}[/]\n"
                "↑↓ provider · Space enable · Tab path · Enter save · Esc cancel",
                id="settings-footer",
            ),
            id="settings-dialog",
        )

    def on_mount(self) -> None:
        option_list = self.query_one("#settings-providers", OptionList)
        try:
            idx = self._provider_ids.index(self._settings.active_provider)
        except ValueError:
            idx = 0
        if self._provider_ids:
            option_list.highlighted = idx
            self._path_for = self._provider_ids[idx]
        option_list.focus()

    def _stash_path_input(self) -> None:
        if self._path_for is None:
            return
        typed = self.query_one("#settings-path", Input).value.strip()
        self._config_dirs[self._path_for] = typed or None

    def _load_path_input(self, pid: str) -> None:
        inp = self.query_one("#settings-path", Input)
        inp.value = self._config_dirs.get(pid) or ""
        self._path_for = pid

    def _highlighted_provider(self) -> str | None:
        option_list = self.query_one("#settings-providers", OptionList)
        highlighted = option_list.highlighted
        if highlighted is None:
            return None
        opt = option_list.get_option_at_index(highlighted)
        return str(opt.id) if opt.id is not None else None

    def _reload_provider_options(self) -> None:
        option_list = self.query_one("#settings-providers", OptionList)
        highlighted = option_list.highlighted
        active = self._highlighted_provider() or self._settings.active_provider
        option_list.clear_options()
        for pid in self._provider_ids:
            label = PROVIDER_LABELS.get(pid, pid)
            mark = MARK_ON if self._enabled.get(pid, True) else MARK_OFF
            arrow = "→" if pid == active else " "
            option_list.add_option(Option(f"{arrow} {mark} {label}  [{pid}]", id=pid))
        if highlighted is not None and highlighted < len(self._provider_ids):
            option_list.highlighted = highlighted

    def action_focus_path(self) -> None:
        self.query_one("#settings-path", Input).focus()

    def action_focus_list(self) -> None:
        self.query_one("#settings-providers", OptionList).focus()

    def action_toggle_enabled(self) -> None:
        if self.focused is not None and self.focused.id == "settings-path":
            return
        pid = self._highlighted_provider()
        if not pid:
            return
        # Keep at least one provider enabled.
        currently = self._enabled.get(pid, True)
        if currently and sum(1 for v in self._enabled.values() if v) <= 1:
            return
        self._enabled[pid] = not currently
        self._reload_provider_options()

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        if event.option_list.id != "settings-providers":
            return
        pid = str(event.option.id) if event.option.id is not None else None
        if not pid:
            return
        self._stash_path_input()
        self._load_path_input(pid)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_apply(self) -> None:
        self._stash_path_input()
        pid = self._highlighted_provider() or self._settings.active_provider
        if pid not in self._provider_ids:
            pid = DEFAULT_PROVIDER
        providers: dict[str, ProviderPrefs] = {}
        for name in self._provider_ids:
            providers[name] = ProviderPrefs(
                enabled=self._enabled.get(name, True),
                config_dir=self._config_dirs.get(name),
            )
        if not providers.get(pid, ProviderPrefs()).enabled:
            pid = next(
                (p for p in self._provider_ids if self._enabled.get(p, True)),
                DEFAULT_PROVIDER,
            )
        self.dismiss(Settings(active_provider=pid, providers=providers))


class MoveTargetScreen(ModalScreen[str | None]):
    """Pick an existing project or type a destination path."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True, priority=True),
        Binding("enter", "apply", "Apply", show=True, priority=True),
        Binding("tab", "focus_path", "Path", show=False, priority=True),
        Binding("shift+tab", "focus_list", "List", show=False, priority=True),
    ]

    def __init__(
        self,
        title: str,
        choices: list[tuple[str, str]],
        hint: str = "",
    ) -> None:
        super().__init__()
        self._title = title
        self.choices = choices  # (cwd, label)
        self.hint = hint or (
            "Select an existing project, or type an absolute path below."
        )

    def compose(self) -> ComposeResult:
        options: list[Option] = [
            Option(label, id=cwd) for cwd, label in self.choices
        ]
        if not options:
            options.append(
                Option("(no other projects — type a path)", id="__none__")
            )
        yield Vertical(
            Static(f"[b]{self._title}[/]", id="move-title"),
            Static(self.hint, id="move-hint"),
            OptionList(*options, id="move-list"),
            Input(
                placeholder="/absolute/path/to/project",
                id="move-input",
            ),
            Static(
                "↑↓ list · Tab path · Shift+Tab list · Enter apply · Esc cancel",
                id="move-footer",
            ),
            id="move-dialog",
        )

    def on_mount(self) -> None:
        self.query_one("#move-list", OptionList).focus()

    def action_focus_path(self) -> None:
        self.query_one("#move-input", Input).focus()

    def action_focus_list(self) -> None:
        self.query_one("#move-list", OptionList).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _dest_from_ui(self) -> str | None:
        typed = self.query_one("#move-input", Input).value.strip()
        if typed:
            return typed
        option_list = self.query_one("#move-list", OptionList)
        highlighted = option_list.highlighted
        if highlighted is None:
            return None
        opt = option_list.get_option_at_index(highlighted)
        if opt.id is None or str(opt.id) == "__none__":
            return None
        return str(opt.id)

    def action_apply(self) -> None:
        dest = self._dest_from_ui()
        self.dismiss(dest if dest else None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option.id is not None and str(event.option.id) != "__none__":
            self.dismiss(str(event.option.id))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        value = event.value.strip()
        self.dismiss(value if value else None)


class AgentPickerScreen(ModalScreen[str | None]):
    """Pick a coding-agent CLI for memory-merge handoff, or save brief only."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True, priority=True),
        Binding("enter", "apply", "Apply", show=True, priority=True),
        Binding("q", "cancel", "Cancel", show=False, priority=True),
    ]

    def __init__(self, title: str, choices: list[tuple[str, str]]) -> None:
        super().__init__()
        self._title = title
        self.choices = choices  # (id, label)

    def compose(self) -> ComposeResult:
        options = [Option(label, id=aid) for aid, label in self.choices]
        yield Vertical(
            Static(f"[b]{self._title}[/]", id="agent-picker-title"),
            Static(
                "Finish MEMORY.md merge with an agent, or save the brief only.",
                id="agent-picker-hint",
            ),
            OptionList(*options, id="agent-list"),
            Static("Enter select · Esc cancel", id="agent-picker-footer"),
            id="agent-picker-dialog",
        )

    def on_mount(self) -> None:
        self.query_one("#agent-list", OptionList).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_apply(self) -> None:
        option_list = self.query_one("#agent-list", OptionList)
        highlighted = option_list.highlighted
        if highlighted is None:
            self.dismiss(None)
            return
        opt = option_list.get_option_at_index(highlighted)
        self.dismiss(str(opt.id) if opt.id is not None else None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option.id is not None:
            self.dismiss(str(event.option.id))


class SessionManagerApp(App[None]):
    """Main TUI: projects | sessions | detail."""

    # Prefer explicit keybinds in our footer; no Ctrl+P command palette.
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    Screen {
        layout: vertical;
        background: $background;
    }
    /* height:1 — no borders here; tall borders consume the only row and hide text */
    AxisHeader {
        dock: top;
        width: 100%;
        height: 1;
        background: #1a1f2a;
        color: #e8eef5;
    }
    AxisHeader #header-left {
        dock: left;
        width: auto;
        max-width: 85%;
        height: 1;
        layout: horizontal;
        background: #1a1f2a;
    }
    AxisHeader #header-brand {
        width: auto;
        max-width: 70%;
        padding: 0 0 0 1;
        color: #e8eef5;
        background: #1a1f2a;
        text-style: bold;
        text-wrap: nowrap;
        text-overflow: ellipsis;
        text-opacity: 100%;
    }
    AxisHeader #header-version {
        width: auto;
        padding: 0 1;
        color: #a8b8c8;
        background: #1a1f2a;
        text-wrap: nowrap;
        text-opacity: 100%;
    }
    AxisHeader HeaderClock {
        color: #a8b8c8;
        background: #1a1f2a;
        text-opacity: 100%;
    }
    #body {
        height: 1fr;
        background: $background;
    }
    #projects, #sessions-pane, #detail {
        background: $surface;
        border: solid $panel;
    }
    #projects.-focused, #sessions-pane.-focused, #detail.-focused {
        border: solid $accent;
        background: $boost;
    }
    #projects {
        width: 25%;
    }
    #sessions-pane {
        width: 50%;
        layout: vertical;
    }
    #sessions-meta {
        height: 1;
        padding: 0 1;
        color: $text-muted;
        background: $panel;
    }
    #sessions-pane.-focused #sessions-meta {
        color: $foreground;
        background: $panel;
        text-style: bold;
    }
    #sessions-filter {
        height: 1;
        border: none;
        background: $panel;
        padding: 0 1;
        color: $text-muted;
    }
    #sessions-filter:focus {
        background: $boost;
        color: $foreground;
    }
    #sessions {
        height: 1fr;
        border: none;
        border-top: solid $panel;
        background: $surface;
    }
    #sessions-pane.-focused #sessions {
        border-top: solid $accent 25%;
        background: $boost;
    }
    #detail {
        width: 1fr;
        padding: 0 1;
    }
    DataTable {
        background: transparent;
    }
    DataTable > .datatable--header {
        text-style: bold;
        color: $text-muted;
        background: $panel;
    }
    DataTable > .datatable--cursor {
        background: $accent 28%;
        color: $foreground;
        text-style: bold;
    }
    DataTable > .datatable--hover {
        background: $primary 12%;
    }
    #status {
        height: 1;
        background: $panel;
        padding: 0 1;
        color: $text-muted;
    }
    #toast {
        display: none;
        height: 1;
        padding: 0 1;
        text-style: bold;
    }
    #toast.-success {
        display: block;
        background: $success 40%;
        color: $text;
    }
    #toast.-error {
        display: block;
        background: $error 45%;
        color: $text;
    }
    #confirm-dialog, #help-dialog, #theme-picker-dialog, #rename-dialog, #move-dialog, #agent-picker-dialog, #version-dialog, #settings-dialog {
        width: 80%;
        height: 70%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
        margin: 2 4;
    }
    #settings-dialog {
        height: auto;
        max-height: 80%;
        border: thick $primary;
    }
    #version-dialog {
        height: auto;
        max-height: 70%;
        border: thick $primary;
    }
    #rename-dialog {
        height: auto;
        max-height: 50%;
        border: thick $primary;
    }
    #move-dialog {
        border: thick $primary;
        layout: vertical;
    }
    #confirm-dialog {
        border: thick $error;
        layout: vertical;
    }
    #confirm-dialog.-project-danger {
        border: double $error;
        background: $error 12%;
    }
    #confirm-dialog.-project-danger #confirm-title {
        background: $error 35%;
        color: $text;
        padding: 0 1;
    }
    #confirm-dialog.-project-danger #confirm-warn {
        color: $error;
        border: thick $error;
        background: $error 20%;
        text-style: bold;
    }
    #confirm-dialog.-project-danger #confirm-prompt {
        background: $error 45%;
        color: $text;
    }
    #confirm-title, #theme-picker-title, #rename-title, #move-title, #agent-picker-title, #version-title, #settings-title {
        text-style: bold;
        margin-bottom: 1;
        color: $accent;
    }
    #rename-hint, #rename-footer, #move-hint, #move-footer, #agent-picker-hint, #agent-picker-footer, #version-hint, #settings-hint, #settings-footer, #settings-path-label {
        color: $text-muted;
        margin-bottom: 1;
    }
    #settings-providers {
        height: auto;
        max-height: 12;
        margin-bottom: 1;
    }
    #settings-path {
        margin-bottom: 1;
    }
    #move-list, #agent-list {
        height: 1fr;
        margin-bottom: 1;
    }
    #move-input {
        margin-bottom: 1;
    }
    #rename-input {
        margin: 1 0;
    }
    #confirm-title {
        color: $error;
        text-style: bold;
    }
    #confirm-warn {
        color: $warning;
        margin-bottom: 1;
        padding: 0 1;
        border: solid $warning;
    }
    #confirm-warn.-empty {
        display: none;
        height: 0;
        margin: 0;
        padding: 0;
        border: none;
    }
    #confirm-scroll {
        height: 1fr;
        border: solid $panel;
        padding: 0 1;
    }
    #confirm-body {
        padding: 1 0;
    }
    #confirm-prompt {
        margin-top: 1;
        padding: 1 1;
        background: $error 20%;
        text-style: bold;
    }
    #theme-list {
        height: 1fr;
        border: solid $primary;
    }
    #help-hint {
        margin-top: 1;
        color: $text-muted;
    }
    #help-body {
        padding: 0 1;
    }
    #help-dialog {
        border: thick $primary;
    }
    /* height:1 — no borders; they zero the content row and hide keybinds */
    Footer {
        height: 1;
        background: #1a1f2a;
        color: #e8eef5;
    }
    FooterKey {
        color: #e8eef5;
        background: #1a1f2a;
        text-opacity: 100%;
        .footer-key--key {
            color: #6ed4c4;
            text-style: bold;
            background: #1a1f2a;
            text-opacity: 100%;
        }
        .footer-key--description {
            color: #e8eef5;
            background: #1a1f2a;
            text-opacity: 100%;
        }
    }
    """

    # Footer: essentials only. Full legend via ? / h. Settings (,) for provider/config.
    BINDINGS = [
        Binding("tab", "focus_next", "Pane", show=True),
        Binding("right", "focus_next", "Pane", show=False, priority=True),
        Binding("left", "focus_previous", "Pane", show=False, priority=True),
        Binding("space", "toggle_select", "Select", show=True),
        Binding("a", "select_all", "All", show=False),
        Binding("A", "select_all", "All", show=False),
        Binding("c", "clear_select", "Clear", show=False, priority=True),
        Binding("C", "clear_select", "Clear", show=False, priority=True),
        Binding("o", "resume_session", "Open", show=True),
        Binding("O", "resume_session", "Open", show=False),
        Binding("s", "stop_sessions", "Stop", show=False, priority=True),
        Binding("S", "stop_sessions", "Stop", show=False, priority=True),
        Binding("n", "rename_session", "Rename", show=False, priority=True),
        Binding("N", "rename_session", "Rename", show=False, priority=True),
        Binding("slash", "focus_session_filter", "Filter", show=False),
        Binding("full_stop", "cycle_session_sort", "Sort", show=False),
        Binding("m", "move_items", "Move", show=True, priority=True),
        Binding("M", "move_items", "Move", show=False, priority=True),
        Binding("d", "delete_items", "Delete", show=True, priority=True),
        Binding("D", "delete_items", "Delete", show=False, priority=True),
        Binding("r", "refresh", "Refresh", show=False),
        Binding("R", "refresh", "Refresh", show=False),
        Binding("t", "pick_theme", "Themes", show=False),
        Binding("T", "next_theme", "Theme cycle", show=False),
        Binding("comma", "show_settings", "Settings", show=True, priority=True),
        Binding("v", "show_version", "Version", show=False, priority=True),
        Binding("V", "show_version", "Version", show=False, priority=True),
        Binding("question_mark", "show_help", "Help", show=True),
        Binding("h", "show_help", "Help", show=False, priority=True),
        Binding("H", "show_help", "Help", show=False, priority=True),
        Binding("q", "quit", "Quit", show=True),
        Binding("Q", "quit", "Quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._axism_settings = load_settings()
        self.provider = provider_from_settings(self._axism_settings)
        self.root = self.provider.config_root()
        self.projects: list[ProjectInfo] = []
        self.live: dict[str, LiveSession] = {}
        self.selected_project: ProjectInfo | None = None
        self.selected_session: SessionMeta | None = None
        self.selected_session_ids: set[str] = set()
        self.selected_project_slugs: set[str] = set()
        self._toast_timer: Timer | None = None
        self._theme_names: list[str] = []
        self._session_filter = ""
        self._session_sort = SESSION_SORT_MODES[0][0]

    def compose(self) -> ComposeResult:
        yield AxisHeader(show_clock=True)
        with Horizontal(id="body"):
            yield DataTable(id="projects", cursor_type="row")
            with Vertical(id="sessions-pane"):
                yield Static("", id="sessions-meta")
                yield Input(
                    placeholder="/ filter · . sort",
                    id="sessions-filter",
                )
                yield DataTable(id="sessions", cursor_type="row")
            yield Static("Select a session  ·  ? help", id="detail")
        yield Static("", id="status")
        yield Static("", id="toast")
        yield Footer(compact=True, show_command_palette=False)

    def on_mount(self) -> None:
        self.register_theme(AXISM_THEME)
        if "axism" in self.available_themes:
            self.theme = "axism"
        # Title/subtitle are for OS chrome; AxisHeader paints the visible bar.
        self.title = APP_BRAND
        self.sub_title = f"v{version_string()}"
        self._theme_names = sorted(self.available_themes.keys())
        proj_table = self.query_one("#projects", DataTable)
        proj_table.border_title = "projects"
        proj_table.add_columns("Sel", "Project", "Sessions", "Size")
        sess_pane = self.query_one("#sessions-pane", Vertical)
        sess_pane.border_title = "sessions"
        sess_table = self.query_one("#sessions", DataTable)
        sess_table.add_columns("Sel", "Title", "Updated", "Size", "Tags")
        detail = self.query_one("#detail", Static)
        detail.border_title = "detail"
        self.theme_changed_signal.subscribe(self, self._on_theme_changed)
        self._sync_pane_focus_classes()
        self.action_refresh()

    def _on_theme_changed(self, _theme: object) -> None:
        """Re-render detail so markup CSS variables pick up the new theme."""
        self._show_detail()

    def _sync_pane_focus_classes(self) -> None:
        """Highlight the focused column so the active pane is obvious."""
        pane = self._focus_pane()
        mapping = {
            "projects": "projects",
            "sessions": "sessions-pane",
            "detail": "detail",
        }
        for key, wid in mapping.items():
            node = self.query_one(f"#{wid}")
            node.set_class(pane == key, "-focused")

    def check_action(
        self, action: str, parameters: tuple[object, ...]
    ) -> bool | None:
        """Hide/disable keys that cannot run in the current pane or state."""
        if len(self.screen_stack) > 1:
            return False
        pane = self._focus_pane()
        if action == "toggle_select" and pane == "detail":
            return False
        if action == "toggle_select" and isinstance(self.focused, Input):
            return False
        if action in {"focus_session_filter", "cycle_session_sort"}:
            if pane not in {"sessions", "detail"} and action == "cycle_session_sort":
                return False
            if action == "cycle_session_sort" and not self.selected_project:
                return False
            if (
                action == "focus_session_filter"
                and isinstance(self.focused, Input)
                and self.focused.id != "sessions-filter"
            ):
                return False
        if action == "resume_session":
            if pane == "projects" or not self.selected_session:
                return False
        if action == "rename_session":
            if pane == "projects" or not self.selected_session:
                return False
        if action == "move_items":
            if pane == "projects":
                if not self._target_projects():
                    return False
            elif pane in {"sessions", "detail"}:
                if not self._target_sessions_for_move():
                    return False
            else:
                return False
        if action == "stop_sessions":
            if pane == "projects":
                return False
            candidates = self._target_sessions_for_stop()
            if not self._stoppable_among(candidates):
                return False
        if action == "delete_items":
            if pane == "projects":
                if not self._target_projects():
                    return False
            elif pane in {"sessions", "detail"}:
                if not self._target_sessions_for_delete():
                    return False
            else:
                return False
        return True

    def _focus_pane(self) -> str:
        """Return which main pane owns focus: projects | sessions | detail | other."""
        focused = self.focused
        node = focused
        while node is not None:
            wid = getattr(node, "id", None)
            if wid in {"projects", "sessions", "detail"}:
                return str(wid)
            if wid in {"sessions-pane", "sessions-filter", "sessions-meta"}:
                return "sessions"
            node = getattr(node, "parent", None)
        return "other"

    def _session_sort_label(self) -> str:
        for sid, label in SESSION_SORT_MODES:
            if sid == self._session_sort:
                return label
        return SESSION_SORT_MODES[0][1]

    def _visible_sessions(self) -> list[SessionMeta]:
        """Sessions for the current project, filtered and sorted for the table."""
        if not self.selected_project:
            return []
        sessions = list(self.selected_project.sessions)
        q = self._session_filter.strip().lower()
        if q:
            filtered: list[SessionMeta] = []
            for s in sessions:
                live = self.live.get(s.session_id)
                hay = " ".join(
                    [
                        s.display_title,
                        s.session_id,
                        s.cwd or "",
                        _badges(s, live),
                    ]
                ).lower()
                if q in hay:
                    filtered.append(s)
            sessions = filtered
        mode = self._session_sort
        if mode == "updated_asc":
            sessions.sort(key=lambda s: s.mtime)
        elif mode == "title_asc":
            sessions.sort(key=lambda s: s.display_title.lower())
        elif mode == "title_desc":
            sessions.sort(key=lambda s: s.display_title.lower(), reverse=True)
        elif mode == "size_desc":
            sessions.sort(key=lambda s: s.size_bytes, reverse=True)
        elif mode == "size_asc":
            sessions.sort(key=lambda s: s.size_bytes)
        else:
            sessions.sort(key=lambda s: s.mtime, reverse=True)
        return sessions

    def _update_sessions_meta(self) -> None:
        meta = self.query_one("#sessions-meta", Static)
        if not self.selected_project:
            meta.update("[$text-muted]/ filter · . sort[/]")
            return
        total = len(self.selected_project.sessions)
        shown = len(self._visible_sessions())
        filt = self._session_filter.strip()
        filt_bit = f'filter="{filt}"  ' if filt else ""
        count = f"{shown}/{total}" if filt or shown != total else str(total)
        meta.update(
            f"[$text-muted]{count} · {filt_bit}sort: {self._session_sort_label()}  "
            f"(/ filter · . sort)[/]"
        )

    def _target_projects(self) -> list[ProjectInfo]:
        """Marked projects if any; else focused project on projects pane; else listed."""
        if self.selected_project_slugs:
            return [p for p in self.projects if p.slug in self.selected_project_slugs]
        if self._focus_pane() == "projects":
            return [self.selected_project] if self.selected_project else []
        return [self.selected_project] if self.selected_project else []

    def _sessions_by_ids(self, ids: set[str]) -> list[SessionMeta]:
        out: list[SessionMeta] = []
        for p in self.projects:
            for s in p.sessions:
                if s.session_id in ids:
                    out.append(s)
        return out

    def _target_sessions_for_delete(self) -> list[SessionMeta]:
        """Marked sessions if any; else focused on sessions/detail; empty on projects."""
        if self.selected_session_ids:
            return self._sessions_by_ids(self.selected_session_ids)
        pane = self._focus_pane()
        if pane in {"sessions", "detail"} and self.selected_session:
            return [self.selected_session]
        return []

    def _target_sessions_for_stop(self) -> list[SessionMeta]:
        """Marked sessions if any; else focused on sessions/detail; expand projects."""
        if self.selected_session_ids:
            return self._sessions_by_ids(self.selected_session_ids)
        pane = self._focus_pane()
        if pane in {"sessions", "detail"}:
            return [self.selected_session] if self.selected_session else []
        if pane == "projects":
            out: list[SessionMeta] = []
            for p in self._target_projects():
                out.extend(p.sessions)
            return out
        return []

    def _target_sessions_for_move(self) -> list[SessionMeta]:
        """Marked sessions if any; else focused session on sessions/detail."""
        if self.selected_session_ids:
            return self._sessions_by_ids(self.selected_session_ids)
        pane = self._focus_pane()
        if pane in {"sessions", "detail"} and self.selected_session:
            return [self.selected_session]
        return []

    def _stoppable_among(
        self, sessions: list[SessionMeta]
    ) -> list[tuple[SessionMeta, LiveSession]]:
        """Background sessions that /stop (claude stop) can target."""
        out: list[tuple[SessionMeta, LiveSession]] = []
        for s in sessions:
            live = self.live.get(s.session_id)
            if live is None or live.kind != "background":
                continue
            if live.state in {"working", "blocked", "running"} or live.is_killable:
                out.append((s, live))
        return out

    def action_refresh(self) -> None:
        self.projects = self.provider.discover(self.root)
        self.live = self.provider.merge_live(self.root, use_cli=True)
        known_sessions = {s.session_id for p in self.projects for s in p.sessions}
        self.selected_session_ids &= known_sessions
        known_slugs = {p.slug for p in self.projects}
        self.selected_project_slugs &= known_slugs
        self._reload_projects_table()
        self._refresh_status()

    def _refresh_status(self, extra: str = "") -> None:
        pane = self._focus_pane()
        pane_label = {"projects": "projects", "sessions": "sessions", "detail": "detail"}.get(
            pane, pane
        )
        bits = [
            f"[b $accent]{pane_label}[/]",
            f"mark {len(self.selected_project_slugs)}p/{len(self.selected_session_ids)}s",
            f"[$text-muted]{self.provider.label}[/]",
            f"[$text-muted]{self.root}[/]",
        ]
        if extra:
            bits.append(extra)
        self.query_one("#status", Static).update("  ·  ".join(bits))
        self.refresh_bindings()

    def on_descendant_focus(self, _event: object) -> None:
        self._sync_pane_focus_classes()
        self._refresh_status()
        self._show_detail()

    def _set_status(self, text: str) -> None:
        """Update status with pane/selection prefix, then optional message."""
        self._refresh_status(extra=text)

    def _show_toast(self, message: str, *, success: bool, seconds: float = 4.0) -> None:
        toast = self.query_one("#toast", Static)
        toast.remove_class("-success", "-error")
        toast.add_class("-success" if success else "-error")
        toast.update(message)
        if self._toast_timer is not None:
            self._toast_timer.stop()
        self._toast_timer = self.set_timer(seconds, self._hide_toast)

    def _hide_toast(self) -> None:
        toast = self.query_one("#toast", Static)
        toast.remove_class("-success", "-error")
        toast.update("")
        self._toast_timer = None

    def _show_project_detail(self) -> None:
        """Detail pane content when the projects list is focused."""
        detail = self.query_one("#detail", Static)
        if self.selected_project_slugs and len(self.selected_project_slugs) > 1:
            targets = [p for p in self.projects if p.slug in self.selected_project_slugs]
            if not targets:
                targets = [self.selected_project] if self.selected_project else []
            lines = [
                f"[b $accent]{len(targets)} projects selected[/]",
                "",
            ]
            total_sess = 0
            total_bytes = 0
            for p in targets:
                total_sess += p.session_count
                total_bytes += p.total_bytes
                mark = MARK_ON if p.slug in self.selected_project_slugs else MARK_OFF
                lines.append(
                    f"{mark} [$secondary]{p.cwd_guess[:42]}[/]  "
                    f"[$text-muted]{p.session_count} sess · {_fmt_size(p.total_bytes)}[/]"
                )
            lines.append("")
            lines.append(
                f"[b $primary]Total[/]  {total_sess} sessions · {_fmt_size(total_bytes)}"
            )
            lines.append("")
            lines.append(
                "[$text-muted]m/M Move · d/D Delete project · Space toggle mark · Tab to sessions[/]"
            )
            detail.update("\n".join(lines))
            return

        p = self.selected_project
        if not p:
            detail.update(
                "[$text-muted]Select a project[/]\n\n"
                "[$text-muted]Tab panes · ? help · , settings[/]"
            )
            return

        live_n = sum(
            1
            for s in p.sessions
            if (lv := self.live.get(s.session_id)) is not None
            and (lv.is_killable or lv.kind == "background")
        )
        marked = p.slug in self.selected_project_slugs
        lines = [
            f"[b $accent]{p.cwd_guess}[/]",
            f"[$text-muted]slug[/]     [$secondary]{p.slug}[/]",
            f"[$text-muted]path[/]     [$text-muted]{p.path}[/]",
            f"[$text-muted]sessions[/] {p.session_count}",
            f"[$text-muted]size[/]     {_fmt_size(p.total_bytes)}",
            f"[$text-muted]live/bg[/]  {live_n}",
            (
                f"[$text-muted]selected[/] "
                f"{'[$text-success]yes[/]' if marked else 'no'}"
            ),
            "",
            "[$text-muted]m/M Move · d/D Delete project · Tab/→ sessions[/]",
        ]
        detail.update("\n".join(lines))

    def _reload_projects_table(self) -> None:
        table = self.query_one("#projects", DataTable)
        keep_slug = self.selected_project.slug if self.selected_project else None
        table.clear()
        focus_row = 0
        for i, p in enumerate(self.projects):
            label = p.cwd_guess if len(p.cwd_guess) < 40 else p.slug[:38] + "…"
            mark = MARK_ON if p.slug in self.selected_project_slugs else MARK_OFF
            table.add_row(
                mark,
                label,
                str(p.session_count),
                _fmt_size(p.total_bytes),
                key=str(i),
            )
            if keep_slug and p.slug == keep_slug:
                focus_row = i
        if self.projects:
            if self.selected_project:
                match = next(
                    (p for p in self.projects if p.slug == self.selected_project.slug),
                    None,
                )
                self.selected_project = match or self.projects[focus_row]
            else:
                self.selected_project = self.projects[focus_row]
            table.move_cursor(row=focus_row, column=0, animate=False)
            self._reload_sessions_table()
        else:
            self.selected_project = None
            self.query_one("#sessions", DataTable).clear()
            self.query_one("#detail", Static).update(
                "[b]No projects found[/b]\n\n"
                f"Config root: {self.root}\n"
                "Press r to refresh · ? for help"
            )

    def _update_project_marks(self) -> None:
        table = self.query_one("#projects", DataTable)
        for i, p in enumerate(self.projects):
            mark = MARK_ON if p.slug in self.selected_project_slugs else MARK_OFF
            try:
                table.update_cell(str(i), "Sel", mark)
            except Exception:
                self._reload_projects_table()
                return
        self._show_detail()
        self._refresh_status()

    def _reload_sessions_table(self, *, focus_session_id: str | None = None) -> None:
        table = self.query_one("#sessions", DataTable)
        keep_id = focus_session_id or (
            self.selected_session.session_id if self.selected_session else None
        )
        table.clear()
        self._update_sessions_meta()
        if not self.selected_project:
            self.selected_session = None
            return
        sessions = self._visible_sessions()
        focus_row = 0
        found = False
        for i, s in enumerate(sessions):
            live = self.live.get(s.session_id)
            title = s.display_title.replace("\n", " ")[:48]
            updated = s.mtime_dt.strftime("%Y-%m-%d %H:%M")
            mark = (
                MARK_ON
                if s.session_id in self.selected_session_ids
                else MARK_OFF
            )
            table.add_row(
                mark,
                title,
                updated,
                _fmt_size(s.size_bytes),
                _badges(s, live),
                key=s.session_id,
            )
            if keep_id and s.session_id == keep_id:
                focus_row = i
                found = True
        if sessions:
            self.selected_session = sessions[focus_row if found else 0]
            table.move_cursor(
                row=focus_row if found else 0, column=0, animate=False
            )
        else:
            self.selected_session = None
        self._show_detail()

    def _update_session_marks(self) -> None:
        """Refresh Sel column marks without resetting cursor."""
        if not self.selected_project:
            return
        table = self.query_one("#sessions", DataTable)
        for s in self._visible_sessions():
            mark = (
                MARK_ON
                if s.session_id in self.selected_session_ids
                else MARK_OFF
            )
            try:
                table.update_cell(s.session_id, "Sel", mark)
            except Exception:
                self._reload_sessions_table(
                    focus_session_id=(
                        self.selected_session.session_id
                        if self.selected_session
                        else None
                    )
                )
                return
        self._show_detail()
        self._refresh_status()

    def _show_detail(self) -> None:
        """Right pane: project summary when projects focused; else session detail."""
        if self._focus_pane() == "projects":
            self._show_project_detail()
            return

        detail = self.query_one("#detail", Static)
        # Prefer explicit marks for multi-select view; else focused session.
        if self.selected_session_ids:
            targets = self._sessions_by_ids(self.selected_session_ids)
        elif self.selected_session:
            targets = [self.selected_session]
        else:
            targets = []

        if len(targets) > 1:
            lines = [
                f"[b $accent]{len(targets)} sessions selected[/]",
                "",
            ]
            total = 0
            for s in targets:
                total += s.size_bytes
                mark = (
                    MARK_ON
                    if s.session_id in self.selected_session_ids
                    else MARK_OFF
                )
                lines.append(
                    f"{mark} [$secondary]{s.session_id[:8]}…[/]  "
                    f"[$text-muted]{_fmt_size(s.size_bytes)}[/]  "
                    f"{s.display_title[:40]}"
                )
            lines.append("")
            lines.append(
                f"[b $primary]Total[/]  {_fmt_size(total)} across {len(targets)} session(s)"
            )
            detail.update("\n".join(lines))
            return

        s = targets[0] if targets else self.selected_session
        if not s:
            if self.selected_project and not self.selected_project.sessions:
                self._show_project_detail()
                return
            detail.update("[$text-muted]Select a session[/]")
            return
        live = self.live.get(s.session_id)
        inv = collect_fragments(
            s.session_id, project_slug=s.project_slug, root=self.root
        )
        title = s.display_title.replace("\n", " ")
        lines = [
            f"[b $accent]{title}[/]",
            f"[$text-muted]id[/]       [$secondary]{s.session_id}[/]",
            f"[$text-muted]cwd[/]      {s.cwd or '-'}",
            f"[$text-muted]project[/]  {s.project_slug}",
            (
                f"[$text-muted]size[/]     {_fmt_size(s.size_bytes)}   "
                f"[$text-muted]mtime[/] {s.mtime_dt.isoformat()}"
            ),
            f"[$text-muted]tags[/]     {_badges(s, live)}",
            (
                f"[$text-muted]selected[/] "
                f"{'[$text-success]yes[/]' if s.session_id in self.selected_session_ids else 'no'}"
            ),
            "",
            "[b $primary]Fragments[/]",
        ]
        for f in inv.fragments:
            flag = " [$text-warning](protected)[/]" if f.is_protected() else ""
            lines.append(
                f"  [$secondary]{f.kind}[/]: {_fmt_size(f.size_bytes)}{flag}"
            )
            lines.append(f"    [$text-muted]{f.path}[/]")
        if s.first_prompt:
            lines.append("")
            lines.append("[b $primary]First prompt[/]")
            lines.append(s.first_prompt[:500])
        detail.update("\n".join(lines))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        table = event.data_table
        if table.id == "projects" and event.row_key is not None:
            try:
                idx = int(str(event.row_key.value))
            except ValueError:
                return
            if 0 <= idx < len(self.projects):
                self.selected_project = self.projects[idx]
                self._reload_sessions_table()
                self._refresh_status()
        elif table.id == "sessions" and event.row_key is not None:
            if not self.selected_project:
                return
            sid = str(event.row_key.value)
            for s in self._visible_sessions():
                if s.session_id == sid:
                    self.selected_session = s
                    self._show_detail()
                    self._refresh_status()
                    return

    def action_toggle_select(self) -> None:
        pane = self._focus_pane()
        if pane == "detail":
            self._show_toast("Space has no effect in detail pane", success=False, seconds=2.5)
            return
        if pane == "projects":
            p = self.selected_project
            if not p:
                self._show_toast("No project focused", success=False, seconds=2.5)
                return
            if p.slug in self.selected_project_slugs:
                self.selected_project_slugs.discard(p.slug)
            else:
                self.selected_project_slugs.add(p.slug)
            self._update_project_marks()
            return
        if pane == "sessions":
            s = self.selected_session
            if not s:
                self._show_toast("No session focused", success=False, seconds=2.5)
                return
            if s.session_id in self.selected_session_ids:
                self.selected_session_ids.discard(s.session_id)
            else:
                self.selected_session_ids.add(s.session_id)
            self._update_session_marks()
            return
        self._show_toast("Focus projects or sessions to select", success=False, seconds=2.5)

    def action_select_all(self) -> None:
        pane = self._focus_pane()
        if pane == "projects":
            for p in self.projects:
                self.selected_project_slugs.add(p.slug)
            self._update_project_marks()
            return
        if pane in {"sessions", "detail"}:
            if not self.selected_project:
                return
            for s in self.selected_project.sessions:
                self.selected_session_ids.add(s.session_id)
            self._update_session_marks()
            return
        self._show_toast("Focus a pane to select all", success=False, seconds=2.5)

    def action_clear_select(self) -> None:
        """Clear project and session marks everywhere."""
        n_proj = len(self.selected_project_slugs)
        n_sess = len(self.selected_session_ids)
        self.selected_project_slugs.clear()
        self.selected_session_ids.clear()
        self._update_project_marks()
        self._update_session_marks()
        self._refresh_status()
        if n_proj or n_sess:
            self._show_toast(
                f"Cleared selections (proj={n_proj} sess={n_sess})",
                success=True,
                seconds=2.5,
            )
        else:
            self._show_toast("Nothing selected", success=False, seconds=2)

    def action_focus_session_filter(self) -> None:
        """Focus the sessions filter box."""
        inp = self.query_one("#sessions-filter", Input)
        inp.focus()
        inp.cursor_position = len(inp.value)

    def action_cycle_session_sort(self) -> None:
        """Cycle sessions sort mode."""
        if not self.selected_project:
            self._show_toast("Select a project first", success=False, seconds=2)
            return
        ids = [m[0] for m in SESSION_SORT_MODES]
        try:
            idx = ids.index(self._session_sort)
        except ValueError:
            idx = 0
        self._session_sort = ids[(idx + 1) % len(ids)]
        self._reload_sessions_table(
            focus_session_id=(
                self.selected_session.session_id if self.selected_session else None
            )
        )
        self._show_toast(f"Sort: {self._session_sort_label()}", success=True, seconds=2)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "sessions-filter":
            return
        self._session_filter = event.value
        self._reload_sessions_table(
            focus_session_id=(
                self.selected_session.session_id if self.selected_session else None
            )
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "sessions-filter":
            event.stop()
            self.query_one("#sessions", DataTable).focus()
            return
        # Other screens (rename/move/settings) handle submit on their own screens.

    def on_key(self, event: Key) -> None:
        """Esc in the sessions filter clears it and returns focus to the table."""
        if event.key != "escape":
            return
        focused = self.focused
        if focused is None or getattr(focused, "id", None) != "sessions-filter":
            return
        event.stop()
        event.prevent_default()
        inp = self.query_one("#sessions-filter", Input)
        if inp.value or self._session_filter:
            inp.value = ""
            self._session_filter = ""
            self._reload_sessions_table()
        self.query_one("#sessions", DataTable).focus()

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_show_settings(self) -> None:
        """Open provider / config-dir settings."""

        def _after(updated: Settings | None) -> None:
            if updated is None:
                self._show_toast("Settings cancelled", success=False, seconds=2)
                return
            try:
                path = save_settings(updated)
            except OSError as exc:
                self._show_toast(f"Could not save settings: {exc}", success=False, seconds=4)
                return
            self._axism_settings = updated
            self.provider = provider_from_settings(updated)
            self.root = self.provider.config_root()
            self.selected_project_slugs.clear()
            self.selected_session_ids.clear()
            self.selected_project = None
            self.selected_session = None
            self.action_refresh()
            self._show_toast(
                f"Saved {path.name} · {self.provider.label} → {self.root}",
                success=True,
                seconds=4,
            )

        self.push_screen(SettingsScreen(load_settings()), _after)

    def action_show_version(self) -> None:
        """Show version and runtime details."""
        try:
            import textual

            textual_ver = getattr(textual, "__version__", "?")
        except Exception:
            textual_ver = "?"
        lines = [
            f"[b $accent]aXism[/]  v{version_string()}",
            "[$text-muted]Agent Interactive Session Manager[/]",
            "",
        ]
        if __commit__:
            lines.append(f"[$text-muted]Commit[/]    {__commit__}")
        lines.extend(
            [
                f"[$text-muted]Python[/]    {sys.version.split()[0]}",
                f"[$text-muted]Textual[/]   {textual_ver}",
                f"[$text-muted]Platform[/]  {platform.system()} {platform.release()}",
                f"[$text-muted]Machine[/]   {platform.machine()}",
                "",
                f"[$text-muted]Provider[/]  {self.provider.label} ({self.provider.name})",
                f"[$text-muted]Config[/]    {self.root}",
                f"[$text-muted]Settings[/]  {settings_path()}",
                f"[$text-muted]Theme[/]     {self.theme}",
                "",
                f"[$text-muted]Projects[/]  {len(self.projects)}",
                (
                    f"[$text-muted]Sessions[/]  "
                    f"{sum(p.session_count for p in self.projects)}"
                ),
            ]
        )
        self.push_screen(VersionScreen("\n".join(lines)))

    def action_stop_sessions(self) -> None:
        """Stop background sessions (same as typing ``/stop`` while attached)."""
        candidates = self._target_sessions_for_stop()
        targets = self._stoppable_among(candidates)
        if not targets:
            self._show_toast(
                "Nothing to stop (need a live background session)",
                success=False,
                seconds=3,
            )
            self._refresh_status()
            return

        warnings = [
            "Runs `claude stop` — same effect as typing /stop while attached.",
            "Conversation/transcript is kept; use Delete to remove files.",
        ]
        lines = [
            f"[b]{len(targets)} background session(s)[/b] will be stopped.",
            "",
            "[b]Targets[/b]",
        ]
        for s, live in targets:
            title = s.display_title.replace("\n", " ")[:44]
            short = live.daemon_short or s.session_id[:8]
            lines.append(
                f"  {short}  {live.kind}/{live.state}  {title}"
            )
        body = "\n".join(lines)

        def _after(confirmed: bool | None) -> None:
            if not confirmed:
                self._set_status("Stop cancelled")
                self._show_toast("Stop cancelled", success=False, seconds=2.5)
                return
            ok_n = 0
            fail_n = 0
            details: list[str] = []
            last_fail = ""
            for s, live in targets:
                ok, msg = self.provider.slash_stop(s.session_id, live)
                last = msg.splitlines()[-1] if msg else ""
                details.append(f"{s.session_id[:8]}: {last}")
                if ok:
                    ok_n += 1
                else:
                    fail_n += 1
                    last_fail = last
            self._set_status("; ".join(details)[:200])
            self.action_refresh()
            if fail_n and not ok_n:
                tip = last_fail[:80] if last_fail else "see status line"
                self._show_toast(f"Stop failed: {tip}", success=False, seconds=5)
            elif fail_n:
                self._show_toast(
                    f"Stopped {ok_n}, failed {fail_n}", success=False, seconds=4
                )
            elif ok_n == 1:
                self._show_toast(
                    f"Stopped: {targets[0][0].display_title[:48]}", success=True
                )
            else:
                self._show_toast(f"Stopped {ok_n} sessions", success=True)

        self.push_screen(
            ConfirmDeleteScreen(
                body,
                title=f"Stop {len(targets)} background session(s)",
                warnings=warnings,
                confirm_verb="Stop",
            ),
            _after,
        )

    def action_resume_session(self) -> None:
        """Exit axism and hand off to ``claude attach`` or ``claude --resume``."""
        if self._focus_pane() == "projects":
            self._show_toast("Focus a session to open", success=False, seconds=2.5)
            return
        s = self.selected_session
        if not s:
            self._show_toast("No session focused to open", success=False)
            return
        live = self.live.get(s.session_id)
        self.exit(
            {
                "open_id": s.session_id,
                "cwd": s.cwd,
                "daemon_short": (live.daemon_short if live else None),
                "live_kind": (live.kind if live else None),
                "live_state": (live.state if live else None),
            }
        )

    def action_rename_session(self) -> None:
        """Rename the focused session (append custom-title like Claude /rename)."""
        if self._focus_pane() == "projects":
            self._show_toast("Focus a session to rename", success=False, seconds=2.5)
            return
        s = self.selected_session
        if not s:
            self._show_toast("No session focused to rename", success=False)
            return
        current = s.display_title.replace("\n", " ").strip()

        def _after(new_title: str | None) -> None:
            if not new_title:
                self._show_toast("Rename cancelled", success=False, seconds=2)
                return
            if new_title == current:
                self._show_toast("Title unchanged", success=False, seconds=2)
                return
            ok, msg = rename_session(
                s.session_id,
                new_title,
                root=self.root,
                project_slug=s.project_slug,
            )
            self.action_refresh()
            self._show_toast(msg, success=ok, seconds=3.5)

        self.push_screen(
            RenameSessionScreen(current, s.session_id[:8]),
            _after,
        )

    def _move_choices(
        self, *, exclude_slugs: set[str] | None = None
    ) -> list[tuple[str, str]]:
        exclude = exclude_slugs or set()
        choices: list[tuple[str, str]] = []
        for p in self.projects:
            if p.slug in exclude:
                continue
            label = (
                f"{p.cwd_guess}  ·  {p.session_count} sess · {_fmt_size(p.total_bytes)}"
            )
            choices.append((p.cwd_guess, label))
        return choices

    def _sessions_are_live_blocked(self, sessions: list[SessionMeta]) -> str | None:
        """Return a short reason if any session should be stopped before move."""
        live_n = 0
        for s in sessions:
            live = self.live.get(s.session_id)
            if live is None:
                continue
            if live.is_killable or (
                live.kind == "background"
                and live.state in {"working", "blocked", "running"}
            ):
                live_n += 1
        if live_n:
            return f"{live_n} live/background session(s) — stop them first (s)"
        return None

    def action_move_items(self) -> None:
        """Move focused/marked project(s) or session(s) to another project path."""
        pane = self._focus_pane()

        if pane == "projects":
            targets = self._target_projects()
            if not targets:
                self._show_toast("No project to move", success=False, seconds=2.5)
                return
            blocked = self._sessions_are_live_blocked(
                [s for p in targets for s in p.sessions]
            )
            if blocked:
                self._show_toast(blocked, success=False, seconds=4)
                return
            exclude = {p.slug for p in targets}
            n = len(targets)
            title = f"Move {n} project{'s' if n != 1 else ''}"
            hint = (
                "Select destination project, or type a new absolute workspace path. "
                "One project → new path renames the dir; otherwise sessions are merged."
            )

            def _after_dest(dest: str | None) -> None:
                if not dest:
                    self._show_toast("Move cancelled", success=False, seconds=2)
                    return

                def _after_confirm(confirmed: bool | None) -> None:
                    if not confirmed:
                        self._show_toast("Move cancelled", success=False, seconds=2)
                        return
                    result = move_projects(targets, dest, root=self.root)
                    self.selected_project_slugs.clear()
                    self.action_refresh()
                    self._show_toast(result.message, success=result.ok, seconds=4)
                    if result.ok and result.memory_needs_agent:
                        self._offer_memory_agent_handoff(result)

                body_lines = [
                    f"[b]{n} project(s)[/b] → [$secondary]{dest}[/]",
                    "",
                    "[b]Sources[/b]",
                ]
                for p in targets:
                    body_lines.append(
                        f"  {p.cwd_guess}  ({p.session_count} sessions)"
                    )
                self.push_screen(
                    ConfirmDeleteScreen(
                        "\n".join(body_lines),
                        title=title,
                        warnings=[
                            "Claude project dir is renamed/merged under ~/.claude/projects/.",
                            (
                                "Creates the workspace folder if missing "
                                "(does not move your source tree files)."
                            ),
                            (
                                "All transcript cwd fields become the destination path. "
                                "Memory merges; dual MEMORY.md may offer agent handoff."
                            ),
                        ],
                        confirm_verb="Move",
                    ),
                    _after_confirm,
                )

            self.push_screen(
                MoveTargetScreen(title, self._move_choices(exclude_slugs=exclude), hint),
                _after_dest,
            )
            return

        if pane in {"sessions", "detail"}:
            sessions = self._target_sessions_for_move()
            if not sessions:
                self._show_toast("No session to move", success=False, seconds=2.5)
                return
            blocked = self._sessions_are_live_blocked(sessions)
            if blocked:
                self._show_toast(blocked, success=False, seconds=4)
                return
            exclude = {s.project_slug for s in sessions}
            n = len(sessions)
            title = f"Move {n} session{'s' if n != 1 else ''}"
            hint = (
                "Select destination project, or type an absolute workspace path "
                "(creates the project dir if needed)."
            )

            def _after_dest(dest: str | None) -> None:
                if not dest:
                    self._show_toast("Move cancelled", success=False, seconds=2)
                    return

                def _after_confirm(confirmed: bool | None) -> None:
                    if not confirmed:
                        self._show_toast("Move cancelled", success=False, seconds=2)
                        return
                    result = move_sessions(sessions, dest, root=self.root)
                    self.selected_session_ids.clear()
                    self.action_refresh()
                    self._show_toast(result.message, success=result.ok, seconds=4)

                body_lines = [
                    f"[b]{n} session(s)[/b] → [$secondary]{dest}[/]",
                    "",
                    "[b]Targets[/b]",
                ]
                for s in sessions[:30]:
                    body_lines.append(
                        f"  {s.session_id[:8]}…  {s.display_title[:40]}"
                    )
                if n > 30:
                    body_lines.append(f"  … +{n - 30} more")
                self.push_screen(
                    ConfirmDeleteScreen(
                        "\n".join(body_lines),
                        title=title,
                        warnings=[
                            "Session files move to the destination Claude project directory.",
                            (
                                "Creates the workspace folder if missing; cwd fields "
                                "and history project entries are updated."
                            ),
                        ],
                        confirm_verb="Move",
                    ),
                    _after_confirm,
                )

            self.push_screen(
                MoveTargetScreen(title, self._move_choices(exclude_slugs=exclude), hint),
                _after_dest,
            )
            return

        self._show_toast("Focus projects or sessions to move", success=False, seconds=2.5)

    def _offer_memory_agent_handoff(self, result: MoveResult) -> None:
        """After a merge that parked MEMORY.md, offer agent handoff or save brief."""
        from axism.agents import (
            detect_agents,
            exec_agent_handoff,
            write_memory_merge_brief,
        )

        stats = result.memory_stats
        over = bool(stats and stats.over_limits())

        def _pick(force: bool) -> None:
            agents = detect_agents()
            choices: list[tuple[str, str]] = [
                (a.id, f"{a.label}  ({a.binary})") for a in agents
            ]
            choices.append(("save", "Save brief only (no handoff)"))

            def _after_pick(choice: str | None) -> None:
                if not choice:
                    self._show_toast("Agent handoff skipped", success=False, seconds=2.5)
                    return
                brief = write_memory_merge_brief(
                    result, root=self.root, force=force
                )
                if choice == "save":
                    self._show_toast(
                        f"Brief saved: {brief}", success=True, seconds=5
                    )
                    return
                agent = next((a for a in agents if a.id == choice), None)
                if agent is None:
                    self._show_toast(
                        f"Brief saved: {brief}", success=True, seconds=5
                    )
                    return
                self._show_toast(
                    f"Handing off to {agent.label}…", success=True, seconds=2
                )
                try:
                    exec_agent_handoff(agent, brief)
                except FileNotFoundError as exc:
                    self._show_toast(str(exc), success=False, seconds=4)
                except OSError as exc:
                    self._show_toast(f"Handoff failed: {exc}", success=False, seconds=4)

            self.push_screen(
                AgentPickerScreen("Memory merge — choose agent", choices),
                _after_pick,
            )

        if over:
            body = (
                f"[b]Memory merge exceeds handoff limits[/]\n\n"
                f"{stats.summary() if stats else ''}\n\n"
                "You can still force an agent handoff, or skip."
            )

            def _after_force(confirmed: bool | None) -> None:
                if confirmed:
                    _pick(force=True)
                else:
                    brief = write_memory_merge_brief(
                        result, root=self.root, force=False
                    )
                    self._show_toast(
                        f"Brief saved (no handoff): {brief}",
                        success=True,
                        seconds=5,
                    )

            self.push_screen(
                ConfirmDeleteScreen(
                    body,
                    title="Memory merge over limits",
                    warnings=[
                        "Forced handoff still tells the agent to refuse if unsafe.",
                    ],
                    confirm_verb="Force",
                ),
                _after_force,
            )
            return

        _pick(force=False)

    def action_pick_theme(self) -> None:
        names = self._theme_names or sorted(self.available_themes.keys())
        if not names:
            self._show_toast("No themes available", success=False)
            return

        def _after(chosen: str | None) -> None:
            if not chosen:
                return
            if chosen not in self.available_themes:
                self._show_toast(f"Unknown theme: {chosen}", success=False)
                return
            self.theme = chosen
            self._show_toast(f"Theme: {chosen}", success=True, seconds=2.5)

        self.push_screen(ThemePickerScreen(names, self.theme), _after)

    def _cycle_theme(self, step: int) -> None:
        names = self._theme_names or sorted(self.available_themes.keys())
        if not names:
            self._show_toast("No themes available", success=False)
            return
        try:
            idx = names.index(self.theme)
        except ValueError:
            idx = 0
        new_name = names[(idx + step) % len(names)]
        self.theme = new_name
        self._show_toast(f"Theme: {new_name}", success=True, seconds=2.5)

    def action_next_theme(self) -> None:
        self._cycle_theme(1)

    def action_prev_theme(self) -> None:
        self._cycle_theme(-1)

    def _confirm_delete_projects(self, projects: list[ProjectInfo]) -> None:
        if not projects:
            self._show_toast("No project selected", success=False, seconds=2.5)
            return
        packed: list[tuple[str, str, object, list[str]]] = []
        for p in projects:
            plan = build_project_delete_plan(
                p.slug, root=self.root, force=True, keep_memory=False
            )
            preview = execute_project_delete(
                plan, root=self.root, dry_run=True, force=True, stop_live=True
            )
            packed.append((p.slug, p.cwd_guess, plan, preview))
        body, warnings = _format_project_delete_confirm(packed)
        slugs = [p.slug for p in projects]

        def _after(confirmed: bool | None) -> None:
            if not confirmed:
                self._set_status("Project delete cancelled")
                self._show_toast("Project delete cancelled", success=False, seconds=2.5)
                return
            all_actions: list[str] = []
            for slug in slugs:
                plan_exec = build_project_delete_plan(
                    slug, root=self.root, force=True, keep_memory=False
                )
                actions = execute_project_delete(
                    plan_exec,
                    root=self.root,
                    dry_run=False,
                    force=True,
                    stop_live=True,
                )
                all_actions.extend(actions)
                self.selected_project_slugs.discard(slug)
            ok, msg = _summarize_project_delete(slugs, all_actions)
            self.selected_session_ids.clear()
            self._set_status("; ".join(all_actions)[:200])
            self.action_refresh()
            self._show_toast(msg, success=ok)

        n = len(projects)
        self.push_screen(
            ConfirmDeleteScreen(
                body,
                title=(
                    f"Delete {n} project{'s' if n != 1 else ''}"
                    if n != 1
                    else "Delete project"
                ),
                warnings=warnings
                + [
                    "Deletes the Claude project directory and linked session data.",
                    "Source workspace files on disk are not moved or removed.",
                ],
                confirm_verb="Delete",
                project_danger=True,
            ),
            _after,
        )

    def _confirm_delete_sessions(self, targets: list[SessionMeta]) -> None:
        if not targets:
            self._show_toast("No session selected", success=False, seconds=2.5)
            return
        plans = [
            build_delete_plan(
                s.session_id,
                project_slug=s.project_slug,
                root=self.root,
                force=True,
            )
            for s in targets
        ]
        preview_actions = execute_deletes(
            plans, root=self.root, dry_run=True, force=True, stop_live=True
        )
        body, warnings = _format_session_delete_confirm(
            targets, plans, preview_actions
        )

        def _after(confirmed: bool | None) -> None:
            if not confirmed:
                self._set_status("Delete cancelled")
                self._show_toast("Delete cancelled", success=False, seconds=2.5)
                return
            plans_exec = [
                build_delete_plan(
                    s.session_id,
                    project_slug=s.project_slug,
                    root=self.root,
                    force=True,
                )
                for s in targets
            ]
            actions = execute_deletes(
                plans_exec, root=self.root, dry_run=False, force=True, stop_live=True
            )
            ok, msg = _summarize_session_delete(targets, actions)
            self.selected_session_ids.clear()
            self._set_status("; ".join(actions)[:200])
            self.action_refresh()
            self._show_toast(msg, success=ok)

        n = len(targets)
        self.push_screen(
            ConfirmDeleteScreen(
                body,
                title=f"Delete {n} session{'s' if n != 1 else ''}",
                warnings=warnings,
                confirm_verb="Delete",
            ),
            _after,
        )

    def action_delete_items(self) -> None:
        """Pane-aware delete: projects → projects; sessions/detail → sessions."""
        if self._focus_pane() == "projects":
            self._confirm_delete_projects(self._target_projects())
            return
        self._confirm_delete_sessions(self._target_sessions_for_delete())


def run_tui() -> None:
    app = SessionManagerApp()
    result = app.run()
    if not isinstance(result, dict):
        return
    open_id = result.get("open_id") or result.get("resume_id")
    if not open_id:
        return
    live = None
    if result.get("live_kind"):
        live = LiveSession(
            session_id=str(open_id),
            kind=str(result["live_kind"]),
            state=result.get("live_state"),
            daemon_short=result.get("daemon_short"),
        )
    try:
        exec_open(str(open_id), cwd=result.get("cwd"), live=live)
    except FileNotFoundError as exc:
        print(f"axism: {exc}", file=__import__("sys").stderr)
        raise SystemExit(1) from exc
    except OSError as exc:
        print(f"axism: failed to open session: {exc}", file=__import__("sys").stderr)
        raise SystemExit(1) from exc
