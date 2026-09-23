"""Textual TUI for aXism — Agent Interactive Session Manager."""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Key, Resize
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Input, OptionList, Static
from textual.widgets._header import HeaderClock, HeaderClockSpace
from textual.widgets.option_list import Option

from axism import __commit__, version_string
from axism.discover import ProjectInfo, SessionMeta
from axism.live import LiveSession
from axism.move import MoveResult
from axism.providers import (
    PROVIDER_LABELS,
    UnsupportedOperation,
    enabled_providers_from_settings,
    federate_discover,
    federate_merge_live,
    get_provider,
    live_for_session,
    provider_by_name,
    provider_config_hint,
    roots_summary,
)
from axism.providers.transfer import transfer_sessions
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

# Short agent labels for the narrow projects pane (full names live in detail).
AGENT_SHORT: dict[str, str] = {
    "claude_code": "Claude",
    "cursor": "Cursor",
    "hermes": "Hermes",
}

APP_NAME = "aXism"
APP_TAGLINE = "Agent Interactive Session Manager"
APP_BRAND = f"{APP_NAME} - {APP_TAGLINE}"

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


@dataclass(frozen=True)
class ProjectsLayout:
    """Projects pane column plan sized to the available width."""

    # Density: "full" | "cozy" | "tight"
    density: str
    show_agent_col: bool
    show_count: bool
    show_size: bool
    # When True, prefix the project cell with a 1-char agent glyph instead of
    # a separate Agent column (saves space on narrow panes).
    agent_in_project: bool
    project_width: int
    agent_width: int = 6


@dataclass(frozen=True)
class SessionsLayout:
    """Sessions pane column plan sized to the available width."""

    density: str
    show_updated: bool
    show_size: bool
    show_tags: bool
    title_width: int
    updated_width: int = 11
    tags_width: int = 8


def _ellipsis(text: str, max_len: int) -> str:
    if max_len <= 0:
        return ""
    if len(text) <= max_len:
        return text
    if max_len == 1:
        return "…"
    return text[: max_len - 1] + "…"


def _short_path(path: str, max_len: int) -> str:
    """Fit a cwd into ``max_len``, preferring a readable tail over mid-cut."""
    if max_len <= 0:
        return ""
    text = path.strip() or path
    if len(text) <= max_len:
        return text
    parts = [p for p in text.replace("\\", "/").split("/") if p]
    if not parts:
        return _ellipsis(text, max_len)
    # Prefer basename, then …/parent/base, else hard ellipsis.
    base = parts[-1]
    if len(base) <= max_len:
        if len(parts) == 1:
            return base
        if len(base) + 2 <= max_len:
            # Try two-segment tail when it fits.
            if len(parts) >= 2:
                two = f"…/{parts[-2]}/{base}"
                if len(two) <= max_len:
                    return two
            return f"…/{base}"
    return _ellipsis(base if len(base) >= max_len else text, max_len)


def _agent_glyph(provider: str | None) -> str:
    """One-character agent mark for tight project rows."""
    return {
        "claude_code": "A",
        "cursor": "C",
        "hermes": "H",
    }.get(provider or "", "?")


def plan_projects_layout(budget: int, *, multi_agent: bool) -> ProjectsLayout:
    """Choose projects columns that fit in ``budget`` cells (no h-scroll)."""
    # Padding for border/cursor chrome inside the DataTable.
    width = max(8, budget - 1)
    # Always: Sel(1) + gap. Project absorbs the rest.
    if width >= 34 and multi_agent:
        # Sel + Agent(6) + Project + #(3) + Size(5) + gaps
        fixed = 1 + 6 + 3 + 5 + 4
        return ProjectsLayout(
            density="full",
            show_agent_col=True,
            show_count=True,
            show_size=True,
            agent_in_project=False,
            project_width=max(8, width - fixed),
            agent_width=6,
        )
    if width >= 28 and multi_agent:
        fixed = 1 + 6 + 3 + 3
        return ProjectsLayout(
            density="cozy",
            show_agent_col=True,
            show_count=True,
            show_size=False,
            agent_in_project=False,
            project_width=max(8, width - fixed),
            agent_width=6,
        )
    if width >= 26 and not multi_agent:
        fixed = 1 + 3 + 5 + 3
        return ProjectsLayout(
            density="full",
            show_agent_col=False,
            show_count=True,
            show_size=True,
            agent_in_project=False,
            project_width=max(8, width - fixed),
        )
    if width >= 20:
        # Sel + optional "H " prefix in project + count
        prefix = 2 if multi_agent else 0
        fixed = 1 + 3 + (2 if multi_agent else 1) + prefix
        return ProjectsLayout(
            density="cozy",
            show_agent_col=False,
            show_count=True,
            show_size=False,
            agent_in_project=multi_agent,
            project_width=max(6, width - fixed),
        )
    # Ultra-tight: mark + project only (agent glyph inlined when federated).
    prefix = 2 if multi_agent else 0
    fixed = 1 + 1 + prefix
    return ProjectsLayout(
        density="tight",
        show_agent_col=False,
        show_count=False,
        show_size=False,
        agent_in_project=multi_agent,
        project_width=max(4, width - fixed),
    )


