"""Textual TUI for aXism — Agent Interactive Session Manager."""

from __future__ import annotations

import contextlib
import inspect
import platform
import sys
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.events import Resize
from textual.theme import Theme
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import DataTable, Input, Static
from textual.widgets._header import HeaderClock, HeaderClockSpace

from axism import __commit__, version_string
from axism.actions import actions_failed, first_failure
from axism.details import SessionDetails
from axism.discover import ProjectInfo, SessionMeta
from axism.live import LiveSession
from axism.move import MoveResult
from axism.providers import (
    PROVIDER_LABELS,
    SessionProvider,
    UnsupportedOperation,
    default_provider_id,
    detect_undecided_providers,
    enabled_providers_from_settings,
    federate_discover,
    federate_merge_live,
    get_provider,
    live_for_session,
    provider_by_name,
    roots_summary,
)
from axism.providers.transfer import transfer_sessions
from axism.settings import (
    DEFAULT_PROVIDER,
    Settings,
    load_settings,
    save_settings,
    settings_path,
)
from axism.tui.details_view import fmt_size, project_details_text, session_details_text
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
from axism.tui.help import HelpScreen, RegistryCommands
from axism.tui.keys import REGISTRY, bindings_for
from axism.tui.reader import ReaderScreen
from axism.tui.settings_screen import UNREADABLE_REFUSAL, SettingsScreen

# Registry scopes whose keys are bound on the main screen.
MAIN_SCREEN_SCOPES = ("main", "projects", "sessions", "details", "filter")
# Registry actions that still act while an Input has focus (everything else,
# printable or not, belongs to the Input: Inputs win).
INPUT_ACTIONS = frozenset({"back", "next_pane", "previous_pane", "command_palette"})
REGISTRY_IDS = frozenset(action.id for action in REGISTRY)

# Mark glyphs (a "[x]" box would read as a style tag in markup).
MARK_ON = "☑"
MARK_OFF = "☐"

# Styles of aXism's own labels; session text next to them stays plain.
MUTED = "$text-muted"
SECONDARY = "$secondary"
HEADING = "bold $accent"
TOTAL = "bold $primary"


def _cell(text: str) -> Text:
    """A table cell shown exactly as written (a plain string would be markup)."""
    return Text(text, no_wrap=True, end="")


def _lines(lines: list[Content]) -> Content:
    return Content("\n").join(lines)

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

    def column_widths(self) -> list[int]:
        """Content widths of the visible columns, in display order."""
        widths = [1]
        if self.show_agent_col:
            widths.append(self.agent_width)
        widths.append(self.project_width)
        if self.show_count:
            widths.append(3)
        if self.show_size:
            widths.append(5)
        return widths


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

    def column_widths(self) -> list[int]:
        """Content widths of the visible columns, in display order."""
        widths = [1, self.title_width]
        if self.show_updated:
            widths.append(self.updated_width)
        if self.show_size:
            widths.append(5)
        if self.show_tags:
            widths.append(self.tags_width)
        return widths


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
        "opencode": "O",
    }.get(provider or "", "?")


# Every DataTable column adds this much cell padding on top of its content width.
CELL_PADDING = 2
# Terminal width below which the detail pane is hidden.
NARROW_COLUMNS = 70


# The primary column (project name / session title) keeps at least this many
# cells before an optional column is added. Layouts that could still grow an
# optional column cap the primary column at this size, so it never gets
# narrower as the budget grows.
MIN_PROJECT_NAME = 12
MIN_SESSION_TITLE = 20


def plan_projects_layout(budget: int, *, multi_agent: bool) -> ProjectsLayout:
    """Choose projects columns that fit in ``budget`` cells (no h-scroll).

    The budget covers content widths plus ``CELL_PADDING`` per visible column.
    """
    cap = MIN_PROJECT_NAME

    def flex(fixed: list[int], n_cols: int) -> int:
        return budget - sum(fixed) - CELL_PADDING * n_cols

    # Sel + Agent + Project + # + Size  (final layout: name takes the rest)
    if multi_agent and flex([1, 6, 3, 5], 5) >= cap:
        return ProjectsLayout(
            density="full",
            show_agent_col=True,
            show_count=True,
            show_size=True,
            agent_in_project=False,
            project_width=flex([1, 6, 3, 5], 5),
            agent_width=6,
        )
    # Sel + Project + # + Size  (final layout without an agent column)
    if not multi_agent and flex([1, 3, 5], 4) >= cap:
        return ProjectsLayout(
            density="full",
            show_agent_col=False,
            show_count=True,
            show_size=True,
            agent_in_project=False,
            project_width=flex([1, 3, 5], 4),
        )
    # Sel + Agent + Project + #
    if multi_agent and flex([1, 6, 3], 4) >= cap:
        return ProjectsLayout(
            density="cozy",
            show_agent_col=True,
            show_count=True,
            show_size=False,
            agent_in_project=False,
            project_width=cap,
            agent_width=6,
        )
    # Sel + Project (with an "H " agent prefix when federated) + #
    if flex([1, 3], 3) >= cap:
        return ProjectsLayout(
            density="cozy",
            show_agent_col=False,
            show_count=True,
            show_size=False,
            agent_in_project=multi_agent,
            project_width=cap,
        )
    # Ultra-tight: mark + project only (agent glyph inlined when federated).
    return ProjectsLayout(
        density="tight",
        show_agent_col=False,
        show_count=False,
        show_size=False,
        agent_in_project=multi_agent,
        project_width=min(cap, max(4, flex([1], 2))),
    )


def plan_sessions_layout(budget: int) -> SessionsLayout:
    """Choose sessions columns that fit in ``budget`` cells (no h-scroll).

    The budget covers content widths plus ``CELL_PADDING`` per visible column.
    """
    cap = MIN_SESSION_TITLE

    def flex(fixed: list[int], n_cols: int) -> int:
        return budget - sum(fixed) - CELL_PADDING * n_cols

    # Sel + Title + Updated + Size + Tags  (final layout)
    if flex([1, 11, 5, 10], 5) >= cap:
        return SessionsLayout(
            density="full",
            show_updated=True,
            show_size=True,
            show_tags=True,
            title_width=flex([1, 11, 5, 10], 5),
            updated_width=11,
            tags_width=10,
        )
    # Sel + Title + Updated + Size
    if flex([1, 11, 5], 4) >= cap:
        return SessionsLayout(
            density="cozy",
            show_updated=True,
            show_size=True,
            show_tags=False,
            title_width=cap,
            updated_width=11,
        )
    # Compact date (MM-DD only), before the full date and Size are added
    if flex([1, 5], 3) >= cap:
        return SessionsLayout(
            density="cozy",
            show_updated=True,
            show_size=False,
            show_tags=False,
            title_width=cap,
            updated_width=5,
        )
    return SessionsLayout(
        density="tight",
        show_updated=False,
        show_size=False,
        show_tags=False,
        title_width=min(cap, max(5, flex([1], 2))),
    )


