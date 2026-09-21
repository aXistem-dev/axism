"""On-disk fragments belonging to a single Cursor chat."""

from __future__ import annotations

from pathlib import Path

from axism.fragments import Fragment, FragmentInventory
from axism.paths import path_size
from axism.providers.cursor_store.discover import locate_sessions
from axism.providers.cursor_store.paths import TRANSCRIPTS_DIRNAME, agent_stores_dir


def _add(out: list[Fragment], kind: str, path: Path, *, optional: bool = False) -> None:
    try:
        if not path.exists():
            return
    except OSError:
        return
    out.append(
        Fragment(
            kind=kind,
            path=path.resolve(),
            size_bytes=path_size(path),
            optional=optional,
        )
    )


def collect_fragments(
    root: Path, session_id: str, *, project_slug: str | None = None
) -> FragmentInventory:
    """Chat directory plus the agent store kept outside the Cursor data dir."""
    inv = FragmentInventory(session_id=session_id, project_slug=project_slug)
    for directory in locate_sessions(root, session_id):
        is_agent = directory.parent.name == TRANSCRIPTS_DIRNAME
        _add(
            inv.fragments,
            "transcript_dir" if is_agent else "cli_chat_dir",
            directory,
        )
    _add(inv.fragments, "agent_store", agent_stores_dir() / session_id, optional=True)
    return inv