def plan_sessions_layout(budget: int) -> SessionsLayout:
    """Choose sessions columns that fit in ``budget`` cells (no h-scroll)."""
    width = max(10, budget - 1)
    if width >= 56:
        fixed = 1 + 11 + 5 + 10 + 4
        return SessionsLayout(
            density="full",
            show_updated=True,
            show_size=True,
            show_tags=True,
            title_width=max(10, width - fixed),
            updated_width=11,
            tags_width=10,
        )
    if width >= 40:
        fixed = 1 + 11 + 5 + 3
        return SessionsLayout(
            density="cozy",
            show_updated=True,
            show_size=True,
            show_tags=False,
            title_width=max(10, width - fixed),
            updated_width=11,
        )
    if width >= 28:
        # Compact date: MM-DD only
        fixed = 1 + 5 + 2
        return SessionsLayout(
            density="cozy",
            show_updated=True,
            show_size=False,
            show_tags=False,
            title_width=max(8, width - fixed),
            updated_width=5,
        )
    fixed = 1 + 1
    return SessionsLayout(
        density="tight",
        show_updated=False,
        show_size=False,
        show_tags=False,
        title_width=max(6, width - fixed),
    )


class AxisHeader(Widget):
    """Top bar: brand + version on the left, optional clock on the right."""

    def __init__(self, *, show_clock: bool = True) -> None:
        super().__init__()
        self._show_clock = show_clock

    def compose(self) -> ComposeResult:
        with Horizontal(id="header-left"):
            yield Static(self._label_for_width(120), id="header-brand")
        yield HeaderClock() if self._show_clock else HeaderClockSpace()

    def on_mount(self) -> None:
        self._sync_brand()

    def on_resize(self) -> None:
        self._sync_brand()

    def _label_for_width(self, width: int) -> str:
        """Prefer the full brand; never ellipsize mid-name — drop the tagline first."""
        ver = f"v{version_string()}"
        full = f"{APP_BRAND}  {ver}"
        short = f"{APP_NAME}  {ver}"
        # Leave room for clock (~10) and padding.
        budget = max(8, width - 12)
        if len(full) <= budget:
            return full
        return short

    def _sync_brand(self) -> None:
        try:
            brand = self.query_one("#header-brand", Static)
        except Exception:
            return
        brand.update(self._label_for_width(self.size.width))


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
                    "!  This removes the whole project directory and linked "
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
  Tab               Switch pane focus (detail → sessions → projects)
  ← / →             Switch pane focus (spatial left / right)
  ↑ ↓               Move within a list

[b $primary]Select[/] [dim](pane-aware)[/]
  Space             Projects: toggle project mark · Sessions: toggle session
  a / A             Projects: select all projects · Sessions/Detail: all sessions in project
  c / C             Clear ALL marks (projects + sessions, every project)

[b $success]Session[/] [dim](sessions/detail only)[/]
  o / O             Open focused session in its agent CLI (exits axism)
  s / S             Stop a live/background session (backend dependent)
  n / N             Rename focused session (custom title)
  /                 Focus session filter
  .                 Cycle session sort (updated / title / size)
  Esc               Clear filter (when filter focused)

[b $primary]Move[/]
  m / M             Projects: move project(s) · Sessions/Detail: move session(s)
                    pick existing project or type a destination path
                    (same agent relocates; other agent copies text turns, source kept)
                    (Tab path field · project merges include memory/)

[b $error]Delete[/]
  d / D             Projects pane: delete project(s)
                    Sessions/Detail: delete session(s)
  y / n / Esc       Confirm or cancel in dialogs

