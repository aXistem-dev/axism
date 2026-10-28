"""The context footer: one row of keys for the focused pane or mode.

Entries come from the key registry (``tui/keys.py``), so the footer cannot
disagree with what a key does. Keys that are relevant but unavailable right
now stay in place, dimmed, so the layout does not jump around.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.cells import cell_len
from rich.style import Style
from rich.text import Text
from textual.widgets import Static

from axism.tui.keys import KeyAction, actions_for, key_display

SEPARATOR = " · "
FILTER_ENTRIES = (("Enter", "done"), ("Esc", "clear"))

KEY_STYLE = Style(color="#6ed4c4", bold=True)
LABEL_STYLE = Style(color="#e8eef5")
SEPARATOR_STYLE = Style(color="#9aabbc")
DIM_STYLE = Style(dim=True)


def footer_actions(scope: str) -> list[KeyAction]:
    """Footer entries for ``scope`` plus the main-screen globals, by priority."""
    seen: set[str] = set()
    chosen: list[KeyAction] = []
    for action in [*actions_for(scope), *actions_for("main")]:
        if action.footer_priority is None or action.id in seen:
            continue
        seen.add(action.id)
        chosen.append(action)
    chosen.sort(key=lambda action: action.footer_priority or 0)
    return chosen


class ContextFooter(Static):
    """Bottom row: the keys that apply where the focus is."""

    DEFAULT_CSS = """
    ContextFooter {
        height: 1;
        width: 1fr;
        background: #1a1f2a;
        color: #e8eef5;
        padding: 0 1;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("", id=id)
        self._scope = "main"
        self._available: Callable[[str], bool] = lambda _action_id: True

    def set_context(self, scope: str, available: Callable[[str], bool]) -> None:
        """Show the keys for ``scope``; ``available`` says which ones to dim."""
        self._scope = scope
        self._available = available
        self.refresh()

    @property
    def scope(self) -> str:
        return self._scope

    def render(self) -> Text:
        width = self.content_region.width or self.size.width or 80
        return self.render_for(self._scope, self._available, width)

    def render_for(
        self, scope: str, available: Callable[[str], bool], width: int
    ) -> Text:
        """The footer row for ``scope``, cut after the last entry that fits ``width``."""
        if scope == "filter":
            entries = [(key, label, True) for key, label in FILTER_ENTRIES]
        else:
            entries = [
                (key_display(action.keys[0]), action.label, bool(available(action.id)))
                for action in footer_actions(scope)
            ]
        text = Text(no_wrap=True, overflow="crop", end="")
        for key, label, enabled in entries:
            piece = f"{key} {label}"
            needed = cell_len(piece) + (cell_len(SEPARATOR) if text.plain else 0)
            if text.cell_len + needed > width:
                break
            if text.plain:
                text.append(SEPARATOR, SEPARATOR_STYLE)
            key_style = KEY_STYLE if enabled else KEY_STYLE + DIM_STYLE
            label_style = LABEL_STYLE if enabled else LABEL_STYLE + DIM_STYLE
            text.append(key, key_style)
            text.append(" ")
            text.append(label, label_style)
        return text
