"""The help screen and the command palette, both generated from the key registry."""

from __future__ import annotations

from collections.abc import Iterator
from functools import partial
from typing import TYPE_CHECKING

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import ScrollableContainer, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from axism.tui.keys import REGISTRY, KeyAction, bindings_for, help_sections, key_display
from axism.tui.scrolling import ScrollKeys

if TYPE_CHECKING:
    from axism.tui.app import SessionManagerApp

HELP_TITLE = "aXism keys"

HELP_NOTES = (
    (
        "Marks are per pane: Space marks projects in the Projects pane and "
        "sessions in the Sessions pane. Move and delete act on the marks, or on "
        "the highlighted row when nothing is marked; c clears every mark."
    ),
    (
        "Move and delete refuse marks from different agents: unmark the rows of "
        "the other agent first."
    ),
    "Uppercase letters do the same as lowercase, except G (last row) and T (next theme).",
    (
        "In a text field, typed keys go into the text; Esc leaves it, Tab moves "
        "on and Ctrl+P opens the command palette."
    ),
)


def keys_text(action: KeyAction) -> str:
    """Every key of ``action`` for display: ``d / D / Del``."""
    return " / ".join(key_display(key) for key in action.keys)


def _key_width() -> int:
    return max(len(keys_text(action)) for action in REGISTRY)


def help_text() -> str:
    """The help screen's content as plain text (same rows, same order)."""
    width = _key_width()
    lines = [HELP_TITLE, ""]
    for group, actions in help_sections():
        lines.append(group)
        lines.extend(
            f"  {keys_text(action):<{width}}  {action.description}" for action in actions
        )
        lines.append("")
    lines.append("Notes")
    lines.extend(f"  {note}" for note in HELP_NOTES)
    return "\n".join(lines)


def _section_table(actions: list[KeyAction], width: int) -> Table:
    table = Table.grid(padding=(0, 2), expand=True)
    table.add_column(min_width=width, no_wrap=True, style="bold")
    table.add_column(ratio=1, overflow="fold")
    for action in actions:
        table.add_row(Text(keys_text(action)), Text(action.description))
    return table


def _notes_table() -> Table:
    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column(no_wrap=True)
    table.add_column(ratio=1, overflow="fold")
    for note in HELP_NOTES:
        table.add_row("·", Text(note))
    return table


class HelpScreen(ScrollKeys, ModalScreen[None]):
    """Every key, grouped, generated from the registry; scrolls like a pane."""

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }
    #help-dialog {
        width: 90%;
        max-width: 110;
        height: 90%;
        border: thick $primary;
        background: $surface;
        padding: 0 1;
    }
    #help-scroll {
        height: 1fr;
    }
    #help-scroll > Static {
        width: 100%;
        height: auto;
        padding: 0 1;
    }
    #help-scroll > .help-heading {
        margin-top: 1;
    }
    #help-hint {
        height: 1;
        color: $text-muted;
        padding: 0 1;
    }
    """

    # Movement keys plus Esc / q / ? to close, all from the registry.
    BINDINGS = bindings_for("help")

    AUTO_FOCUS = "#help-scroll"

    def compose(self) -> ComposeResult:
        yield Vertical(
            VerticalScroll(*self._content(), id="help-scroll", can_focus=True),
            Static("Esc / q / ?  close · j k g G Ctrl+D Ctrl+U PgUp PgDn  scroll",
                   id="help-hint", markup=False),
            id="help-dialog",
        )

    def _content(self) -> Iterator[Static]:
        width = _key_width()
        yield Static(f"[b $accent]{HELP_TITLE}[/]", id="help-title")
        for group, actions in help_sections():
            yield Static(f"[b $primary]{group}[/]", classes="help-heading")
            yield Static(_section_table(actions, width))
        yield Static("[b $primary]Notes[/]", classes="help-heading")
        yield Static(_notes_table(), id="help-notes")

    def scroll_view(self) -> ScrollableContainer:
        return self.query_one("#help-scroll", VerticalScroll)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_close_help(self) -> None:
        self.dismiss(None)


class RegistryCommands(Provider):
    """Command palette entries: one per registry action, named by its description.

    Choosing one runs the app action through the same availability check as
    its key, so an unavailable choice shows the same reason.
    """

    def _commands(self) -> list[KeyAction]:
        return [action for action in REGISTRY if action.palette]

    def _run(self, action_id: str):
        app: SessionManagerApp = self.app  # type: ignore[assignment]
        return partial(app.run_registry_action, action_id)

    async def discover(self) -> Hits:
        # The footer's actions first, in footer order; then the rest.
        commands = self._commands()
        commands.sort(
            key=lambda a: (a.footer_priority is None, a.footer_priority or 0)
        )
        for action in commands:
            yield DiscoveryHit(
                action.description,
                self._run(action.id),
                text=action.description,
                help=keys_text(action),
            )

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for action in self._commands():
            score = matcher.match(action.description)
            label_score = matcher.match(action.label)
            best = max(score, label_score)
            if best > 0:
                yield Hit(
                    best,
                    matcher.highlight(action.description),
                    self._run(action.id),
                    text=action.description,
                    help=keys_text(action),
                )
