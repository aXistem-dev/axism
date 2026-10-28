"""The conversation reader: one session's whole conversation, full screen.

Turns come from the session's agent (``SessionProvider.transcript``) as text
turns only. The screen scrolls with the registry movement keys (scope
``reader``); Esc goes back to the details pane.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import ScrollableContainer, VerticalScroll
from textual.content import Content
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


def conversation_content(turns: list[Turn]) -> Content:
    """Alternating ``You`` / ``Agent`` blocks.

    Only the role names carry styles; the turn text goes in as plain text, so
    brackets, ``$`` and backslashes in a conversation are shown as written.
    """
    if not turns:
        return Content.styled("No text turns in this conversation.", "$text-muted")
    blocks: list[Content] = []
    for turn in turns:
        name = ROLE_NAMES.get(turn.role, turn.role.capitalize() or "?")
        colour = "$accent" if turn.role == "user" else "$primary"
        blocks.append(
            Content.assemble((name, f"bold {colour}"), "\n", turn.content.strip())
        )
    return Content("\n\n").join(blocks)


def _note(text: str) -> Content:
    return Content.styled(_one_line(text), "$text-warning")


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
            Static(
                Content.styled("Loading…", "$text-muted"), id="reader-body"
            ),
            id="reader-scroll",
            can_focus=True,
        )
        yield ContextFooter(self.SCOPE)

    def on_mount(self) -> None:
        self.run_worker(self._load, thread=True, group="reader", exit_on_error=False)

    def _load(self) -> None:
        """Worker thread: build the conversation, then show it."""
        body = self._body()
        with contextlib.suppress(Exception):  # the screen may already be closed
            self.app.call_from_thread(self._show, body)

    def _show(self, body: Content) -> None:
        if not self.is_attached:
            return
        with contextlib.suppress(Exception):
            self.query_one("#reader-body", Static).update(body)

    def _body(self) -> Content:
        """The conversation, or one line saying why it cannot be shown."""
        try:
            turns = self.provider.transcript(self.session)
        except TranscriptUnavailable as exc:
            return _note(exc.reason)
        except Exception as exc:  # an agent's odd data must not crash the app
            return _note(f"Cannot read conversation: {exc}")
        return conversation_content(turns)

    def scroll_view(self) -> ScrollableContainer:
        return self.query_one("#reader-scroll", VerticalScroll)

    def action_close_reader(self) -> None:
        self.dismiss(None)
