"""What a Hermes session occupies on disk."""

from __future__ import annotations

from pathlib import Path

from axism.fragments import Fragment, FragmentInventory
from axism.paths import path_size
from axism.providers.hermes_store.discover import connect
from axism.providers.hermes_store.paths import legacy_session_files, state_db


def _message_bytes(db: Path, session_id: str) -> int:
    if not db.is_file():
        return 0
    try:
        con = connect(db)
    except Exception:
        # A locked or malformed database only costs us the size estimate.
        return 0
    try:
        row = con.execute(
            "SELECT SUM(LENGTH(COALESCE(content, ''))) FROM messages "
            "WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    except Exception:
        return 0
    finally:
        con.close()
    return int(row[0] or 0) if row else 0


def collect_fragments(
    root: Path, session_id: str, *, project_slug: str | None = None
) -> FragmentInventory:
    """Database rows (protected, removed via the CLI) plus legacy JSON files."""
    inv = FragmentInventory(session_id=session_id, project_slug=project_slug)
    db = state_db(root)
    if db.is_file():
        # state.db is in PROTECTED_NAMES: shown for size, never unlinked.
        inv.fragments.append(
            Fragment(
                kind="session_rows",
                path=db.resolve(),
                size_bytes=_message_bytes(db, session_id),
            )
        )
    for path in legacy_session_files(root, session_id):
        if path.is_file():
            inv.fragments.append(
                Fragment(
                    kind="legacy_json",
                    path=path.resolve(),
                    size_bytes=path_size(path),
                    optional=True,
                )
            )
    return inv
