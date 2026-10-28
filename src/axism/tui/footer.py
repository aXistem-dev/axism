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

from axism.tui.keys import KeyAction, actions_for

SEPARATOR = " · "
# Main-screen scopes: their footer also lists the app-wide keys (help, quit, …).
# Dialogs, help and Settings list only their own keys.
MAIN_SCOPES = frozenset({"main", "projects", "sessions", "details", "filter"})
FILTER_ENTRIES = (("Enter", "done"), ("Esc", "clear"))

# Fallbacks for a footer that is not mounted (tests, previews); mounted, the
# colours come from the theme through the component classes below.
KEY_STYLE = Style(bold=True)
LABEL_STYLE = Style()
SEPARATOR_STYLE = Style()
DIM_STYLE = Style(dim=True)


def footer_actions(scope: str) -> list[KeyAction]:
    """Footer entries for ``scope`` (plus the main-screen globals on the main
    screen), by priority."""
    seen: set[str] = set()
    chosen: list[KeyAction] = []
    globals_ = actions_for("main") if scope in MAIN_SCOPES else []
    for action in [*actions_for(scope), *globals_]:
        if action.footer_priority_in(scope) is None or action.id in seen:
            continue
        seen.add(action.id)
        chosen.append(action)
    chosen.sort(key=lambda action: action.footer_priority_in(scope) or 0)
    return chosen


class ContextFooter(Static):
    """Bottom row: the keys that apply where the focus is."""

    COMPONENT_CLASSES = {
        "context-footer--key",
        "context-footer--label",
        "context-footer--separator",
    }

    DEFAULT_CSS = """
    ContextFooter {
        height: 1;
        width: 1fr;
        background: $footer-background;
        color: $footer-foreground;
        padding: 0 1;
        .context-footer--key {
            color: $footer-key-foreground;
            text-style: bold;
        }
        .context-footer--label {
            color: $footer-description-foreground;
        }
        .context-footer--separator {
            color: $footer-description-foreground 60%;
        }
    }
    """

    def __init__(
        self,
        scope: str = "main",
        available: Callable[[str], bool] | None = None,
        *,
        hidden: frozenset[str] = frozenset(),
        labels: dict[str, str] | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__("", id=id)
        # Labels that replace the registry's for this instance (action id → label).
        self._labels = labels or {}
        self._scope = scope
        self._available: Callable[[str], bool] = available or (lambda _action_id: True)
        self._hidden = hidden

    def set_context(
        self,
        scope: str,
        available: Callable[[str], bool],
        *,
        hidden: frozenset[str] = frozenset(),
    ) -> None:
        """Show the keys for ``scope``; ``available`` says which ones to dim.

        ``hidden`` leaves out actions that do not apply at all here (the
        delete dialog's memory key when there is no project memory).
        """
        self._scope = scope
        self._available = available
        self._hidden = hidden
        self.refresh()

    @property
    def scope(self) -> str:
        return self._scope

    def render(self) -> Text:
        width = self.content_region.width or self.size.width or 80
        try:
            styles = (
                self.get_component_rich_style("context-footer--key"),
                self.get_component_rich_style("context-footer--label"),
                self.get_component_rich_style("context-footer--separator"),
            )
        except Exception:  # not mounted yet
            styles = None
        return self.render_for(
            self._scope, self._available, width, self._hidden, self._labels, styles
        )

    def render_for(
        self,
        scope: str,
        available: Callable[[str], bool],
        width: int,
        hidden: frozenset[str] = frozenset(),
        labels: dict[str, str] | None = None,
        styles: tuple[Style, Style, Style] | None = None,
    ) -> Text:
        """The footer row for ``scope``, cut after the last entry that fits ``width``.

        ``styles`` are the theme's key, label and separator styles; their
        background is dropped so the row keeps the footer's own.
        """
        key_base, label_base, sep_base = (
            (Style(color=s.color, bold=s.bold or None) for s in styles)
            if styles
            else (KEY_STYLE, LABEL_STYLE, SEPARATOR_STYLE)
        )
        labels = labels or {}
        if scope == "filter":
            entries = [(key, label, True) for key, label in FILTER_ENTRIES]
        else:
            entries = [
                (
                    action.footer_keys_in(scope),
                    labels.get(action.id, action.label_in(scope)),
                    bool(available(action.id)),
                )
                for action in footer_actions(scope)
                if action.id not in hidden
            ]
        text = Text(no_wrap=True, overflow="crop", end="")
        for key, label, enabled in entries:
            piece = f"{key} {label}"
            needed = cell_len(piece) + (cell_len(SEPARATOR) if text.plain else 0)
            if text.cell_len + needed > width:
                break
            if text.plain:
                text.append(SEPARATOR, sep_base)
            key_style = key_base if enabled else key_base + DIM_STYLE
            label_style = label_base if enabled else label_base + DIM_STYLE
            text.append(key, key_style)
            text.append(" ")
            text.append(label, label_style)
        return text
