"""Hermes home resolution.

Hermes stores every session in one SQLite database under its home directory,
which is the same location on Linux and macOS (``~/.hermes``); only Windows
differs. Named profiles are separate homes under ``profiles/`` and are out of
scope here — this provider manages the default profile.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

STATE_DB = "state.db"
HOME_MARKERS = ("config.yaml", ".env", STATE_DB)

# Interactive + imported chats. Cron, messaging platforms, and kanban workers
# stay out of the default inventory (see AGENTS.md extras branches).
DEFAULT_SOURCES = (
    "cli",
    "tui",
    "desktop",
    "webui",
    "oneshot",
    "claude-code",  # hermes sessions import --from claude
    "codex",  # hermes sessions import --from codex
)


def hermes_home() -> Path:
    """``$HERMES_HOME`` or the platform default."""
    override = os.environ.get("HERMES_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return (base / "hermes").resolve()
    return (Path.home() / ".hermes").resolve()


def state_db(root: Path) -> Path:
    return root / STATE_DB


def is_hermes_home(root: Path) -> bool:
    return any((root / marker).exists() for marker in HOME_MARKERS)


def legacy_session_files(root: Path, session_id: str) -> list[Path]:
    """Pre-SQLite dumps and WebUI mirrors that may shadow a session id."""
    return [
        root / "sessions" / f"session_{session_id}.json",
        root / "sessions" / "saved" / f"{session_id}.json",
        root / "webui" / "sessions" / f"{session_id}.json",
    ]