class AxisHeader(Widget):
    """Top bar: brand + version on the left, optional clock on the right."""

    def __init__(self, *, show_clock: bool = True) -> None:
        super().__init__()
        self._show_clock = show_clock

    def compose(self) -> ComposeResult:
        with Horizontal(id="header-left"):
            yield Static(self._label_for_width(120), id="header-brand", markup=False)
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


_fmt_size = fmt_size


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


def _summarize_session_delete(
    targets: list[SessionMeta], actions: list[str]
) -> tuple[bool, str]:
    failed = actions_failed(actions)
    if failed:
        err = first_failure(actions)
        return False, f"Delete failed: {err}"
    if len(targets) == 1:
        s = targets[0]
        label = s.display_title.replace("\n", " ")[:48] or s.session_id[:8]
        return True, f"Deleted session: {label}"
    return True, f"Deleted {len(targets)} sessions"


def _summarize_project_delete(slugs: list[str], actions: list[str]) -> tuple[bool, str]:
    failed = actions_failed(actions)
    if failed:
        err = first_failure(actions)
        return False, f"Project delete failed: {err}"
    if len(slugs) == 1:
        return True, f"Deleted project: {slugs[0]}"
    return True, f"Deleted {len(slugs)} projects"


def _live_needs_stop(
    live: LiveSession | None, provider: SessionProvider | None = None
) -> bool:
    """Whether a delete stops ``live`` first (a process, or what the backend can stop)."""
    if live is None:
        return False
    return live.is_killable or (provider is not None and provider.can_stop(live))


def _format_session_delete_confirm(
    targets: list[SessionMeta],
    plans: list,
    preview: list[str],
    provider: SessionProvider | None = None,
) -> tuple[str, list[str]]:
    """Return (body, warning lines) for the delete confirm dialog."""
    warnings: list[str] = []
    for s, plan in zip(targets, plans, strict=False):
        live = plan.live
        if not _live_needs_stop(live, provider):
            continue
        pid = f" pid={live.pid}" if live.pid is not None else ""
        warnings.append(
            f"{s.session_id[:8]}…  {live.kind}/{live.state}{pid} — will stop/kill first"
        )

    lines: list[Content] = [
        Content.assemble(
            (f"{len(targets)} session(s)", "bold"),
            " will be permanently removed from disk.",
        ),
        Content("Protected items (credentials, settings, skills, memory) are kept."),
        Content(""),
        Content.styled("Targets", "bold"),
    ]
    total = 0
    for s, plan in zip(targets, plans, strict=False):
        total += plan.inventory.total_bytes
        title = s.display_title.replace("\n", " ")[:44]
        nfrag = len(plan.inventory.deletable)
        live = plan.live
        live_bit = ""
        if _live_needs_stop(live, provider):
            live_bit = f"  ·  live {live.kind}/{live.state}"
            if live.pid is not None:
                live_bit += f" pid={live.pid}"
        stored = plan.inventory.stored_rows_note()
        size_bit = (
            stored
            if stored is not None and not nfrag
            else _fmt_size(plan.inventory.total_bytes)
        )
        lines.append(
            Content(
                f"  {s.session_id[:8]}…  {size_bit}"
                f"  {nfrag} fragments{live_bit}"
            )
        )
        lines.append(Content(f"    {title}"))
    lines.append(Content(""))
    lines.append(
        Content.assemble(
            ("Total", "bold"), f"  {_fmt_size(total)} across {len(targets)} session(s)"
        )
    )
    lines.append(Content(""))
    lines.append(Content.styled("Will", "bold"))
    lines.extend(Content(f"  {a}") for a in preview[:40])
    if len(preview) > 40:
        lines.append(Content(f"  … +{len(preview) - 40} more"))
    return _lines(lines), warnings


def _memory_line(n_projects: int, keep: bool) -> Content:
    """One sentence saying exactly what happens to each project's ``memory/``."""
    what = "its memory/ folder" if n_projects == 1 else "their memory/ folders"
    if keep:
        return Content.assemble(
            f"Project memory is KEPT: {what} stays on disk, "
            "everything else in the project is deleted.  ",
            ("m", "bold"),
            " to delete it too.",
        )
    return Content.assemble(
        f"Project memory is DELETED: {what} is removed with the project.  ",
        ("m", "bold"),
        " to keep it.",
    )


def _format_project_delete_confirm(
    projects: list[tuple[str, str, object, list[str]]],
    provider: SessionProvider | None = None,
) -> tuple[str, list[str]]:
    """Format confirm body for one or more projects.

    ``projects`` items are ``(slug, cwd, plan, preview_actions)``.
    """
    warnings: list[str] = []
    lines: list[Content] = [
        Content.assemble(
            (f"{len(projects)} project(s)", "bold"), " will be permanently removed."
        ),
        Content("Protected items (credentials, settings, skills) are kept."),
        Content(""),
    ]
    for slug, cwd, plan, preview in projects:
        live_n = 0
        for sp in getattr(plan, "session_plans", []) or []:
            if _live_needs_stop(sp.live, provider):
                live_n += 1
                live = sp.live
                pid = f" pid={live.pid}" if live and live.pid is not None else ""
                warnings.append(
                    f"{slug}: {sp.session_id[:8]}…  "
                    f"{live.kind}/{live.state}{pid} — will stop/kill"
                )
        n_sess = len(getattr(plan, "session_plans", []) or [])
        lines.append(Content.assemble(("Project", "bold"), f"  {slug}"))
        lines.append(Content(f"cwd: {cwd or '-'}"))
        lines.append(Content(f"Sessions inside: {n_sess}"))
        if live_n:
            warnings.insert(
                0,
                f"{slug}: {live_n} live session(s) will be stopped/killed first",
            )
        lines.append(Content(""))
        lines.append(Content.styled("Will", "bold"))
        lines.extend(Content(f"  {a}") for a in preview[:40])
        if len(preview) > 40:
            lines.append(Content(f"  … +{len(preview) - 40} more"))
        lines.append(Content(""))
    while lines and not lines[-1].plain:
        lines.pop()
    return _lines(lines), warnings


