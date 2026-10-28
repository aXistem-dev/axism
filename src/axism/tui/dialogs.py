"""Dialogs of the main screen: confirm, quit, rename, move, agent, theme, version.

Every dialog takes its keys from the key registry (scope ``dialog:<name>``) and
shows them in its own context footer. A dialog is ``height: auto`` up to 90% of
the terminal: its title sits in a head docked at the top, its key line in a
foot docked at the bottom, and the body in between shrinks and scrolls (with
the navigation keys) when the terminal is small, so the key line is always on
screen.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar, TypeVar

from textual.app import ComposeResult
from textual.containers import ScrollableContainer, Vertical, VerticalScroll
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from axism.tui.footer import ContextFooter
from axism.tui.keys import bindings_for
from axism.tui.scrolling import OptionListKeys, ScrollKeys

ResultT = TypeVar("ResultT")

# While a dialog's text field has focus only these act; every other key belongs
# to the field (Inputs win).
INPUT_ACTIONS = frozenset({"cancel", "apply", "focus_path", "focus_list"})


def plain(text: str | Content) -> Content:
    """``text`` as shown on screen: a string is plain text, never markup.

    Dialog bodies, titles and lists carry session titles, folders, paths and
    agent messages; aXism's own styling comes in as ``Content``.
    """
    return text if isinstance(text, Content) else Content(text)


def quit_prompt(marks: int) -> str:
    """``Quit and drop N marks?`` with the right plural."""
    noun = "mark" if marks == 1 else "marks"
    return f"Quit and drop {marks} {noun}?"


class DialogScreen(ModalScreen[ResultT]):
    """Shared layout, footer and Inputs-win rule of the dialogs."""

    SCOPE: ClassVar[str] = ""

    DEFAULT_CSS = """
    DialogScreen {
        align: center middle;
    }
    DialogScreen .dialog {
        width: 90%;
        max-width: 110;
        height: auto;
        max-height: 90%;
        border: thick $primary;
        background: $surface;
        padding: 0 1;
    }
    DialogScreen .dialog-head {
        dock: top;
        height: auto;
    }
    DialogScreen .dialog-foot {
        dock: bottom;
        height: auto;
    }
    DialogScreen .dialog-body {
        height: auto;
        max-height: 100%;
    }
    DialogScreen .dialog-title {
        text-style: bold;
        color: $accent;
    }
    DialogScreen .dialog-hint {
        color: $text-muted;
    }
    DialogScreen ContextFooter {
        margin-top: 1;
    }
    """

    def footer_hidden(self) -> frozenset[str]:
        """Registry actions of this dialog that do not apply to this instance."""
        return frozenset()

    def footer(self) -> ContextFooter:
        return ContextFooter(
            self.SCOPE,
            lambda action_id: self.check_action(action_id, ()) is not False,
            hidden=self.footer_hidden(),
        )

    def check_action(
        self, action: str, parameters: tuple[object, ...]
    ) -> bool | None:
        if isinstance(self.focused, Input) and action not in INPUT_ACTIONS:
            # Inputs win: Home / End / PgUp / … edit the text, not the list.
            return False
        return True


class QuitConfirmScreen(DialogScreen[bool]):
    """Ask before quitting when anything is marked: y quits, n / Esc stays."""

    SCOPE = "dialog:quit"
    BINDINGS = bindings_for(SCOPE)

    DEFAULT_CSS = """
    QuitConfirmScreen #quit-dialog {
        width: 48;
        max-width: 90%;
        border: thick $warning;
        padding: 1 2 0 2;
    }
    QuitConfirmScreen #quit-prompt {
        padding: 0 1;
    }
    """

    def __init__(self, marks: int) -> None:
        super().__init__()
        self.marks = marks

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(quit_prompt(self.marks), id="quit-prompt", markup=False),
            Vertical(self.footer(), classes="dialog-foot"),
            id="quit-dialog",
            classes="dialog",
        )

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_decline(self) -> None:
        self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)


class ConfirmDeleteScreen(ScrollKeys, DialogScreen[bool]):
    """Confirm a destructive action with a clear summary and y/n prompt.

    ``body`` and ``title`` are plain text unless given as ``Content``; the
    warnings are plain text.
    """

    SCOPE = "dialog:confirm"
    BINDINGS = bindings_for(SCOPE)
    AUTO_FOCUS = "#confirm-scroll"

    DEFAULT_CSS = """
    ConfirmDeleteScreen #confirm-dialog {
        border: thick $error;
    }
    ConfirmDeleteScreen #confirm-dialog.-project-danger {
        border: double $error;
        background: $error 12%;
    }
    ConfirmDeleteScreen #confirm-title {
        color: $error;
        text-style: bold;
    }
    ConfirmDeleteScreen .-project-danger #confirm-title {
        background: $error 35%;
        color: $text;
        padding: 0 1;
    }
    ConfirmDeleteScreen #confirm-memory {
        height: auto;
        padding: 0 1;
        margin-top: 1;
        border: solid $warning;
        text-style: bold;
    }
    ConfirmDeleteScreen #confirm-memory.-empty {
        display: none;
    }
    ConfirmDeleteScreen #confirm-scroll {
        margin-top: 1;
        border: solid $panel;
        padding: 0 1;
    }
    ConfirmDeleteScreen #confirm-scroll:focus {
        border: solid $accent;
    }
    ConfirmDeleteScreen #confirm-scroll > Static {
        width: 100%;
        height: auto;
    }
    ConfirmDeleteScreen #confirm-warn {
        color: $warning;
        padding: 0 1;
        border: solid $warning;
    }
    ConfirmDeleteScreen .-project-danger #confirm-warn {
        color: $error;
        border: thick $error;
        background: $error 20%;
        text-style: bold;
    }
    ConfirmDeleteScreen #confirm-warn.-empty {
        display: none;
    }
    ConfirmDeleteScreen #confirm-prompt {
        margin-top: 1;
        padding: 0 1;
        background: $error 20%;
        text-style: bold;
    }
    ConfirmDeleteScreen .-project-danger #confirm-prompt {
        background: $error 45%;
        color: $text;
    }
    ConfirmDeleteScreen ContextFooter {
        margin-top: 0;
    }
    """

    def __init__(
        self,
        body: str | Content,
        *,
        title: str | Content,
        warnings: list[str] | None = None,
        confirm_verb: str = "Delete",
        project_danger: bool = False,
        memory_toggle: Callable[[bool], tuple[str | Content, str | Content]] | None = None,
    ) -> None:
        super().__init__()
        # ``memory_toggle(keep)`` returns (body, memory line) for the keep state.
        self.memory_toggle = memory_toggle
        self.keep_memory = False
        if memory_toggle is not None:
            body = memory_toggle(False)[0]
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
        title = plain(self._title)
        if self.project_danger:
            title = Content.assemble("⚠ ", title).stylize("bold $error")
            if not self.warnings:
                warn = (
                    "!  This removes the whole project directory and linked "
                    "session fragments."
                )
        dialog_classes = "dialog -project-danger" if self.project_danger else "dialog"
        verb = (
            f"{self.confirm_verb} forever"
            if self.confirm_verb.lower() in {"delete"}
            else self.confirm_verb
        )
        prompt = Content.assemble(
            ("y", "bold"), f"  {verb}     ", ("n", "bold"), " / Esc  Cancel"
        )
        yield Vertical(
            Vertical(
                Static(title, id="confirm-title"),
                Static(
                    plain(self.memory_toggle(False)[1] if self.memory_toggle else ""),
                    id="confirm-memory",
                    classes="" if self.memory_toggle else "-empty",
                ),
                classes="dialog-head",
            ),
            Vertical(
                Static(prompt, id="confirm-prompt"),
                self.footer(),
                classes="dialog-foot",
            ),
            VerticalScroll(
                Static(
                    Content(warn),
                    id="confirm-warn",
                    classes="-empty" if not warn else "",
                ),
                Static(plain(self.body), id="confirm-body"),
                id="confirm-scroll",
                classes="dialog-body",
            ),
            id="confirm-dialog",
            classes=dialog_classes,
        )

    def scroll_view(self) -> ScrollableContainer:
        return self.query_one("#confirm-scroll", VerticalScroll)

    def footer_hidden(self) -> frozenset[str]:
        return frozenset() if self.memory_toggle else frozenset({"toggle_memory"})

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_decline(self) -> None:
        self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def check_action(
        self, action: str, parameters: tuple[object, ...]
    ) -> bool | None:
        if action == "toggle_memory" and self.memory_toggle is None:
            return False
        return super().check_action(action, parameters)

    def action_toggle_memory(self) -> None:
        if self.memory_toggle is None:
            return
        self.keep_memory = not self.keep_memory
        body, line = self.memory_toggle(self.keep_memory)
        self.body = body
        self.query_one("#confirm-body", Static).update(plain(body))
        self.query_one("#confirm-memory", Static).update(plain(line))


class VersionScreen(ScrollKeys, DialogScreen[None]):
    """Detailed version / runtime info."""

    SCOPE = "dialog:version"
    BINDINGS = bindings_for(SCOPE)
    AUTO_FOCUS = "#version-scroll"

    DEFAULT_CSS = """
    VersionScreen #version-scroll {
        margin-top: 1;
    }
    VersionScreen #version-scroll > Static {
        width: 100%;
        height: auto;
    }
    """

    def __init__(self, body: str | Content) -> None:
        super().__init__()
        self.body = body

    def compose(self) -> ComposeResult:
        yield Vertical(
            Vertical(
                Static("[b $accent]aXism version[/]", id="version-title"),
                classes="dialog-head",
            ),
            Vertical(self.footer(), classes="dialog-foot"),
            VerticalScroll(
                Static(plain(self.body), id="version-body"),
                id="version-scroll",
                classes="dialog-body",
            ),
            id="version-dialog",
            classes="dialog",
        )

    def scroll_view(self) -> ScrollableContainer:
        return self.query_one("#version-scroll", VerticalScroll)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_close_version(self) -> None:
        self.dismiss(None)


class RenameSessionScreen(DialogScreen[str | None]):
    """Prompt for a new session title (rename or copy-as)."""

    SCOPE = "dialog:rename"
    BINDINGS = bindings_for(SCOPE)

    DEFAULT_CSS = """
    RenameSessionScreen #rename-input {
        margin-top: 1;
    }
    """

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
            Vertical(
                Static(
                    Content.assemble(
                        (self.heading, "bold"), f"  {self.session_short}…"
                    ),
                    id="rename-title",
                    classes="dialog-title",
                ),
                Static(self.hint, id="rename-hint", classes="dialog-hint", markup=False),
                classes="dialog-head",
            ),
            Vertical(self.footer(), classes="dialog-foot"),
            Vertical(
                Input(
                    value=self.current_title,
                    placeholder="Session title",
                    id="rename-input",
                ),
                classes="dialog-body",
            ),
            id="rename-dialog",
            classes="dialog",
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


class PickerScreen(OptionListKeys, DialogScreen[str | None]):
    """A dialog around one pick list that the navigation keys move through."""

    LIST_ID: ClassVar[str] = ""

    DEFAULT_CSS = """
    PickerScreen .dialog-body {
        margin-top: 1;
        border: solid $primary;
    }
    """

    def option_list(self) -> OptionList:
        return self.query_one(f"#{self.LIST_ID}", OptionList)


class ThemePickerScreen(PickerScreen):
    """Pick a Textual theme from a scrollable list."""

    SCOPE = "dialog:theme"
    BINDINGS = bindings_for(SCOPE)
    LIST_ID = "theme-list"

    DEFAULT_CSS = """
    ThemePickerScreen #theme-picker-dialog {
        border: thick $accent;
    }
    """

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
            Vertical(
                Static(
                    f"[b]Themes[/b]  ({len(self.theme_names)})",
                    id="theme-picker-title",
                    classes="dialog-title",
                ),
                classes="dialog-head",
            ),
            Vertical(self.footer(), classes="dialog-foot"),
            OptionList(*options, id="theme-list", classes="dialog-body", markup=False),
            id="theme-picker-dialog",
            classes="dialog",
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

    def action_cancel_picker(self) -> None:
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


class MoveTargetScreen(PickerScreen):
    """Pick an existing project or type a destination path."""

    SCOPE = "dialog:move"
    BINDINGS = bindings_for(SCOPE)
    LIST_ID = "move-list"

    DEFAULT_CSS = """
    MoveTargetScreen #move-input {
        margin-top: 1;
    }
    """

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
            Vertical(
                Static(
                    Content.styled(self._title, "bold"),
                    id="move-title",
                    classes="dialog-title",
                ),
                Static(self.hint, id="move-hint", classes="dialog-hint", markup=False),
                classes="dialog-head",
            ),
            Vertical(
                Input(
                    placeholder="/absolute/path/to/project",
                    id="move-input",
                ),
                self.footer(),
                classes="dialog-foot",
            ),
            OptionList(*options, id="move-list", classes="dialog-body", markup=False),
            id="move-dialog",
            classes="dialog",
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


class AgentPickerScreen(PickerScreen):
    """Pick a coding-agent CLI for memory-merge handoff, or save brief only."""

    SCOPE = "dialog:agent"
    BINDINGS = bindings_for(SCOPE)
    LIST_ID = "agent-list"

    DEFAULT_CSS = """
    AgentPickerScreen #agent-picker-dialog {
        border: thick $accent;
    }
    """

    def __init__(self, title: str, choices: list[tuple[str, str]]) -> None:
        super().__init__()
        self._title = title
        self.choices = choices  # (id, label)

    def compose(self) -> ComposeResult:
        options = [Option(label, id=aid) for aid, label in self.choices]
        yield Vertical(
            Vertical(
                Static(
                    Content.styled(self._title, "bold"),
                    id="agent-picker-title",
                    classes="dialog-title",
                ),
                Static(
                    "Finish MEMORY.md merge with an agent, or save the brief only.",
                    id="agent-picker-hint",
                    classes="dialog-hint",
                ),
                classes="dialog-head",
            ),
            Vertical(self.footer(), classes="dialog-foot"),
            OptionList(*options, id="agent-list", classes="dialog-body", markup=False),
            id="agent-picker-dialog",
            classes="dialog",
        )

    def on_mount(self) -> None:
        self.query_one("#agent-list", OptionList).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cancel_picker(self) -> None:
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
