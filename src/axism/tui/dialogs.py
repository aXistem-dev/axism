"""Small dialogs of the main screen."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


def quit_prompt(marks: int) -> str:
    """``Quit and drop N marks?`` with the right plural."""
    noun = "mark" if marks == 1 else "marks"
    return f"Quit and drop {marks} {noun}?"


class QuitConfirmScreen(ModalScreen[bool]):
    """Ask before quitting when anything is marked: y quits, n / Esc stays."""

    DEFAULT_CSS = """
    QuitConfirmScreen {
        align: center middle;
    }
    #quit-dialog {
        width: 44;
        max-width: 90%;
        height: auto;
        border: thick $warning;
        background: $surface;
        padding: 1 2;
    }
    #quit-keys {
        margin-top: 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("y", "confirm", "Quit", show=False, priority=True),
        Binding("Y", "confirm", "Quit", show=False, priority=True),
        Binding("n", "cancel", "Stay", show=False, priority=True),
        Binding("N", "cancel", "Stay", show=False, priority=True),
        Binding("escape", "cancel", "Stay", show=False, priority=True),
    ]

    def __init__(self, marks: int) -> None:
        super().__init__()
        self.marks = marks

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(quit_prompt(self.marks), id="quit-prompt", markup=False),
            Static("[b]y[/b]  Quit     [b]n[/b] / Esc  Stay", id="quit-keys"),
            id="quit-dialog",
        )

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
