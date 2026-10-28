"""The key registry: one entry per app action, the single source for its keys.

Bindings are generated from it; the footer, help and command palette read the
same entries, so they cannot disagree with what a key really does.

Scopes say where an action applies:

* ``main`` — anywhere on the main screen (any pane);
* ``projects`` / ``sessions`` / ``details`` — that pane of the main screen;
* ``filter`` — the sessions filter input;
* ``reader`` / ``help`` — the full-screen reader and the help screen;
* ``dialog:<name>`` — a dialog; ``settings`` — the Settings screen.

Uppercase letters are aliases of their lowercase key, except ``G`` (last row)
and ``T`` (next theme), which have their own function.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.binding import Binding

GROUPS = (
    "Everywhere",
    "Projects",
    "Sessions",
    "Details & reader",
    "Filter",
    "Dialogs",
    "Settings",
)

SCOPES = frozenset(
    {
        "main",
        "projects",
        "sessions",
        "details",
        "filter",
        "reader",
        "help",
        "dialog:confirm",
        "dialog:quit",
        "dialog:rename",
        "dialog:move",
        "dialog:agent",
        "dialog:theme",
        "dialog:version",
        "settings",
    }
)


@dataclass(frozen=True)
class KeyAction:
    """One action and every key bound to it."""

    id: str
    keys: tuple[str, ...]
    label: str
    description: str
    group: str
    scopes: frozenset[str]
    footer_priority: int | None = None
    palette: bool = True
    priority: bool = False
    # False: the key belongs to a widget (an Input's Enter); it is listed in the
    # help but not bound, so the widget keeps handling it.
    bound: bool = True
    # Per-scope overrides: label shown where the scope differs from ``label``
    # (the quit dialog's "Quit" / "Stay"), the key text the footer shows
    # ("j/k" for a pair of keys), and the scopes whose footer lists the action
    # (empty: every scope the action belongs to).
    scope_labels: tuple[tuple[str, str], ...] = ()
    footer_keys: tuple[tuple[str, str], ...] = ()
    footer_scopes: frozenset[str] = frozenset()

    def label_in(self, scope: str) -> str:
        """The label for ``scope``."""
        return dict(self.scope_labels).get(scope, self.label)

    def footer_keys_in(self, scope: str) -> str:
        """The key text the footer shows for ``scope``."""
        return dict(self.footer_keys).get(scope, key_display(self.keys[0]))

    def footer_priority_in(self, scope: str) -> int | None:
        """Footer position in ``scope`` (``None``: not listed there)."""
        if self.footer_scopes and scope not in self.footer_scopes:
            return None
        return self.footer_priority


def _scopes(*names: str) -> frozenset[str]:
    return frozenset(names)


# Panes plus the scroll views and dialog lists that share the movement keys.
_MOVE = _scopes(
    "projects", "sessions", "details", "reader", "help",
    "dialog:confirm", "dialog:version", "dialog:move", "dialog:agent", "dialog:theme",
    "settings",
)
# The reader and the help screen list their scroll keys in the footer.
_SCROLL_FOOTER = _scopes("reader", "help")
_LISTS = _scopes("projects", "sessions", "details")
_SESSIONS = _scopes("sessions", "details")
# Dialogs that ask a yes / no question.
_QUESTIONS = _scopes("dialog:confirm", "dialog:quit")
# Dialogs where Enter applies the choice or the typed text.
_APPLY = _scopes("dialog:rename", "dialog:move", "dialog:agent", "dialog:theme")
# Dialogs that Esc cancels.
_CANCEL = _QUESTIONS | _APPLY | _scopes("settings")

REGISTRY: tuple[KeyAction, ...] = (
    # --- Everywhere: movement -------------------------------------------------
    KeyAction(
        "cursor_down", ("down", "j", "J"), "Down",
        "Move down one row, or scroll down one line",
        "Everywhere", _MOVE, priority=True,
        footer_priority=1, footer_scopes=_SCROLL_FOOTER,
        footer_keys=(("reader", "j/k"), ("help", "j/k")),
        scope_labels=(("reader", "Scroll"), ("help", "Scroll")),
    ),
    KeyAction(
        "cursor_up", ("up", "k", "K"), "Up",
        "Move up one row, or scroll up one line",
        "Everywhere", _MOVE, priority=True,
    ),
    KeyAction(
        "cursor_first", ("home", "g"), "First",
        "Jump to the first row, or the top of the text",
        "Everywhere", _MOVE, priority=True,
        footer_priority=2, footer_scopes=_SCROLL_FOOTER,
        footer_keys=(("reader", "g/G"), ("help", "g/G")),
        scope_labels=(("reader", "Top/End"), ("help", "Top/End")),
    ),
    KeyAction(
        "cursor_last", ("end", "G"), "Last",
        "Jump to the last row, or the bottom of the text",
        "Everywhere", _MOVE, priority=True,
    ),
    KeyAction(
        "half_page_down", ("ctrl+d",), "Half page down",
        "Move down half a page", "Everywhere", _MOVE, priority=True,
    ),
    KeyAction(
        "half_page_up", ("ctrl+u",), "Half page up",
        "Move up half a page", "Everywhere", _MOVE, priority=True,
    ),
    KeyAction(
        "page_down", ("pagedown",), "Page down",
        "Move down one page", "Everywhere", _MOVE, priority=True,
        footer_priority=3, footer_scopes=_SCROLL_FOOTER,
        footer_keys=(("reader", "PgDn/PgUp"), ("help", "PgDn/PgUp")),
        scope_labels=(("reader", "Page"), ("help", "Page")),
    ),
    KeyAction(
        "page_up", ("pageup",), "Page up",
        "Move up one page", "Everywhere", _MOVE, priority=True,
    ),
    # --- Everywhere: panes ----------------------------------------------------
    KeyAction(
        "pane_left", ("left", "h", "H"), "Pane left",
        "Focus the pane to the left", "Everywhere", _scopes("main"), priority=True,
    ),
    KeyAction(
        "pane_right", ("right", "l", "L"), "Pane right",
        "Focus the pane to the right", "Everywhere", _scopes("main"), priority=True,
    ),
    KeyAction(
        "next_pane", ("tab",), "Pane",
        "Focus the next pane (projects → sessions → details)",
        "Everywhere", _scopes("main"), footer_priority=80, priority=True,
    ),
    KeyAction(
        "previous_pane", ("shift+tab",), "Pane back",
        "Focus the previous pane", "Everywhere", _scopes("main"), priority=True,
    ),
    KeyAction(
        "enter_pane", ("enter",), "Enter",
        "Go into the highlighted item: projects → sessions → details → reader",
        "Everywhere", _scopes("main"), priority=True,
    ),
    # --- Everywhere: app ------------------------------------------------------
    KeyAction(
        "refresh", ("r", "R"), "Refresh",
        "Reload projects, sessions and running state",
        "Everywhere", _scopes("main"),
    ),
    KeyAction(
        "show_settings", ("comma",), "Settings",
        "Open Settings (agents and their folders)",
        "Everywhere", _scopes("main"), footer_priority=90, priority=True,
    ),
    KeyAction(
        "show_version", ("v", "V"), "Version",
        "Show version and runtime details",
        "Everywhere", _scopes("main"), priority=True,
    ),
    KeyAction(
        "pick_theme", ("t",), "Themes",
        "Pick a theme", "Everywhere", _scopes("main"),
    ),
    KeyAction(
        "next_theme", ("T",), "Next theme",
        "Switch to the next theme", "Everywhere", _scopes("main"),
    ),
    KeyAction(
        "command_palette", ("ctrl+p",), "Commands",
        "Open the command palette: run any action by name",
        # Works inside text fields too; not listed in the palette itself.
        "Everywhere", _scopes("main"), palette=False, priority=True,
    ),
    KeyAction(
        "show_help", ("question_mark",), "Help",
        "Show every key", "Everywhere", _scopes("main"), footer_priority=60,
    ),
    KeyAction(
        "quit", ("q", "Q"), "Quit",
        "Quit aXism", "Everywhere", _scopes("main"), footer_priority=70,
    ),
    # --- Projects (pane-aware: projects pane acts on projects) ----------------
    KeyAction(
        "toggle_select", ("space",), "Mark",
        "Mark or unmark the highlighted project or session",
        "Projects", _scopes("projects", "sessions"), footer_priority=10,
    ),
    KeyAction(
        "select_all", ("a", "A"), "Mark all",
        "Mark every project, or every listed session of the project",
        "Projects", _LISTS,
    ),
    KeyAction(
        "clear_select", ("c", "C"), "Clear",
        "Clear all marks (projects and sessions)",
        "Projects", _scopes("main"), priority=True,
    ),
    KeyAction(
        "move_items", ("m", "M"), "Move",
        "Move the marked or highlighted projects or sessions to another folder",
        "Projects", _LISTS, footer_priority=40, priority=True,
    ),
    KeyAction(
        "delete_items", ("d", "D", "delete"), "Delete",
        "Delete the marked or highlighted projects or sessions",
        "Projects", _LISTS, footer_priority=50, priority=True,
    ),
    # --- Sessions -------------------------------------------------------------
    KeyAction(
        "resume_session", ("o", "O"), "Open",
        "Open the highlighted session in its agent",
        # Also listed (dimmed) in the projects pane, so it is easy to find.
        "Sessions", _LISTS, footer_priority=20,
    ),
    KeyAction(
        "stop_sessions", ("s", "S"), "Stop",
        "Stop the marked or highlighted running sessions",
        "Sessions", _SESSIONS, footer_priority=30, priority=True,
    ),
    KeyAction(
        "rename_session", ("n", "N"), "Rename",
        "Rename the highlighted session",
        "Sessions", _SESSIONS, footer_priority=35, priority=True,
    ),
    KeyAction(
        "focus_session_filter", ("slash",), "Filter",
        "Filter the session list", "Sessions", _scopes("main"),
    ),
    KeyAction(
        "cycle_session_sort", ("full_stop",), "Sort",
        "Change the session sort order (updated / title / size)",
        "Sessions", _SESSIONS,
    ),
    # --- Details & reader / filter --------------------------------------------
    KeyAction(
        "back", ("escape",), "Back",
        "Details: back to the sessions list · filter: clear it and leave",
        "Details & reader", _scopes("details", "filter"), footer_priority=55,
    ),
    KeyAction(
        "close_reader", ("escape", "q", "Q"), "Back",
        "Reader: back to the details pane",
        "Details & reader", _scopes("reader"),
        footer_priority=90, palette=False, priority=True,
    ),
    KeyAction(
        "filter_done", ("enter",), "Done",
        "Filter: done typing, go to the filtered sessions list",
        # The filter Input's own Enter (submit); listed, not bound.
        "Filter", _scopes("filter"), palette=False, bound=False,
    ),
    # --- Dialogs (keys of the dialog screens; never in the palette) -----------
    KeyAction(
        "confirm", ("y", "Y"), "Yes",
        "Yes: go ahead (delete, stop, move, copy or quit)",
        "Dialogs", _QUESTIONS, footer_priority=10, palette=False, priority=True,
        scope_labels=(("dialog:quit", "Quit"),),
    ),
    KeyAction(
        "decline", ("n", "N"), "No",
        "No: cancel the question", "Dialogs", _QUESTIONS,
        footer_priority=20, palette=False, priority=True,
        scope_labels=(("dialog:quit", "Stay"),),
    ),
    KeyAction(
        "toggle_memory", ("m", "M"), "Memory",
        "Delete project: keep or delete its memory/ folder",
        "Dialogs", _scopes("dialog:confirm"),
        footer_priority=30, palette=False, priority=True,
    ),
    KeyAction(
        "apply", ("enter",), "Apply",
        "Apply: the new title, the move target, the agent or the theme",
        "Dialogs", _APPLY, footer_priority=10, palette=False, priority=True,
    ),
    KeyAction(
        "focus_path", ("tab",), "Path",
        "Move: go to the path field",
        "Dialogs", _scopes("dialog:move"),
        footer_priority=30, palette=False, priority=True,
    ),
    KeyAction(
        "focus_list", ("shift+tab",), "List",
        "Move and Settings: back to the list",
        "Dialogs", _scopes("dialog:move", "settings"), palette=False, priority=True,
    ),
    KeyAction(
        "cancel", ("escape",), "Cancel",
        "Close without changing anything (q works in pickers, Settings, help and version)",
        "Dialogs", _CANCEL, footer_priority=90, palette=False, priority=True,
        scope_labels=(("dialog:quit", "Stay"),),
    ),
    KeyAction(
        "cancel_picker", ("q", "Q"), "Cancel",
        "Close without changing anything (q works in pickers, Settings, help and version)",
        "Dialogs", _scopes("dialog:agent", "dialog:theme", "settings"),
        palette=False, priority=True,
    ),
    KeyAction(
        "close", ("escape", "q", "Q"), "Close",
        "Close without changing anything (q works in pickers, Settings, help and version)",
        "Dialogs", _scopes("help", "dialog:version"),
        footer_priority=90, palette=False, priority=True,
    ),
    KeyAction(
        "close_help", ("question_mark",), "Close",
        "Close the help", "Dialogs", _scopes("help"), palette=False, priority=True,
    ),
    KeyAction(
        "close_version", ("v", "V"), "Close",
        "Close the version window",
        "Dialogs", _scopes("dialog:version"), palette=False, priority=True,
    ),
    # --- Settings -------------------------------------------------------------
    KeyAction(
        "toggle_enabled", ("space",), "On/off",
        "Switch the highlighted agent on or off (its sessions join the app)",
        "Settings", _scopes("settings"), footer_priority=10, palette=False,
    ),
    KeyAction(
        "set_default", ("asterisk",), "Default",
        "Make the highlighted agent the CLI default (commands without --provider)",
        "Settings", _scopes("settings"), footer_priority=20, palette=False,
    ),
    KeyAction(
        "save", ("enter",), "Save",
        "Save the settings and close",
        "Settings", _scopes("settings"), footer_priority=30, palette=False, priority=True,
    ),
    KeyAction(
        "focus_folder", ("tab",), "Folder",
        "Settings: go to the folder field",
        "Settings", _scopes("settings"), footer_priority=40, palette=False, priority=True,
    ),
)

_BY_ID = {action.id: action for action in REGISTRY}

_KEY_NAMES = {
    "question_mark": "?",
    "full_stop": ".",
    "slash": "/",
    "comma": ",",
    "space": "Space",
    "escape": "Esc",
    "enter": "Enter",
    "tab": "Tab",
    "delete": "Del",
    "backspace": "Backspace",
    "home": "Home",
    "end": "End",
    "pageup": "PgUp",
    "pagedown": "PgDn",
    "up": "↑",
    "down": "↓",
    "left": "←",
    "right": "→",
    "asterisk": "*",
}


def find(action_id: str) -> KeyAction:
    """The registry entry for ``action_id`` (``KeyError`` if unknown)."""
    return _BY_ID[action_id]


def actions_for(scope: str) -> list[KeyAction]:
    """Registry entries that apply in ``scope``, in registry order."""
    return [action for action in REGISTRY if scope in action.scopes]


def help_sections() -> list[tuple[str, list[KeyAction]]]:
    """Registry entries grouped for the help screen, in ``GROUPS`` order.

    Each action is listed once, under its ``group``; empty groups are left out.
    """
    sections = [
        (group, [action for action in REGISTRY if action.group == group])
        for group in GROUPS
    ]
    return [(group, actions) for group, actions in sections if actions]


def bindings_for(*scopes: str) -> list[Binding]:
    """One hidden Binding per key of every bound action in any of ``scopes``."""
    wanted = set(scopes)
    bindings: list[Binding] = []
    for action in REGISTRY:
        if not action.bound or not action.scopes & wanted:
            continue
        for key in action.keys:
            bindings.append(
                Binding(
                    key,
                    action.id,
                    action.label,
                    show=False,
                    priority=action.priority,
                )
            )
    return bindings


def key_display(key: str) -> str:
    """Human form of a Textual key name: ``ctrl+d`` → ``Ctrl+D``, ``delete`` → ``Del``."""
    *mods, base = key.split("+")
    if not mods:
        return _KEY_NAMES.get(base, base)
    shown = _KEY_NAMES.get(base, base.upper() if len(base) == 1 else base.capitalize())
    return "+".join([*(m.capitalize() for m in mods), shown])
