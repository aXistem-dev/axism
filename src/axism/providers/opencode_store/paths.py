"""OpenCode 2 data and state folder resolution."""

from __future__ import annotations

import os
import re
from pathlib import Path

DB_NAME = "opencode.db"
SESSION_ID_RE = re.compile(r"ses_[A-Za-z0-9]+")


def _xdg(var: str, fallback: Path) -> Path:
    value = os.environ.get(var, "").strip()
    base = Path(value).expanduser() if value else fallback
    return base / "opencode"


def default_data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", Path.home() / ".local" / "share")


def default_state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", Path.home() / ".local" / "state")


def db_path(root: Path) -> Path:
    return root / DB_NAME


def is_default_root(root: Path) -> bool:
    try:
        return root.expanduser().resolve() == default_data_dir().resolve()
    except OSError:
        return False


def is_session_id(s: str) -> bool:
    return isinstance(s, str) and bool(SESSION_ID_RE.fullmatch(s))


def short_id(session_id: str) -> str:
    return session_id[:12]
