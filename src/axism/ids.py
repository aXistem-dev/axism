"""Session id matching shared by every provider."""

from __future__ import annotations

from collections.abc import Iterable


class AmbiguousSessionId(ValueError):
    """A prefix matched more than one session."""

    def __init__(self, needle: str, matches: list[str]) -> None:
        self.needle = needle
        self.matches = sorted(matches)
        super().__init__(f"ambiguous id '{needle}' matches {len(self.matches)} sessions")


def match_session_id(candidates: Iterable[str], needle: str) -> str | None:
    """Return the full id for ``needle``: exact match, else the unique prefix match.

    Case-insensitive. Raises ``AmbiguousSessionId`` when several ids share the prefix.
    """
    key = needle.strip().lower()
    if not key:
        return None
    ids = list(dict.fromkeys(candidates))
    for sid in ids:
        if sid.lower() == key:
            return sid
    found = [sid for sid in ids if sid.lower().startswith(key)]
    if len(found) > 1:
        raise AmbiguousSessionId(needle, found)
    return found[0] if found else None
