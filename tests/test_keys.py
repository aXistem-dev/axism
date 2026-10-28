"""Key registry consistency: one source of truth for the app's keys."""

from __future__ import annotations

import string

import pytest

from axism.tui import keys
from axism.tui.app import SessionManagerApp

MAIN_SCOPES = ("main", "projects", "sessions", "details", "filter")


def _all_scopes() -> set[str]:
    return {scope for action in keys.REGISTRY for scope in action.scopes}


def test_no_duplicate_key_within_a_scope():
    for scope in _all_scopes():
        seen: dict[str, str] = {}
        for action in keys.actions_for(scope):
            for key in action.keys:
                assert key not in seen, (scope, key, seen.get(key), action.id)
                seen[key] = action.id


def test_no_duplicate_key_on_the_main_screen():
    seen: dict[str, str] = {}
    for binding in keys.bindings_for(*MAIN_SCOPES):
        assert seen.get(binding.key, binding.action) == binding.action, binding.key
        seen[binding.key] = binding.action


def test_action_ids_are_unique():
    ids = [action.id for action in keys.REGISTRY]
    assert len(ids) == len(set(ids))


def test_every_action_has_label_description_group():
    for action in keys.REGISTRY:
        assert action.label.strip(), action.id
        assert action.description.strip(), action.id
        assert action.group in keys.GROUPS, action.id
        assert action.keys, action.id
        assert action.scopes, action.id
        assert action.scopes <= keys.SCOPES, (action.id, action.scopes - keys.SCOPES)


def test_uppercase_aliases_except_G_T():
    for action in keys.REGISTRY:
        for key in action.keys:
            if len(key) == 1 and key in string.ascii_lowercase:
                upper = key.upper()
                if upper in {"G", "T"}:
                    continue
                assert upper in action.keys, (action.id, key)


def test_G_and_T_have_their_own_function():
    assert "G" in keys.find("cursor_last").keys
    assert "g" not in keys.find("cursor_last").keys
    assert "T" in keys.find("next_theme").keys
    assert "t" in keys.find("pick_theme").keys
    assert "T" not in keys.find("pick_theme").keys


def test_h_is_not_help():
    help_keys = keys.find("show_help").keys
    assert "h" not in help_keys and "H" not in help_keys
    assert help_keys == ("question_mark",)
    assert "h" in keys.find("pane_left").keys
    assert "l" in keys.find("pane_right").keys


def test_existing_letters_keep_their_meaning():
    expected = {
        "o": "resume_session",
        "s": "stop_sessions",
        "n": "rename_session",
        "m": "move_items",
        "d": "delete_items",
        "space": "toggle_select",
        "a": "select_all",
        "c": "clear_select",
        "slash": "focus_session_filter",
        "full_stop": "cycle_session_sort",
        "comma": "show_settings",
        "r": "refresh",
        "t": "pick_theme",
        "T": "next_theme",
        "v": "show_version",
        "q": "quit",
    }
    by_key = {b.key: b.action for b in keys.bindings_for(*MAIN_SCOPES)}
    for key, action in expected.items():
        assert by_key[key] == action, key


def test_new_navigation_keys():
    by_key = {b.key: b.action for b in keys.bindings_for(*MAIN_SCOPES)}
    assert by_key["j"] == by_key["down"] == "cursor_down"
    assert by_key["k"] == by_key["up"] == "cursor_up"
    assert by_key["g"] == by_key["home"] == "cursor_first"
    assert by_key["G"] == by_key["end"] == "cursor_last"
    assert by_key["ctrl+d"] == "half_page_down"
    assert by_key["ctrl+u"] == "half_page_up"
    assert by_key["pagedown"] == "page_down"
    assert by_key["pageup"] == "page_up"
    assert by_key["h"] == by_key["left"] == "pane_left"
    assert by_key["l"] == by_key["right"] == "pane_right"
    assert by_key["enter"] == "enter_pane"
    assert by_key["escape"] == "back"
    assert by_key["delete"] == "delete_items"