def _format_stop_confirm(targets: list[tuple[SessionMeta, LiveSession]]) -> Content:
    """The stop dialog's body: how many sessions stop, then one line each."""
    lines: list[Content] = [
        Content.assemble((f"{len(targets)} live session(s)", "bold"), " will be stopped."),
        Content(""),
        Content.styled("Targets", "bold"),
    ]
    for s, live in targets:
        title = s.display_title.replace("\n", " ")[:44]
        short = live.daemon_short or s.session_id[:8]
        lines.append(Content(f"  {short}  {live.kind}/{live.state}  {title}"))
    return _lines(lines)


class DetailPane(VerticalScroll):
    """Right pane: a focusable scroll view around the details text."""

    def __init__(self, content: Content | None = None, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._body = Static(content or Content(""), id="detail-body")
        self.subject: str | None = None

    def compose(self) -> ComposeResult:
        yield self._body

    def update(self, content: Content, *, subject: str | None = None) -> None:
        """Replace the details text.

        Only ``Content`` is taken: session titles, folders and agent text go
        in as plain parts, so nothing in them is ever read as markup.

        ``subject`` names what is shown (a session or project key). The pane
        goes back to the top only when the subject changes, so re-rendering
        the same session (refresh, marks, a finished load) keeps the place.
        """
        if not isinstance(content, Content):
            raise TypeError("DetailPane.update takes Content, not a markup string")
        self._body.update(content)
        if subject is None or subject != self.subject:
            self.scroll_home(animate=False)
        self.subject = subject


class SessionManagerApp(App[None]):
    """Main TUI: projects | sessions | detail."""

    # Ctrl+P lists every registry action by name (see tui/help.py); Textual's
    # own system commands are left out so the palette matches the real keys.
    ENABLE_COMMAND_PALETTE = True
    COMMANDS = {RegistryCommands}

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
        height: 1fr;
        padding: 0 1;
    }
    #detail-body {
        width: 100%;
        height: auto;
    }
    /* Below 70 columns: drop the detail pane so projects + sessions both fit. */
    .-narrow #detail {
        display: none;
    }
    .-narrow #projects {
        min-width: 10;
    }
    .-narrow #sessions-pane {
        min-width: 16;
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
    """

    # Every main-screen key comes from the key registry (tui/keys.py).
    BINDINGS = bindings_for(*MAIN_SCREEN_SCOPES)

    def __init__(
        self,
        *,
        run_provider: str | None = None,
        run_config_dir: str | None = None,
    ) -> None:
        super().__init__()
        # ``axism tui --provider`` / ``--config-dir``: this run only, never saved.
        self._run_provider = run_provider
        self._run_config_dir = run_config_dir
        self._axism_settings = load_settings()
        detection = detect_undecided_providers(self._axism_settings)
        self._detection_message = detection.message() if detection.changed else ""
        # The agent ``--config-dir`` applies to, fixed for the run: a later
        # change of the CLI default in Settings must not move the folder.
        self._run_config_agent = run_provider or default_provider_id(self._axism_settings)
        self.providers = self._providers_for(self._axism_settings)
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
        # Details per (agent, session id, mtime), computed lazily off the UI
        # thread for the session the pane shows.
        self._details_cache: OrderedDict[tuple[str, str, float], SessionDetails] = (
            OrderedDict()
        )
        self._details_pending: set[tuple[str, str, float]] = set()
        self._details_timer: Timer | None = None
        # The single session the details pane shows (the reader opens it).
        self._detail_session: SessionMeta | None = None

    def _providers_for(self, settings: Settings) -> list[SessionProvider]:
        """Agents in the app's inventory: the switched-on ones, narrowed or
        pointed at another folder by this run's ``--provider`` / ``--config-dir``.

        ``--config-dir`` stays on the agent chosen at start (``--provider`` or
        the CLI default then), whatever Settings later saves."""
        if self._run_provider:
            prefs = settings.providers.get(self._run_provider)
            folder = self._run_config_dir or (prefs.config_dir if prefs else None)
            return [get_provider(self._run_provider, config_dir=folder)]
        providers = enabled_providers_from_settings(settings)
        if not self._run_config_dir:
            return providers
        name = self._run_config_agent
        override = get_provider(name, config_dir=self._run_config_dir)
        if any(p.name == name for p in providers):
            return [override if p.name == name else p for p in providers]
        return [override, *providers]

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
            yield DetailPane(Content("Select a session  ·  ? help"), id="detail")
        yield Static("", id="status")
        yield Static("", id="toast", markup=False)
        yield ContextFooter(id="footer")

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
        detail = self.query_one("#detail", DetailPane)
        detail.border_title = "Detail"
        self.theme_changed_signal.subscribe(self, self._on_theme_changed)
        self._sync_pane_focus_classes()
        self._sync_narrow_class()
        self.action_refresh()
        if self._detection_message:
            self._show_toast(self._detection_message, success=True, seconds=8.0)
        # The first pass runs before the tables have a real size; plan again.
        self.call_after_refresh(self._refit_tables)

    def _sync_narrow_class(self) -> None:
        self.set_class(self.size.width < NARROW_COLUMNS, "-narrow")

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

    @staticmethod
    def _table_budget(table: DataTable, default: int) -> int:
        """Cells available to columns: the table width minus the scrollbar gutter.

        The gutter is always reserved so the plan does not flip when rows start
        to scroll.
        """
        if not table.size.width:
            return default
        return table.size.width - table.styles.scrollbar_size_vertical

    def _compute_projects_layout(self) -> ProjectsLayout:
        table = self.query_one("#projects", DataTable)
        return plan_projects_layout(
            self._table_budget(table, 24), multi_agent=self._multi_agent()
        )

    def _compute_sessions_layout(self) -> SessionsLayout:
        table = self.query_one("#sessions", DataTable)
        return plan_sessions_layout(self._table_budget(table, 40))

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
        self._sync_narrow_class()
        self._refit_tables()
        # Pane widths settle after the class change has been laid out.
        self.call_after_refresh(self._refit_tables)

    def _refit_tables(self) -> None:
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
        """Re-render detail so its theme variables pick up the new theme."""
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
        """Decide whether a key reaches its action at all.

        Registry keys always fire on the main screen: an action that cannot
        run says why (see ``action_available`` and ``_guard``). Dialogs keep
        their own keys, and a focused Input keeps its keys (Inputs win).
        """
        if len(self.screen_stack) > 1:
            return False
        if (
            isinstance(self.focused, Input)
            and action in REGISTRY_IDS
            and action not in INPUT_ACTIONS
        ):
            # Inputs win: the key goes to the text field (cursor, Home/End, Del, …).
            return False
        return True

    def action_available(self, action_id: str) -> tuple[bool, str]:
        """Whether ``action_id`` can run now, and if not, a short reason why."""
        pane = self._focus_pane()
        in_filter = (
            isinstance(self.focused, Input) and self.focused.id == "sessions-filter"
        )
        on_sessions = pane in {"sessions", "detail"}
        if action_id == "toggle_select":
            if pane not in {"projects", "sessions"} or in_filter:
                return False, "Mark works in the Projects and Sessions panes"
            if pane == "projects" and not self.selected_project:
                return False, "Select a project first"
            if pane == "sessions" and not self.selected_session:
                return False, "Select a session first"
        elif action_id == "select_all":
            if pane == "projects":
                if not self.projects:
                    return False, "No projects to mark"
            elif on_sessions:
                if not self.selected_project:
                    return False, "Select a project first"
            else:
                return False, "Mark all works in the Projects and Sessions panes"
        elif action_id == "clear_select":
            if not (self.selected_project_slugs or self.selected_session_ids):
                return False, "Nothing marked"
        elif action_id == "cycle_session_sort":
            if not on_sessions:
                return False, "Sort works in the Sessions pane"
            if not self.selected_project:
                return False, "Select a project first"
        elif action_id in {"resume_session", "rename_session"}:
            verb = "Open" if action_id == "resume_session" else "Rename"
            if not on_sessions:
                return False, f"{verb} works in the Sessions pane"
            session = self.selected_session
            if not session:
                return False, "Select a session first"
            provider = self._provider_for_session(session)
            if action_id == "resume_session" and not provider.capabilities.resume:
                return False, f"{provider.label} cannot open sessions"
            if action_id == "rename_session" and not provider.capabilities.rename:
                return False, f"{provider.label} cannot rename sessions"
        elif action_id == "stop_sessions":
            if not on_sessions:
                return False, "Stop works in the Sessions pane"
            if not self._stoppable_among(self._target_sessions_for_stop()):
                return False, "Nothing to stop"
        elif action_id in {"move_items", "delete_items"}:
            verb = "move" if action_id == "move_items" else "delete"
            if pane == "projects":
                targets: list[object] = list(self._target_projects())
            elif on_sessions:
                targets = list(
                    self._target_sessions_for_move()
                    if action_id == "move_items"
                    else self._target_sessions_for_delete()
                )
            else:
                return False, f"{verb.capitalize()} works in the Projects and Sessions panes"
            if not targets:
                return False, f"Nothing marked to {verb}"
        elif action_id == "enter_pane":
            if pane == "sessions" and "detail" not in self._pane_order():
                return False, "Details are hidden at this width"
            if pane == "detail" and self._reader_session() is None:
                if self.query_one("#detail", DetailPane).subject == "sessions:marked":
                    return False, "Clear marks to read a single session"
                return False, "Select a session first"
        elif action_id == "back":
            if not (in_filter or pane == "detail"):
                return False, "Esc goes back from the details pane or the filter"
        return True, ""

    def _guard(self, action_id: str, *, quiet: bool = False) -> bool:
        """True when ``action_id`` may run; otherwise show the reason and say False.

        ``quiet`` skips the message, for keys such as Esc that are pressed
        out of habit where there is nothing to back out of.
        """
        enabled, reason = self.action_available(action_id)
        if not enabled and not quiet:
            self._show_toast(reason, success=False, seconds=2.5)
        return enabled

    def _footer_scope(self) -> str:
        """Registry scope for the footer: the focused pane, or filter mode."""
        focused = self.focused
        if isinstance(focused, Input) and focused.id == "sessions-filter":
            return "filter"
        return {"projects": "projects", "sessions": "sessions", "detail": "details"}.get(
            self._focus_pane(), "main"
        )

    def _refresh_footer(self) -> None:
        try:
            footer = self.query_one(ContextFooter)
        except Exception:
            return
        footer.set_context(
            self._footer_scope(), lambda action_id: self.action_available(action_id)[0]
        )

    # ---- pane focus ---------------------------------------------------------

    def _pane_order(self) -> list[str]:
        """Focusable main panes, left to right (details hidden when narrow)."""
        panes = ["projects", "sessions"]
        if self.query_one("#detail").display:
            panes.append("detail")
        return panes

    def _focus_pane_id(self, pane: str) -> None:
        self.query_one(f"#{pane}").focus()

    def _step_pane(self, step: int, *, wrap: bool) -> None:
        order = self._pane_order()
        current = self._focus_pane()
        if current not in order:
            self._focus_pane_id(order[0])
            return
        index = order.index(current) + step
        if wrap:
            index %= len(order)
        elif not 0 <= index < len(order):
            return
        self._focus_pane_id(order[index])

    def action_pane_right(self) -> None:
        if not self._guard("pane_right"):
            return
        self._step_pane(1, wrap=False)

    def action_pane_left(self) -> None:
        if not self._guard("pane_left"):
            return
        self._step_pane(-1, wrap=False)

    def action_next_pane(self) -> None:
        if not self._guard("next_pane"):
            return
        self._step_pane(1, wrap=True)

    def action_previous_pane(self) -> None:
        if not self._guard("previous_pane"):
            return
        self._step_pane(-1, wrap=True)

    def action_enter_pane(self) -> None:
        """Go one level in: projects → sessions → details → reader."""
        if not self._guard("enter_pane"):
            return
        pane = self._focus_pane()
        if pane == "projects":
            self._focus_pane_id("sessions")
        elif pane == "sessions":
            self._focus_pane_id("detail")
        elif pane == "detail":
            self._open_reader()

    def _reader_session(self) -> SessionMeta | None:
        """The session the reader opens: the one the details pane shows (none
        while it shows the marked list or no session)."""
        return self._detail_session

    def _open_reader(self) -> None:
        session = self._reader_session()
        if session is None:
            return
        provider = self._provider_for_session(session)
        self.push_screen(
            ReaderScreen(
                session, provider, self._agent_label(session.provider or provider.name)
            )
        )

    def action_back(self) -> None:
        """Esc: details → sessions list; filter → clear it and leave."""
        if not self._guard("back", quiet=True):
            return
        focused = self.focused
        if isinstance(focused, Input) and focused.id == "sessions-filter":
            if focused.value or self._session_filter:
                focused.value = ""
                self._session_filter = ""
                self._reload_sessions_table()
            self._focus_pane_id("sessions")
            return
        self._focus_pane_id("sessions")

    # ---- movement inside the focused list or scroll view ---------------------

    def _scroll_target(self) -> Widget | None:
        """The focused list or scroll view that the movement keys act on."""
        focused = self.focused
        if isinstance(focused, DataTable) or (
            focused is not None and focused.id == "detail"
        ):
            return focused
        return None

    @staticmethod
    def _table_page_rows(table: DataTable) -> int:
        header = table.header_height if table.show_header else 0
        return max(1, table.scrollable_content_region.height - header)

    def _move_table_cursor(self, table: DataTable, delta: int) -> None:
        if not table.row_count:
            return
        row = min(max(table.cursor_row + delta, 0), table.row_count - 1)
        table.move_cursor(row=row, animate=False)

    def action_cursor_down(self) -> None:
        if not self._guard("cursor_down"):
            return
        target = self._scroll_target()
        if isinstance(target, DataTable):
            target.action_cursor_down()
        elif target is not None:
            target.scroll_down(animate=False)

    def action_cursor_up(self) -> None:
        if not self._guard("cursor_up"):
            return
        target = self._scroll_target()
        if isinstance(target, DataTable):
            target.action_cursor_up()
        elif target is not None:
            target.scroll_up(animate=False)

    def action_cursor_first(self) -> None:
        if not self._guard("cursor_first"):
            return
        target = self._scroll_target()
        if isinstance(target, DataTable):
            if target.row_count:
                target.move_cursor(row=0, animate=False)
        elif target is not None:
            target.scroll_home(animate=False)

    def action_cursor_last(self) -> None:
        if not self._guard("cursor_last"):
            return
        target = self._scroll_target()
        if isinstance(target, DataTable):
            if target.row_count:
                target.move_cursor(row=target.row_count - 1, animate=False)
        elif target is not None:
            target.scroll_end(animate=False)

    def action_half_page_down(self) -> None:
        if not self._guard("half_page_down"):
            return
        target = self._scroll_target()
        if isinstance(target, DataTable):
            self._move_table_cursor(target, max(1, self._table_page_rows(target) // 2))
        elif target is not None:
            target.scroll_relative(
                y=max(1, target.scrollable_content_region.height // 2), animate=False
            )

    def action_half_page_up(self) -> None:
        if not self._guard("half_page_up"):
            return
        target = self._scroll_target()
        if isinstance(target, DataTable):
            self._move_table_cursor(target, -max(1, self._table_page_rows(target) // 2))
        elif target is not None:
            target.scroll_relative(
                y=-max(1, target.scrollable_content_region.height // 2), animate=False
            )

    def action_page_down(self) -> None:
        if not self._guard("page_down"):
            return
        target = self._scroll_target()
        if isinstance(target, DataTable):
            target.action_page_down()
        elif target is not None:
            target.scroll_page_down(animate=False)

    def action_page_up(self) -> None:
        if not self._guard("page_up"):
            return
        target = self._scroll_target()
        if isinstance(target, DataTable):
            target.action_page_up()
        elif target is not None:
            target.scroll_page_up(animate=False)

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
            meta.update(Content.styled("/ filter · . sort", MUTED))
            return
        total = len(self.selected_project.sessions)
        shown = len(self._visible_sessions())
        filt = self._session_filter.strip()
        filt_bit = f'filter="{filt}"  ' if filt else ""
        count = f"{shown}/{total}" if filt or shown != total else str(total)
        meta.update(
            Content.styled(
                f"{count} · {filt_bit}sort: {self._session_sort_label()}  "
                "(/ filter · . sort)",
                MUTED,
            )
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
        if not self._guard("refresh"):
            return
        self.projects = federate_discover(self.providers)
        self.live = federate_merge_live(self.providers, use_cli=True)
        # Failed loads retry; good ones stay cached.
        for key in [k for k, d in self._details_cache.items() if d.note]:
            del self._details_cache[key]
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
            agent_bit = Content.styled(f"{len(self.providers)} agents", MUTED)
            root_bit = Content.styled(roots_summary(self.providers), MUTED)
        else:
            agent_bit = Content.styled(self.provider.label, MUTED)
            root_bit = Content.styled(str(self.root), MUTED)
        bits = [
            Content.styled(pane_label, HEADING),
            Content(
                f"mark {len(self.selected_project_slugs)}p/{len(self.selected_session_ids)}s"
            ),
            agent_bit,
            root_bit,
        ]
        if extra:
            bits.append(Content(extra))
        self.query_one("#status", Static).update(Content("  ·  ").join(bits))
        self.refresh_bindings()
        self._refresh_footer()

    def on_descendant_focus(self, _event: object) -> None:
        self._sync_pane_focus_classes()
        self._refresh_status()
        self._show_detail()

    def _set_status(self, text: str) -> None:
        """Update status with pane/selection prefix, then optional message."""
        self._refresh_status(extra=text)

    def _show_toast(self, message: str, *, success: bool, seconds: float = 4.0) -> None:
        """One line of plain text (titles, paths and agent messages as written)."""
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
        detail = self.query_one("#detail", DetailPane)
        if self.selected_project_slugs and len(self.selected_project_slugs) > 1:
            targets = [p for p in self.projects if p.key in self.selected_project_slugs]
            if not targets:
                targets = [self.selected_project] if self.selected_project else []
            lines = [Content.styled(f"{len(targets)} projects selected", HEADING), Content("")]
            total_sess = 0
            total_bytes = 0
            for p in targets:
                total_sess += p.session_count
                total_bytes += p.total_bytes
                mark = MARK_ON if p.key in self.selected_project_slugs else MARK_OFF
                agent = self._agent_label(p.provider)
                lines.append(
                    Content.assemble(
                        f"{mark} ",
                        (agent, SECONDARY),
                        "  ",
                        (p.cwd_guess[:36], SECONDARY),
                        "  ",
                        (f"{p.session_count} sess · {_fmt_size(p.total_bytes)}", MUTED),
                    )
                )
            lines.append(Content(""))
            lines.append(
                Content.assemble(
                    ("Total", TOTAL), f"  {total_sess} sessions · {_fmt_size(total_bytes)}"
                )
            )
            lines.append(Content(""))
            lines.append(
                Content.styled(
                    "m/M Move · d/D Delete project · Space toggle mark · Tab to sessions",
                    MUTED,
                )
            )
            detail.update(_lines(lines), subject="projects:marked")
            return

        p = self.selected_project
        if not p:
            detail.update(
                _lines(
                    [
                        Content.styled("Select a project", MUTED),
                        Content(""),
                        Content.styled("Tab panes · ? help · , settings", MUTED),
                    ]
                )
            )
            return

        running = 0
        for s in p.sessions:
            lv = live_for_session(self.live, s)
            if lv is not None and (
                lv.is_live
                or lv.state == "running"
                or self._provider_for_session(s).can_stop(lv)
            ):
                running += 1
        folder = p.cwd_guess or p.slug
        detail.update(
            project_details_text(
                name=Path(folder.rstrip("/")).name or folder,
                folder=folder,
                agent=self._agent_label(p.provider),
                sessions=p.session_count,
                running=running,
                size_bytes=p.total_bytes,
                updated=max((s.mtime for s in p.sessions), default=None),
                now=datetime.now().astimezone(),
                home=str(Path.home()),
                hint="m/M Move · d/D Delete project · Tab/→ sessions",
            ),
            subject=f"project:{p.key}",
        )

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
            table.add_row(*map(_cell, cells), key=p.key)
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
            self.selected_session = None
            self.query_one("#sessions", DataTable).clear()
            self._update_sessions_meta()
            roots = roots_summary(self.providers) or str(self.root)
            self.query_one("#detail", DetailPane).update(
                _lines(
                    [
                        Content.styled("No projects found", "bold"),
                        Content(""),
                        Content(f"Config root(s): {roots}"),
                        Content("Press r to refresh · , settings · ? for help"),
                    ]
                )
            )

    def _update_project_marks(self) -> None:
        table = self.query_one("#projects", DataTable)
        for p in self.projects:
            mark = MARK_ON if p.key in self.selected_project_slugs else MARK_OFF
            try:
                table.update_cell(p.key, "Sel", _cell(mark))
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
            table.add_row(*map(_cell, cells), key=s.key)
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
                table.update_cell(s.key, "Sel", _cell(mark))
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
        self._detail_session = None
        if self._focus_pane() == "projects":
            self._show_project_detail()
            return

        detail = self.query_one("#detail", DetailPane)
        # Prefer explicit marks for multi-select view; else focused session.
        if self.selected_session_ids:
            targets = self._sessions_by_ids(self.selected_session_ids)
        elif self.selected_session:
            targets = [self.selected_session]
        else:
            targets = []

        if len(targets) > 1:
            lines = [Content.styled(f"{len(targets)} sessions selected", HEADING), Content("")]
            total = 0
            for s in targets:
                total += s.size_bytes
                mark = MARK_ON if s.key in self.selected_session_ids else MARK_OFF
                agent = self._agent_label(s.provider)
                lines.append(
                    Content.assemble(
                        f"{mark} ",
                        (agent, SECONDARY),
                        "  ",
                        (f"{s.session_id[:8]}…", SECONDARY),
                        "  ",
                        (_fmt_size(s.size_bytes), MUTED),
                        "  ",
                        s.display_title[:40],
                    )
                )
            lines.append(Content(""))
            lines.append(
                Content.assemble(
                    ("Total", TOTAL), f"  {_fmt_size(total)} across {len(targets)} session(s)"
                )
            )
            self._detail_session = None
            detail.update(_lines(lines), subject="sessions:marked")
            return

        s = targets[0] if targets else self.selected_session
        if not s:
            if self.selected_project and not self.selected_project.sessions:
                self._show_project_detail()
                return
            detail.update(Content.styled("Select a session", MUTED))
            return
        self._detail_session = s
        detail.update(self._session_detail_text(s), subject=f"session:{s.key}")

    # ---- session details (lazy, cached, off the UI thread) -------------------

    # Seconds the highlight must rest on a session before its details load,
    # so holding j / k never queues a read per row.
    DETAILS_DELAY = 0.1
    DETAILS_CACHE_SIZE = 256

    def _details_key(self, s: SessionMeta) -> tuple[str, str, float]:
        return (s.provider or self._provider_for_session(s).name, s.session_id, s.mtime)

    def _running_state(self, s: SessionMeta) -> str:
        live = live_for_session(self.live, s)
        if live is None:
            return "not running"
        where = "in the background" if live.kind == "background" else "in a terminal"
        if live.is_live:
            text = f"running {where}"
        else:
            text = "not running"
        if live.state and live.state not in {"idle", "running"}:
            text += f" · {live.state}"
        if live.is_remote or s.has_bridge:
            text += " · remote"
        return text

    def _session_detail_text(self, s: SessionMeta) -> Content:
        """Facts for ``s``; the conversation part loads lazily on a cache miss."""
        key = self._details_key(s)
        details = self._details_cache.get(key)
        if details is None:
            self._schedule_details(s)
        else:
            self._details_cache.move_to_end(key)
        provider = self._provider_for_session(s)
        return session_details_text(
            s,
            details,
            agent=self._agent_label(s.provider or provider.name),
            state=self._running_state(s),
            database=provider.stored_in_database(s),
            now=datetime.now().astimezone(),
            home=str(Path.home()),
        )

    def _schedule_details(self, s: SessionMeta) -> None:
        if self._details_key(s) in self._details_pending:
            return
        if self._details_timer is not None:
            self._details_timer.stop()
        self._details_timer = self.set_timer(
            self.DETAILS_DELAY, lambda: self._start_details_load(s)
        )

    def _start_details_load(self, s: SessionMeta) -> None:
        self._details_timer = None
        key = self._details_key(s)
        shown = self._detail_session
        if shown is None or self._details_key(shown) != key:
            return  # the highlight moved on; that session schedules its own load
        if key in self._details_cache or key in self._details_pending:
            return
        self._details_pending.add(key)
        provider = self._provider_for_session(s)
        self.run_worker(
            lambda: self._load_details(provider, s, key),
            thread=True,
            group="details",
            exit_on_error=False,
        )

    def _load_details(
        self, provider: SessionProvider, s: SessionMeta, key: tuple[str, str, float]
    ) -> None:
        """Worker thread: ask the agent for the details; never raises."""
        try:
            details = provider.session_details(s)
            if not isinstance(details, SessionDetails):
                raise TypeError("unexpected details")
        except Exception as exc:  # odd agent data must not break cursor movement
            reason = " ".join(str(exc).splitlines()[:1]) or type(exc).__name__
            details = SessionDetails(note=f"Details unavailable: {reason}"[:200])
        with contextlib.suppress(Exception):  # the app may be closing
            self.call_from_thread(self._details_loaded, key, details)

    def _details_loaded(
        self, key: tuple[str, str, float], details: SessionDetails
    ) -> None:
        self._details_pending.discard(key)
        self._details_cache[key] = details
        self._details_cache.move_to_end(key)
        while len(self._details_cache) > self.DETAILS_CACHE_SIZE:
            self._details_cache.popitem(last=False)
        shown = self._detail_session
        try:
            detail = self.query_one("#detail", DetailPane)
        except Exception:
            return
        if shown is not None and detail.subject == f"session:{shown.key}" and (
            self._details_key(shown) == key
        ):
            self._show_detail()

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
        if not self._guard("toggle_select"):
            return
        p = self.selected_project
        if self._focus_pane() == "projects" and p is not None:
            if p.key in self.selected_project_slugs:
                self.selected_project_slugs.discard(p.key)
            else:
                self.selected_project_slugs.add(p.key)
            self._update_project_marks()
            return
        s = self.selected_session
        if s is None:
            return
        if s.key in self.selected_session_ids:
            self.selected_session_ids.discard(s.key)
        else:
            self.selected_session_ids.add(s.key)
        self._update_session_marks()

    def action_select_all(self) -> None:
        if not self._guard("select_all"):
            return
        pane = self._focus_pane()
        if pane == "projects":
            for p in self.projects:
                self.selected_project_slugs.add(p.key)
            self._update_project_marks()
            return
        if pane in {"sessions", "detail"}:
            for s in self._visible_sessions():
                self.selected_session_ids.add(s.key)
            self._update_session_marks()

    def action_clear_select(self) -> None:
        """Clear project and session marks everywhere."""
        if not self._guard("clear_select"):
            return
        n_proj = len(self.selected_project_slugs)
        n_sess = len(self.selected_session_ids)
        self.selected_project_slugs.clear()
        self.selected_session_ids.clear()
        self._update_project_marks()
        self._update_session_marks()
        self._refresh_status()
        self._show_toast(
            f"Cleared selections (proj={n_proj} sess={n_sess})",
            success=True,
            seconds=2.5,
        )

    def action_focus_session_filter(self) -> None:
        """Focus the sessions filter box."""
        if not self._guard("focus_session_filter"):
            return
        inp = self.query_one("#sessions-filter", Input)
        inp.focus()
        inp.cursor_position = len(inp.value)

    def action_cycle_session_sort(self) -> None:
        """Cycle sessions sort mode."""
        if not self._guard("cycle_session_sort"):
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

    async def action_quit(self) -> None:
        if not self._guard("quit"):
            return
        marks = len(self.selected_session_ids) + len(self.selected_project_slugs)
        if not marks:
            self.exit()
            return

        def done(confirmed: bool | None) -> None:
            if confirmed:
                self.exit()

        self.push_screen(QuitConfirmScreen(marks), done)

    def action_command_palette(self) -> None:
        if not self._guard("command_palette"):
            return
        super().action_command_palette()

    async def run_registry_action(self, action_id: str) -> None:
        """Run a registry action chosen outside its key (the command palette).

        The availability check runs first and always explains a refusal, also
        for actions whose key stays quiet (Esc). The action is called directly,
        so a focused text field does not swallow it.
        """
        if not self._guard(action_id):
            return
        result = getattr(self, f"action_{action_id}")()
        if inspect.isawaitable(result):
            await result

    def action_show_help(self) -> None:
        if not self._guard("show_help"):
            return
        self.push_screen(HelpScreen())

    def action_show_settings(self) -> None:
        """Open provider / config-dir settings."""
        if not self._guard("show_settings"):
            return

        previous_default = default_provider_id(self._axism_settings)

        def _after(updated: Settings | None) -> None:
            if updated is None:
                self._show_toast("Settings cancelled", success=False, seconds=2)
                return
            if self._axism_settings.load_error():
                # Never replace a settings file that could not be read.
                self._show_toast(UNREADABLE_REFUSAL, success=False, seconds=4)
                return
            try:
                path = save_settings(updated)
            except OSError as exc:
                self._show_toast(f"Could not save settings: {exc}", success=False, seconds=4)
                return
            self._axism_settings = updated
            self.providers = self._providers_for(updated)
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
            new_default = default_provider_id(updated)
            if new_default != previous_default:
                msg += f" · CLI default: {PROVIDER_LABELS.get(new_default, new_default)}"
            self._show_toast(msg, success=True, seconds=4)

        self.push_screen(
            SettingsScreen(
                self._axism_settings,
                session_counts=self._session_counts(),
                run_provider=self._run_provider,
                run_config_dir=self._run_config_dir,
                run_config_agent=self._run_config_agent,
            ),
            _after,
        )

    def _session_counts(self) -> dict[tuple[str, str], int]:
        """Sessions per (agent, folder) in the current inventory, for Settings."""
        counts: dict[tuple[str, str], int] = {}
        for provider in self.providers:
            try:
                root = str(provider.config_root())
            except (OSError, UnsupportedOperation):
                continue
            counts[(provider.name, root)] = sum(
                len(project.sessions)
                for project in self.projects
                if project.provider == provider.name
            )
        return counts

    def action_show_version(self) -> None:
        """Show version and runtime details."""
        if not self._guard("show_version"):
            return
        try:
            import textual

            textual_ver = getattr(textual, "__version__", "?")
        except Exception:
            textual_ver = "?"
        def fact(label: str, value: str) -> Content:
            return Content.assemble((f"{label:<10}", MUTED), value)

        lines = [
            Content.assemble(("aXism", HEADING), f"  v{version_string()}"),
            Content.styled("Agent Interactive Session Manager", MUTED),
            Content(""),
        ]
        if __commit__:
            lines.append(fact("Commit", __commit__))
        lines.extend(
            [
                fact("Python", sys.version.split()[0]),
                fact("Textual", textual_ver),
                fact("Platform", f"{platform.system()} {platform.release()}"),
                fact("Machine", platform.machine()),
                Content(""),
                fact("Providers", ", ".join(p.label for p in self.providers)),
                fact("Config", roots_summary(self.providers)),
                fact("Settings", str(settings_path())),
                fact("Theme", str(self.theme)),
                Content(""),
                fact("Projects", str(len(self.projects))),
                fact("Sessions", str(sum(p.session_count for p in self.projects))),
            ]
        )
        self.push_screen(VersionScreen(_lines(lines)))

    def action_stop_sessions(self) -> None:
        """Stop background sessions (same as typing ``/stop`` while attached)."""
        if not self._guard("stop_sessions"):
            return
        targets = self._stoppable_among(self._target_sessions_for_stop())

        warnings = [
            "Stops each session via its agent tool.",
            "Conversation/transcript is kept; use Delete to remove files.",
        ]
        body = _format_stop_confirm(targets)

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
                try:
                    ok, msg = self._provider_for_session(s).slash_stop(
                        s.session_id, live
                    )
                except UnsupportedOperation as exc:
                    ok, msg = False, f"refused: {exc}"
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
        if not self._guard("resume_session"):
            return
        s = self.selected_session
        if s is None:
            return
        provider = self._provider_for_session(s)
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
        if not self._guard("rename_session"):
            return
        s = self.selected_session
        if s is None:
            return
        provider = self._provider_for_session(s)
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
            if (
                live.is_killable
                or (
                    live.kind == "background"
                    and live.state in {"working", "blocked", "running"}
                )
                or self._provider_for_session(s).can_stop(live)
            ):
                live_n += 1
        if live_n:
            return f"{live_n} live/background session(s) — stop them first (s)"
        return None

    def action_move_items(self) -> None:
        """Move focused/marked project(s) or session(s) within the same agent tool."""
        if not self._guard("move_items"):
            return
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
                    Content.assemble(
                        (f"{n} project(s)", "bold"), " → ", (dest, SECONDARY)
                    ),
                    Content.styled(f"via {provider.label}", MUTED),
                    Content(""),
                    Content.styled("Sources", "bold"),
                ]
                for p in targets:
                    body_lines.append(
                        Content(f"  {p.cwd_guess}  ({p.session_count} sessions)")
                    )
                self.push_screen(
                    ConfirmDeleteScreen(
                        _lines(body_lines),
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
                    Content.assemble(
                        (f"{n} session(s)", "bold"), " → ", (dest, SECONDARY)
                    ),
                    Content.styled(
                        f"{provider.label} → {dest_provider.label} (copy, source kept)"
                        if is_cross
                        else f"via {provider.label} (relocate)",
                        MUTED,
                    ),
                    Content(""),
                    Content.styled("Targets", "bold"),
                ]
                for s in sessions[:30]:
                    body_lines.append(
                        Content(f"  {s.session_id[:8]}…  {s.display_title[:40]}")
                    )
                if n > 30:
                    body_lines.append(Content(f"  … +{n - 30} more"))
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
                        _lines(body_lines),
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
            body = _lines(
                [
                    Content.styled("Memory merge exceeds handoff limits", "bold"),
                    Content(""),
                    Content(stats.summary() if stats else ""),
                    Content(""),
                    Content("You can still force an agent handoff, or skip."),
                ]
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
        if not self._guard("pick_theme"):
            return
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
        if not self._guard("next_theme"):
            return
        self._cycle_theme(1)

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
        has_memory = provider.capabilities.memory_merge
        keys = [p.key for p in projects]
        slugs = [p.slug for p in projects]

        # Plans (and their live lookups) are built once; only the memory
        # wording differs between the two toggle states.
        plans_preview = [
            provider.plan_project_delete(p.slug, force=True, keep_memory=False)
            for p in projects
        ]

        def _build(keep: bool) -> tuple[Content, list[str]]:
            packed: list[tuple[str, str, object, list[str]]] = []
            for p, base in zip(projects, plans_preview, strict=True):
                plan = replace(base, keep_memory=keep)
                preview = provider.execute_project_delete(
                    plan, dry_run=True, force=True, stop_live=True
                )
                packed.append((p.slug, p.cwd_guess, plan, preview))
            return _format_project_delete_confirm(packed, provider)

        body, warnings = _build(False)
        screen_ref: list[ConfirmDeleteScreen] = []

        def _after(confirmed: bool | None) -> None:
            if not confirmed:
                self._set_status("Project delete cancelled")
                self._show_toast("Project delete cancelled", success=False, seconds=2.5)
                return
            keep = screen_ref[0].keep_memory if screen_ref else False
            all_actions: list[str] = []
            for slug in slugs:
                plan_exec = provider.plan_project_delete(
                    slug, force=True, keep_memory=keep
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

        def _memory(keep: bool) -> tuple[Content, Content]:
            return _build(keep)[0], _memory_line(n, keep)

        screen = ConfirmDeleteScreen(
            body,
            title=f"Delete {n} projects" if n != 1 else "Delete project",
            warnings=warnings
            + [
                f"Deletes via {provider.label}.",
                "Deletes the backend's project directory and linked session data.",
                "Source workspace files on disk are not moved or removed.",
            ],
            confirm_verb="Delete",
            project_danger=True,
            memory_toggle=_memory if has_memory else None,
        )
        screen_ref.append(screen)
        self.push_screen(screen, _after)

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
            targets, plans, preview_actions, provider
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
        if not self._guard("delete_items"):
            return
        if self._focus_pane() == "projects":
            self._confirm_delete_projects(self._target_projects())
            return
        self._confirm_delete_sessions(self._target_sessions_for_delete())


def run_tui(provider: str | None = None, config_dir: str | None = None) -> None:
    """Run the app; ``provider`` / ``config_dir`` apply to this run only."""
    app = SessionManagerApp(run_provider=provider, run_config_dir=config_dir)
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
