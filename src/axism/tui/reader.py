"""The conversation reader: one session's whole conversation, full screen.

Turns come from the session's agent (``SessionProvider.transcript``) as text
turns only. The screen scrolls with the registry movement keys (scope
``reader``); Esc goes back to the details pane.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import ScrollableContainer, VerticalScroll
from textual.markup import escape
from textual.screen import Screen
from textual.widgets import Static

from axism.details import TranscriptUnavailable
from axism.discover import SessionMeta
from axism.tui.footer import ContextFooter
from axism.tui.keys import bindings_for
from axism.tui.scrolling import ScrollKeys

if TYPE_CHECKING:
    from axism.providers import SessionProvider
    from axism.providers.transfer import Turn

ROLE_NAMES = {"user": "You", "assistant": "Agent"}


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def conversation_markup(turns: list[Turn]) -> str:
    """Alternating ``You`` / ``Agent`` blocks, the turn text escaped."""
    if not turns:
        return "[$text-muted]No text turns in this conversation.[/]"
    blocks: list[str] = []
    for turn in turns:
        name = ROLE_NAMES.get(turn.role, turn.role.capitalize() or "?")
        colour = "$accent" if turn.role == "user" else "$primary"
        blocks.append(f"[b {colour}]{escape(name)}[/]\n{escape(turn.content.strip())}")
    return "\n\n".join(blocks)


class ReaderScreen(ScrollKeys, Screen[None]):
    """Full-screen view of one conversation: header, turns, context footer."""

    SCOPE = "reader"
    BINDINGS = bindings_for(SCOPE)
    AUTO_FOCUS = "#reader-scroll"

    DEFAULT_CSS = """
    ReaderScreen {
        background: $background;
    }
    #reader-header {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $accent;
        text-style: bold;
    }
    #reader-scroll {
        height: 1fr;
        border: round $primary;
        padding: 0 1;
    }
    #reader-scroll:focus {
        border: round $accent;
    }
    #reader-body {
        width: 100%;
        height: auto;
    }
    """

    def __init__(self, session: SessionMeta, provider: SessionProvider, agent: str) -> None:
        super().__init__()
        self.session = session
        self.provider = provider
        self.agent = agent

    def compose(self) -> ComposeResult:
        title = _one_line(self.session.display_title)
        yield Static(f"{title} · {self.agent}", id="reader-header", markup=False)
        yield VerticalScroll(
            Static(self._body(), id="reader-body"), id="reader-scroll", can_focus=True
        )
        yield ContextFooter(self.SCOPE)

    def _body(self) -> str:
        """The conversation, or one line saying why it cannot be shown."""
        try:
            turns = self.provider.transcript(self.session)
        except TranscriptUnavailable as exc:
            return f"[$text-warning]{escape(_one_line(exc.reason))}[/]"
        except Exception as exc:  # an agent's odd data must not crash the app
            return f"[$text-warning]Cannot read conversation: {escape(_one_line(exc))}[/]"
        return conversation_markup(turns)

    def scroll_view(self) -> ScrollableContainer:
        return self.query_one("#reader-scroll", VerticalScroll)

    def action_close_reader(self) -> None:
        self.dismiss(None)