[b $warning]App[/]
  r / R             Refresh session list
  t                 Theme picker
  T                 Cycle theme
  ,                 Settings (include agents + config dirs)
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
    """Prompt for a new session title (rename or copy-as)."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True, priority=True),
        Binding("enter", "apply", "Apply", show=True, priority=True),
    ]

    def __init__(
        self,
        current_title: str,
        session_short: str,
        *,
        heading: str = "Rename session",
        hint: str = "Enter a new title for this session",
        allow_empty: bool = False,
    ) -> None:
        super().__init__()
        self.current_title = current_title
        self.session_short = session_short
        self.heading = heading
        self.hint = hint
        self.allow_empty = allow_empty

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(
                f"[b]{self.heading}[/b]  {self.session_short}…",
                id="rename-title",
            ),
            Static(self.hint, id="rename-hint"),
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
        if value:
            self.dismiss(value)
        elif self.allow_empty:
            self.dismiss(self.current_title or None)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        value = event.value.strip()
        if value:
            self.dismiss(value)
        elif self.allow_empty:
            self.dismiss(self.current_title or None)
        else:
            self.dismiss(None)


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
    """Choose which backends to include, active default, and config dirs."""

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
            Static("[b]Settings[/]  ·  providers + config dirs", id="settings-title"),
            Static(
                "Space includes a backend in the inventory. Enter sets the "
                "highlighted one as the CLI default (→).",
                id="settings-hint",
            ),
            OptionList(*options, id="settings-providers"),
            Static(
                "Config dir for highlighted provider (blank = default)",
                id="settings-path-label",
            ),
            Input(
                value=path_value,
                placeholder=provider_config_hint(active),
                id="settings-path",
            ),
            Static(
                f"[$text-muted]Saved at {settings_path()}[/]\n"
                "↑↓ provider · Space include · Tab path · Enter save · Esc cancel",
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
        inp.placeholder = provider_config_hint(pid)
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
    /* Two rows so the brand reads larger; no borders (they eat the content row). */
    AxisHeader {
        dock: top;
        width: 100%;
        height: 2;
        background: #1a1f2a;
        color: #e8eef5;
    }
    AxisHeader #header-left {
        dock: left;
        width: 1fr;
        height: 2;
        layout: horizontal;
        background: #1a1f2a;
        align: left middle;
    }
    AxisHeader #header-brand {
        width: auto;
        height: 1;
        padding: 0 1;
        color: #e8eef5;
        background: #1a1f2a;
        text-style: bold;
        text-wrap: nowrap;
        text-overflow: clip;
        text-opacity: 100%;
    }
    AxisHeader HeaderClock {
        dock: right;
        height: 2;
        color: #a8b8c8;
        background: #1a1f2a;
        text-opacity: 100%;
        content-align: right middle;
    }
    #body {
        height: 1fr;
        background: $background;
    }
    #projects, #sessions-pane, #detail {
        background: $surface;
        border: solid $panel;
        border-title-style: bold;
    }
    #projects.-focused, #sessions-pane.-focused, #detail.-focused {
        border: solid $accent;
        background: $boost;
    }
    # Flex panes: projects stays usable on narrow terminals (old 25% got too thin).
    #projects {
        width: 1fr;
        min-width: 18;
    }
    #sessions-pane {
        width: 2fr;
        min-width: 24;
        layout: vertical;
    }
    #detail {
        width: 2fr;
        min-width: 20;
        padding: 0 1;
    }
    #sessions {
        height: 1fr;
        border: none;
        border-bottom: solid $panel;
        background: $surface;
    }
    #sessions-pane.-focused #sessions {
        border-bottom: solid $accent 25%;
        background: $boost;
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
    DataTable {
        background: transparent;
        overflow-x: hidden;
        scrollbar-size-horizontal: 0;
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
        # Tab cycles right→left (detail → sessions → projects); arrows stay spatial.
        Binding("tab", "focus_previous", "Pane", show=True),
        Binding("shift+tab", "focus_next", "Pane", show=False),
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
        self.providers = enabled_providers_from_settings(self._axism_settings)
        self.provider = (
            provider_by_name(self.providers, self._axism_settings.active_provider)
            or self.providers[0]
        )
        self.root = self.provider.config_root()
        self.projects: list[ProjectInfo] = []
        self.live: dict[str, LiveSession] = {}
        self.selected_project: ProjectInfo | None = None
        self.selected_session: SessionMeta | None = None
        # Composite keys: provider:slug / provider:session_id when federated.
        self.selected_session_ids: set[str] = set()
        self.selected_project_slugs: set[str] = set()
        self._toast_timer: Timer | None = None
        self._theme_names: list[str] = []
        self._session_filter = ""
        self._session_sort = SESSION_SORT_MODES[0][0]
        self._projects_layout: ProjectsLayout | None = None
        self._sessions_layout: SessionsLayout | None = None

    def compose(self) -> ComposeResult:
        yield AxisHeader(show_clock=True)
        with Horizontal(id="body"):
            yield DataTable(id="projects", cursor_type="row")
            with Vertical(id="sessions-pane"):
                yield DataTable(id="sessions", cursor_type="row")
                yield Static("", id="sessions-meta")
                yield Input(
                    placeholder="/ filter · . sort",
                    id="sessions-filter",
                )
            yield Static("Select a session  ·  ? help", id="detail")
        yield Static("", id="status")
        yield Static("", id="toast")
        yield Footer(compact=True, show_command_palette=False)

    def on_mount(self) -> None:
        self.register_theme(AXISM_THEME)
        if "axism" in self.available_themes:
            self.theme = "axism"
        # Title/subtitle are for OS chrome; AxisHeader paints the visible bar.
        self.title = f"{APP_BRAND}  v{version_string()}"
        self.sub_title = ""
        self._theme_names = sorted(self.available_themes.keys())
        proj_table = self.query_one("#projects", DataTable)
        proj_table.border_title = "Projects"
        proj_table.show_horizontal_scrollbar = False
        sess_table = self.query_one("#sessions", DataTable)
        sess_table.show_horizontal_scrollbar = False
        sess_pane = self.query_one("#sessions-pane", Vertical)
        sess_pane.border_title = "Sessions"
        detail = self.query_one("#detail", Static)
        detail.border_title = "Detail"
        self.theme_changed_signal.subscribe(self, self._on_theme_changed)
        self._sync_pane_focus_classes()
        self.action_refresh()

    def _multi_agent(self) -> bool:
        return len(self.providers) > 1

    def _agent_label(self, name: str | None, *, short: bool = False) -> str:
        if not name:
            return "?"
        if short:
            return AGENT_SHORT.get(name, name[:6])
        return PROVIDER_LABELS.get(name, name)

    def _provider_for_project(self, project: ProjectInfo):
        found = provider_by_name(self.providers, project.provider)
        return found or self.provider

    def _provider_for_session(self, session: SessionMeta):
        found = provider_by_name(self.providers, session.provider)
        return found or self.provider

    def _compute_projects_layout(self) -> ProjectsLayout:
        table = self.query_one("#projects", DataTable)
        return plan_projects_layout(
            table.size.width or 24, multi_agent=self._multi_agent()
        )

    def _compute_sessions_layout(self) -> SessionsLayout:
        table = self.query_one("#sessions", DataTable)
        return plan_sessions_layout(table.size.width or 40)

    def _setup_projects_columns(self, layout: ProjectsLayout | None = None) -> None:
        table = self.query_one("#projects", DataTable)
        layout = layout or self._compute_projects_layout()
        if self._projects_layout == layout and table.columns:
            return
        table.clear(columns=True)
        table.add_column("Sel", width=1, key="Sel")
        if layout.show_agent_col:
            table.add_column("Agent", width=layout.agent_width, key="Agent")
        table.add_column("Project", width=layout.project_width, key="Project")
        if layout.show_count:
            table.add_column("#", width=3, key="Sessions")
        if layout.show_size:
            table.add_column("Size", width=5, key="Size")
        self._projects_layout = layout

    def _setup_sessions_columns(self, layout: SessionsLayout | None = None) -> None:
        table = self.query_one("#sessions", DataTable)
        layout = layout or self._compute_sessions_layout()
        if self._sessions_layout == layout and table.columns:
            return
        table.clear(columns=True)
        table.add_column("Sel", width=1, key="Sel")
        table.add_column("Title", width=layout.title_width, key="Title")
        if layout.show_updated:
            table.add_column("Updated", width=layout.updated_width, key="Updated")
        if layout.show_size:
            table.add_column("Size", width=5, key="Size")
        if layout.show_tags:
            table.add_column("Tags", width=layout.tags_width, key="Tags")
        self._sessions_layout = layout

    def on_resize(self, _event: Resize) -> None:
        """Refit list columns when the terminal or pane widths change."""
        if not self.is_mounted or len(self.screen_stack) > 1:
            return
        try:
            self.query_one("#projects", DataTable)
            self.query_one("#sessions", DataTable)
        except Exception:
            return
        old_p, old_s = self._projects_layout, self._sessions_layout
        new_p, new_s = self._compute_projects_layout(), self._compute_sessions_layout()
        if new_p != old_p:
            self._reload_projects_table()
        elif new_s != old_s:
            self._reload_sessions_table()

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
                live = live_for_session(self.live, s)
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
            return [p for p in self.projects if p.key in self.selected_project_slugs]
        if self._focus_pane() == "projects":
            return [self.selected_project] if self.selected_project else []
        return [self.selected_project] if self.selected_project else []

    def _sessions_by_ids(self, ids: set[str]) -> list[SessionMeta]:
        out: list[SessionMeta] = []
        for p in self.projects:
            for s in p.sessions:
                if s.key in ids or s.session_id in ids:
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
        """Live sessions their owning backend can stop."""
        out: list[tuple[SessionMeta, LiveSession]] = []
        for s in sessions:
            live = live_for_session(self.live, s)
            provider = self._provider_for_session(s)
            if provider.can_stop(live) and live is not None:
                out.append((s, live))
        return out

    def _same_provider_or_toast(
        self, items: list[ProjectInfo] | list[SessionMeta], *, action: str
    ) -> str | None:
        """Return shared provider name, or toast and return None if mixed."""
        names = {getattr(i, "provider", None) for i in items}
        names.discard(None)
        if len(names) > 1:
            self._show_toast(
                f"Cannot {action} across different agent tools — unmark mixed rows",
                success=False,
                seconds=5,
            )
            return None
        if not names:
            return self.provider.name
        return next(iter(names))

    def action_refresh(self) -> None:
        self.projects = federate_discover(self.providers)
        self.live = federate_merge_live(self.providers, use_cli=True)
        known_sessions = {s.key for p in self.projects for s in p.sessions}
        self.selected_session_ids &= known_sessions
        known_slugs = {p.key for p in self.projects}
        self.selected_project_slugs &= known_slugs
        self._reload_projects_table()
        self._refresh_status()

    def _refresh_status(self, extra: str = "") -> None:
        pane = self._focus_pane()
        pane_label = {"projects": "projects", "sessions": "sessions", "detail": "detail"}.get(
            pane, pane
        )
        if self._multi_agent():
            agent_bit = f"[$text-muted]{len(self.providers)} agents[/]"
            root_bit = f"[$text-muted]{roots_summary(self.providers)}[/]"
        else:
            agent_bit = f"[$text-muted]{self.provider.label}[/]"
            root_bit = f"[$text-muted]{self.root}[/]"
        bits = [
            f"[b $accent]{pane_label}[/]",
            f"mark {len(self.selected_project_slugs)}p/{len(self.selected_session_ids)}s",
            agent_bit,
            root_bit,
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
            targets = [p for p in self.projects if p.key in self.selected_project_slugs]
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
                mark = MARK_ON if p.key in self.selected_project_slugs else MARK_OFF
                agent = self._agent_label(p.provider)
                lines.append(
                    f"{mark} [$secondary]{agent}[/]  "
                    f"[$secondary]{p.cwd_guess[:36]}[/]  "
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
            if (lv := live_for_session(self.live, s)) is not None
            and (lv.is_killable or lv.kind == "background")
        )
        marked = p.key in self.selected_project_slugs
        lines = [
            f"[b $accent]{p.cwd_guess}[/]",
            f"[$text-muted]agent[/]    [$secondary]{self._agent_label(p.provider)}[/]",
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
        keep_key = self.selected_project.key if self.selected_project else None
        layout = self._compute_projects_layout()
        self._setup_projects_columns(layout)
        table.clear()
        focus_row = 0
        for i, p in enumerate(self.projects):
            path_budget = layout.project_width
            if layout.agent_in_project:
                path_budget = max(2, layout.project_width - 2)
            label = _short_path(p.cwd_guess or p.slug, path_budget)
            if layout.agent_in_project:
                label = f"{_agent_glyph(p.provider)} {label}"
                label = _ellipsis(label, layout.project_width)
            mark = MARK_ON if p.key in self.selected_project_slugs else MARK_OFF
            cells: list[str] = [mark]
            if layout.show_agent_col:
                cells.append(
                    _ellipsis(
                        self._agent_label(p.provider, short=True), layout.agent_width
                    )
                )
            cells.append(label)
            if layout.show_count:
                cells.append(str(min(p.session_count, 999)))
            if layout.show_size:
                cells.append(_ellipsis(_fmt_size(p.total_bytes), 5))
            table.add_row(*cells, key=p.key)
            if keep_key and p.key == keep_key:
                focus_row = i
        if self.projects:
            if self.selected_project:
                match = next(
                    (p for p in self.projects if p.key == self.selected_project.key),
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
            roots = roots_summary(self.providers) or str(self.root)
            self.query_one("#detail", Static).update(
                "[b]No projects found[/b]\n\n"
                f"Config root(s): {roots}\n"
                "Press r to refresh · , settings · ? for help"
            )

    def _update_project_marks(self) -> None:
        table = self.query_one("#projects", DataTable)
        for p in self.projects:
            mark = MARK_ON if p.key in self.selected_project_slugs else MARK_OFF
            try:
                table.update_cell(p.key, "Sel", mark)
            except Exception:
                self._reload_projects_table()
                return
        self._show_detail()
        self._refresh_status()

    def _reload_sessions_table(self, *, focus_session_id: str | None = None) -> None:
        table = self.query_one("#sessions", DataTable)
        keep_id = focus_session_id or (
            self.selected_session.key if self.selected_session else None
        )
        layout = self._compute_sessions_layout()
        self._setup_sessions_columns(layout)
        table.clear()
        self._update_sessions_meta()
        if not self.selected_project:
            self.selected_session = None
            return
        sessions = self._visible_sessions()
        focus_row = 0
        found = False
        for i, s in enumerate(sessions):
            live = live_for_session(self.live, s)
            title = _ellipsis(
                s.display_title.replace("\n", " "), layout.title_width
            )
            mark = MARK_ON if s.key in self.selected_session_ids else MARK_OFF
            cells: list[str] = [mark, title]
            if layout.show_updated:
                if layout.updated_width <= 5:
                    cells.append(s.mtime_dt.strftime("%m-%d"))
                else:
                    cells.append(s.mtime_dt.strftime("%m-%d %H:%M"))
            if layout.show_size:
                cells.append(_ellipsis(_fmt_size(s.size_bytes), 5))
            if layout.show_tags:
                cells.append(_ellipsis(_badges(s, live), layout.tags_width))
            table.add_row(*cells, key=s.key)
            if keep_id and (s.key == keep_id or s.session_id == keep_id):
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
            mark = MARK_ON if s.key in self.selected_session_ids else MARK_OFF
            try:
                table.update_cell(s.key, "Sel", mark)
            except Exception:
                self._reload_sessions_table(
                    focus_session_id=(
                        self.selected_session.key if self.selected_session else None
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
                mark = MARK_ON if s.key in self.selected_session_ids else MARK_OFF
                agent = self._agent_label(s.provider)
                lines.append(
                    f"{mark} [$secondary]{agent}[/]  "
                    f"[$secondary]{s.session_id[:8]}…[/]  "
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
        live = live_for_session(self.live, s)
        provider = self._provider_for_session(s)
        try:
            inv = provider.collect_fragments(
                s.session_id, project_slug=s.project_slug
            )
        except Exception:
            from axism.fragments import FragmentInventory

            inv = FragmentInventory(
                session_id=s.session_id,
                project_slug=s.project_slug,
                fragments=[],
            )
        title = s.display_title.replace("\n", " ")
        lines = [
            f"[b $accent]{title}[/]",
            f"[$text-muted]agent[/]    [$secondary]{self._agent_label(s.provider)}[/]",
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
                f"{'[$text-success]yes[/]' if s.key in self.selected_session_ids else 'no'}"
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
            key = str(event.row_key.value)
            match = next((p for p in self.projects if p.key == key), None)
            if match is None:
                # Legacy numeric keys during transitional reloads.
                try:
                    idx = int(key)
                except ValueError:
                    return
                if 0 <= idx < len(self.projects):
                    match = self.projects[idx]
                else:
                    return
            self.selected_project = match
            self._reload_sessions_table()
            self._refresh_status()
        elif table.id == "sessions" and event.row_key is not None:
            if not self.selected_project:
                return
            key = str(event.row_key.value)
            for s in self._visible_sessions():
                if s.key == key or s.session_id == key:
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
            if p.key in self.selected_project_slugs:
                self.selected_project_slugs.discard(p.key)
            else:
                self.selected_project_slugs.add(p.key)
            self._update_project_marks()
            return
        s = self.selected_session
        if not s:
            self._show_toast("No session focused", success=False, seconds=2.5)
            return
        if s.key in self.selected_session_ids:
            self.selected_session_ids.discard(s.key)
        else:
            self.selected_session_ids.add(s.key)
        self._update_session_marks()

    def action_select_all(self) -> None:
        pane = self._focus_pane()
        if pane == "projects":
            for p in self.projects:
                self.selected_project_slugs.add(p.key)
            self._update_project_marks()
            return
        if pane in {"sessions", "detail"}:
            if not self.selected_project:
                return
            for s in self.selected_project.sessions:
                self.selected_session_ids.add(s.key)
            self._update_session_marks()

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
            self.providers = enabled_providers_from_settings(updated)
            self.provider = (
                provider_by_name(self.providers, updated.active_provider)
                or self.providers[0]
            )
            self.root = self.provider.config_root()
            self._projects_layout = None
            self._sessions_layout = None
            self.selected_project_slugs.clear()
            self.selected_session_ids.clear()
            self.selected_project = None
            self.selected_session = None
            self.action_refresh()
            if self._multi_agent():
                labels = ", ".join(p.label for p in self.providers)
                msg = f"Saved {path.name} · {len(self.providers)} agents ({labels})"
            else:
                msg = f"Saved {path.name} · {self.provider.label} → {self.root}"
            self._show_toast(msg, success=True, seconds=4)

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
                (
                    "[$text-muted]Providers[/] "
                    + ", ".join(f"{p.label}" for p in self.providers)
                ),
                f"[$text-muted]Config[/]    {roots_summary(self.providers)}",
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
            "Stops each session via its agent tool.",
            "Conversation/transcript is kept; use Delete to remove files.",
        ]
        lines = [
            f"[b]{len(targets)} live session(s)[/b] will be stopped.",
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
                ok, msg = self._provider_for_session(s).slash_stop(s.session_id, live)
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
        """Exit axism and hand off to the provider's agent CLI."""
        if self._focus_pane() == "projects":
            self._show_toast("Focus a session to open", success=False, seconds=2.5)
            return
        s = self.selected_session
        if not s:
            self._show_toast("No session focused to open", success=False)
            return
        provider = self._provider_for_session(s)
        if not provider.capabilities.resume:
            self._show_toast(
                f"{provider.label} cannot open sessions", success=False, seconds=4
            )
            return
        live = live_for_session(self.live, s)
        self.exit(
            {
                "provider": provider.name,
                "config_dir": str(provider.config_root()),
                "session": s,
                "open_id": s.session_id,
                "cwd": s.cwd,
                "daemon_short": (live.daemon_short if live else None),
                "live_kind": (live.kind if live else None),
                "live_state": (live.state if live else None),
            }
        )

    def action_rename_session(self) -> None:
        """Rename the focused session with its owning backend's title mechanism."""
        if self._focus_pane() == "projects":
            self._show_toast("Focus a session to rename", success=False, seconds=2.5)
            return
        s = self.selected_session
        if not s:
            self._show_toast("No session focused to rename", success=False)
            return
        provider = self._provider_for_session(s)
        if not provider.capabilities.rename:
            self._show_toast(
                f"{provider.label} cannot rename sessions",
                success=False,
                seconds=4,
            )
            return
        current = s.display_title.replace("\n", " ").strip()

        def _after(new_title: str | None) -> None:
            if not new_title:
                self._show_toast("Rename cancelled", success=False, seconds=2)
                return
            if new_title == current:
                self._show_toast("Title unchanged", success=False, seconds=2)
                return
            try:
                ok, msg = provider.rename(s.session_id, new_title)
            except UnsupportedOperation as exc:
                self._show_toast(str(exc), success=False, seconds=4)
                return
            self.action_refresh()
            self._show_toast(msg, success=ok, seconds=3.5)

        self.push_screen(
            RenameSessionScreen(current, s.session_id[:8]),
            _after,
        )

    def _move_choices(
        self,
        *,
        exclude_slugs: set[str] | None = None,
        provider_name: str | None = None,
        any_provider: bool = False,
    ) -> list[tuple[str, str]]:
        exclude = exclude_slugs or set()
        choices: list[tuple[str, str]] = []
        for p in self.projects:
            if any_provider:
                if (
                    provider_name
                    and p.provider == provider_name
                    and p.slug in exclude
                ):
                    continue
            else:
                if p.slug in exclude:
                    continue
                if provider_name and p.provider and p.provider != provider_name:
                    continue
            if any_provider or self._multi_agent():
                agent = self._agent_label(p.provider)
                label = (
                    f"{agent} · {p.cwd_guess}  ·  {p.session_count} sess · "
                    f"{_fmt_size(p.total_bytes)}"
                )
                dest_id = f"{p.provider or '?'}|{p.cwd_guess}"
            else:
                label = (
                    f"{p.cwd_guess}  ·  {p.session_count} sess · "
                    f"{_fmt_size(p.total_bytes)}"
                )
                dest_id = p.cwd_guess
            choices.append((dest_id, label))
        return choices

    def _parse_move_dest(self, dest: str) -> tuple[str | None, str]:
        """Split ``provider|cwd`` dest ids; bare paths return (None, path)."""
        if "|" not in dest:
            return None, dest
        pid, _, path = dest.partition("|")
        if pid in PROVIDER_LABELS and path:
            return pid, path
        return None, dest

    def _sessions_are_live_blocked(self, sessions: list[SessionMeta]) -> str | None:
        """Return a short reason if any session should be stopped before move."""
        live_n = 0
        for s in sessions:
            live = live_for_session(self.live, s)
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
        """Move focused/marked project(s) or session(s) within the same agent tool."""
        pane = self._focus_pane()

        if pane == "projects":
            targets = self._target_projects()
            if not targets:
                self._show_toast("No project to move", success=False, seconds=2.5)
                return
            pname = self._same_provider_or_toast(targets, action="move")
            if pname is None:
                return
            provider = provider_by_name(self.providers, pname) or self.provider
            if not provider.capabilities.move_projects:
                self._show_toast(
                    f"{provider.label} cannot move projects", success=False, seconds=4
                )
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
                "One project → new path renames the dir; otherwise sessions are merged. "
                "Destinations are limited to the same agent tool."
            )

            def _after_dest(dest_raw: str | None) -> None:
                if not dest_raw:
                    self._show_toast("Move cancelled", success=False, seconds=2)
                    return
                _pid, dest = self._parse_move_dest(dest_raw)

                def _after_confirm(confirmed: bool | None) -> None:
                    if not confirmed:
                        self._show_toast("Move cancelled", success=False, seconds=2)
                        return
                    result = provider.move_projects(targets, dest)
                    self.selected_project_slugs.clear()
                    self.action_refresh()
                    self._show_toast(result.message, success=result.ok, seconds=4)
                    if result.ok and result.memory_needs_agent:
                        self._offer_memory_agent_handoff(result)

                body_lines = [
                    f"[b]{n} project(s)[/b] → [$secondary]{dest}[/]",
                    f"[$text-muted]via {provider.label}[/]",
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
                            "Renames or merges the backend's project directory.",
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
                MoveTargetScreen(
                    title,
                    self._move_choices(exclude_slugs=exclude, provider_name=pname),
                    hint,
                ),
                _after_dest,
            )
            return

        if pane in {"sessions", "detail"}:
            sessions = self._target_sessions_for_move()
            if not sessions:
                self._show_toast("No session to move", success=False, seconds=2.5)
                return
            pname = self._same_provider_or_toast(sessions, action="move")
            if pname is None:
                return
            provider = provider_by_name(self.providers, pname) or self.provider
            blocked = self._sessions_are_live_blocked(sessions)
            if blocked:
                self._show_toast(blocked, success=False, seconds=4)
                return
            exclude = {s.project_slug for s in sessions}
            n = len(sessions)
            cross = self._multi_agent()
            title = (
                f"Move/copy {n} session{'s' if n != 1 else ''}"
                if cross
                else f"Move {n} session{'s' if n != 1 else ''}"
            )
            hint = (
                "Pick a destination project (any enabled agent) or type a path. "
                "Same agent relocates files; another agent copies text turns "
                "(source kept)."
                if cross
                else (
                    "Select destination project, or type an absolute workspace path "
                    "(creates the project dir if needed)."
                )
            )

            def _after_dest(dest_raw: str | None) -> None:
                if not dest_raw:
                    self._show_toast("Move cancelled", success=False, seconds=2)
                    return
                dest_provider_id, dest = self._parse_move_dest(dest_raw)
                if dest_provider_id is None:
                    dest_provider_id = pname
                dest_provider = (
                    provider_by_name(self.providers, dest_provider_id) or provider
                )
                is_cross = dest_provider.name != provider.name

                if not is_cross and not provider.capabilities.move_sessions:
                    self._show_toast(
                        f"{provider.label} cannot move sessions",
                        success=False,
                        seconds=4,
                    )
                    return

                def _after_confirm(confirmed: bool | None) -> None:
                    if not confirmed:
                        self._show_toast("Move cancelled", success=False, seconds=2)
                        return
                    if is_cross:
                        result = transfer_sessions(
                            provider, dest_provider, sessions, dest
                        )
                        msg = result.message
                        ok = result.ok
                    else:
                        result_m = provider.move_sessions(sessions, dest)
                        msg = result_m.message
                        ok = result_m.ok
                    self.selected_session_ids.clear()
                    self.action_refresh()
                    self._show_toast(msg, success=ok, seconds=5)

                body_lines = [
                    f"[b]{n} session(s)[/b] → [$secondary]{dest}[/]",
                    (
                        f"[$text-muted]{provider.label} → {dest_provider.label} "
                        f"(copy, source kept)[/]"
                        if is_cross
                        else f"[$text-muted]via {provider.label} (relocate)[/]"
                    ),
                    "",
                    "[b]Targets[/b]",
                ]
                for s in sessions[:30]:
                    body_lines.append(
                        f"  {s.session_id[:8]}…  {s.display_title[:40]}"
                    )
                if n > 30:
                    body_lines.append(f"  … +{n - 30} more")
                warnings = (
                    [
                        "Cross-agent copy keeps the source session.",
                        "Only user/assistant text turns are transferred; tools are collapsed.",
                        "Cursor destinations may not fully resume in cursor-agent.",
                    ]
                    if is_cross
                    else [
                        "Session files move to the destination project directory.",
                        (
                            "Creates the workspace folder if missing; cwd fields "
                            "and history project entries are updated."
                        ),
                    ]
                )
                self.push_screen(
                    ConfirmDeleteScreen(
                        "\n".join(body_lines),
                        title=("Copy to other agent" if is_cross else title),
                        warnings=warnings,
                        confirm_verb=("Copy" if is_cross else "Move"),
                    ),
                    _after_confirm,
                )

            self.push_screen(
                MoveTargetScreen(
                    title,
                    self._move_choices(
                        exclude_slugs=exclude,
                        provider_name=pname,
                        any_provider=cross,
                    ),
                    hint,
                ),
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
        pname = self._same_provider_or_toast(projects, action="delete")
        if pname is None:
            return
        provider = provider_by_name(self.providers, pname) or self.provider
        if not provider.capabilities.delete_projects:
            self._show_toast(
                f"{provider.label} cannot delete projects", success=False, seconds=4
            )
            return
        packed: list[tuple[str, str, object, list[str]]] = []
        for p in projects:
            plan = provider.plan_project_delete(
                p.slug, force=True, keep_memory=False
            )
            preview = provider.execute_project_delete(
                plan, dry_run=True, force=True, stop_live=True
            )
            packed.append((p.slug, p.cwd_guess, plan, preview))
        body, warnings = _format_project_delete_confirm(packed)
        keys = [p.key for p in projects]
        slugs = [p.slug for p in projects]

        def _after(confirmed: bool | None) -> None:
            if not confirmed:
                self._set_status("Project delete cancelled")
                self._show_toast("Project delete cancelled", success=False, seconds=2.5)
                return
            all_actions: list[str] = []
            for slug in slugs:
                plan_exec = provider.plan_project_delete(
                    slug, force=True, keep_memory=False
                )
                actions = provider.execute_project_delete(
                    plan_exec,
                    dry_run=False,
                    force=True,
                    stop_live=True,
                )
                all_actions.extend(actions)
            for key in keys:
                self.selected_project_slugs.discard(key)
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
                    f"Deletes via {provider.label}.",
                    "Deletes the backend's project directory and linked session data.",
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
        pname = self._same_provider_or_toast(targets, action="delete")
        if pname is None:
            return
        provider = provider_by_name(self.providers, pname) or self.provider
        if not provider.capabilities.delete_sessions:
            self._show_toast(
                f"{provider.label} cannot delete sessions", success=False, seconds=4
            )
            return
        plans = [
            provider.plan_delete(
                s.session_id,
                project_slug=s.project_slug,
                force=True,
            )
            for s in targets
        ]
        preview_actions = provider.execute_deletes(
            plans, dry_run=True, force=True, stop_live=True
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
                provider.plan_delete(
                    s.session_id,
                    project_slug=s.project_slug,
                    force=True,
                )
                for s in targets
            ]
            actions = provider.execute_deletes(
                plans_exec, dry_run=False, force=True, stop_live=True
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
                warnings=warnings + [f"Deletes via {provider.label}."],
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
    session = result.get("session")
    if not isinstance(session, SessionMeta):
        return
    live = None
    if result.get("live_kind"):
        live = LiveSession(
            session_id=session.session_id,
            kind=str(result["live_kind"]),
            state=result.get("live_state"),
            daemon_short=result.get("daemon_short"),
        )
    provider = get_provider(
        str(result.get("provider") or DEFAULT_PROVIDER),
        config_dir=result.get("config_dir"),
    )
    try:
        provider.resume(session, live)
    except (FileNotFoundError, UnsupportedOperation) as exc:
        print(f"axism: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except OSError as exc:
        print(f"axism: failed to open session: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