def test_bindings_are_one_per_key_and_hidden():
    bindings = keys.bindings_for(*MAIN_SCOPES)
    for binding in bindings:
        assert binding.show is False
    action = keys.find("delete_items")
    mine = [b for b in bindings if b.action == "delete_items"]
    assert [b.key for b in mine] == list(action.keys)
    assert all(b.priority == action.priority for b in mine)


def test_main_screen_bindings_come_from_the_registry():
    registry_ids = {action.id for action in keys.REGISTRY}
    for binding in SessionManagerApp.BINDINGS:
        assert binding.action in registry_ids, binding.action
    expected = {(b.key, b.action) for b in keys.bindings_for(*MAIN_SCOPES)}
    actual = {(b.key, b.action) for b in SessionManagerApp.BINDINGS}
    assert actual == expected


def test_find_unknown_action_raises():
    with pytest.raises(KeyError):
        keys.find("no_such_action")


@pytest.mark.parametrize(
    ("key", "shown"),
    [
        ("ctrl+d", "Ctrl+D"),
        ("ctrl+u", "Ctrl+U"),
        ("question_mark", "?"),
        ("full_stop", "."),
        ("delete", "Del"),
        ("home", "Home"),
        ("end", "End"),
        ("slash", "/"),
        ("comma", ","),
        ("space", "Space"),
        ("escape", "Esc"),
        ("enter", "Enter"),
        ("tab", "Tab"),
        ("shift+tab", "Shift+Tab"),
        ("pagedown", "PgDn"),
        ("pageup", "PgUp"),
        ("down", "↓"),
        ("up", "↑"),
        ("left", "←"),
        ("right", "→"),
        ("j", "j"),
        ("G", "G"),
    ],
)
def test_key_display(key, shown):
    assert keys.key_display(key) == shown


README_ACTIONS = {
    "Down": "cursor_down",
    "Up": "cursor_up",
    "First row": "cursor_first",
    "Last row": "cursor_last",
    "Half page down": "half_page_down",
    "Half page up": "half_page_up",
    "Pane left": "pane_left",
    "Pane right": "pane_right",
    "Next pane": "next_pane",
    "Previous pane": "previous_pane",
    "Page down": "page_down",
    "Page up": "page_up",
    "Refresh": "refresh",
    "Version": "show_version",
    "Back": "back",
    "Mark or unmark": "toggle_select",
    "Mark all": "select_all",
    "Clear all marks": "clear_select",
    "Open the session in its agent": "resume_session",
    "Stop a running session": "stop_sessions",
    "Rename a session": "rename_session",
    "Move to another project path": "move_items",
    "Delete": "delete_items",
    "Filter sessions": "focus_session_filter",
    "Sort sessions": "cycle_session_sort",
    "Settings": "show_settings",
    "All keys": "show_help",
    "Command palette: run any action by name": "command_palette",
}


def test_readme_key_table_matches_registry():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    section = text.split("## Using the app", 1)[1].split("Capital letters", 1)[0]
    rows = [ln for ln in section.splitlines() if ln.startswith("| `")]
    assert rows
    by_prefix = {
        "Go into": ["enter_pane"],
        "Quit": ["quit"],
        "Pick a theme": ["pick_theme", "next_theme"],
    }
    for line in rows:
        _, key_cell, action_cell, *_ = (c.strip() for c in line.split("|"))
        shown = [k.strip("`") for k in key_cell.split(" / ")]
        if action_cell in README_ACTIONS:
            ids = [README_ACTIONS[action_cell]]
        else:
            matched = [v for p, v in by_prefix.items() if action_cell.startswith(p)]
            assert matched, f"README row not covered by the test: {action_cell!r}"
            ids = matched[0]
        # Every key shown in the row must belong to one of the row's actions.
        bound = {keys.key_display(k) for i in ids for k in keys.find(i).keys}
        for name in shown:
            assert name in bound, (name, ids)
