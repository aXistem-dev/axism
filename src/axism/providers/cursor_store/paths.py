"""Cursor data locations and the slug / bucket rules that name them.

Cursor keeps agent data in the same place on Linux and macOS
(``$CURSOR_DATA_DIR`` or ``~/.cursor``); only the desktop Electron profile
differs per OS, and that holds no agent transcripts.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

# Directories under projects/ that are not workspace slugs.
NON_PROJECT_DIRS = frozenset({"canvases", "mcps", "terminals", "uploads"})

TRANSCRIPTS_DIRNAME = "agent-transcripts"
TITLE_SIDECAR = "axism-title.json"

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def cursor_data_dir() -> Path:
    """Agent data root: ``$CURSOR_DATA_DIR`` or ``~/.cursor``."""
    override = os.environ.get("CURSOR_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".cursor").resolve()


def projects_dir(root: Path) -> Path:
    return root / "projects"


def chats_dir(root: Path) -> Path:
    return root / "chats"


def is_chat_id(name: str) -> bool:
    return bool(_UUID_RE.match(name))


def slugify_path(path: str | Path) -> str:
    """Name Cursor gives a workspace directory under ``projects/``.

    Non-alphanumeric runs collapse to a single dash, with no leading or
    trailing dash (unlike Claude Code, which keeps the leading dash).
    """
    text = str(path)
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text)
    return slug.strip("-")


def cwd_bucket(cwd: str | Path) -> str:
    """CLI chats live under ``chats/<md5 of the absolute cwd>/``."""
    resolved = str(Path(cwd).expanduser())
    return hashlib.md5(resolved.encode("utf-8")).hexdigest()


def decode_slug(slug: str, *, probe: bool = True) -> str:
    """Best-effort reverse of :func:`slugify_path`.

    The slug is lossy: ``-``, ``_``, ``.`` and ``/`` all became dashes. When
    ``probe`` is set we walk the filesystem and match each level against the
    slugified names of real directories, which recovers paths like
    ``Cardputer-ABS-B-Radar`` or ``my_project``.
    """
    if not slug:
        return ""
    parts = [p for p in slug.split("-") if p]
    if not parts:
        return ""
    fallback = "/" + "/".join(parts)
    if not probe:
        return fallback

    current = Path("/")
    rest = "-".join(parts)
    while rest:
        best_slug = ""
        best_dir: Path | None = None
        try:
            children = list(current.iterdir())
        except OSError:
            break
        for child in children:
            if not child.is_dir():
                continue
            child_slug = slugify_path(child.name)
            if not child_slug:
                continue
            if rest == child_slug or rest.startswith(child_slug + "-"):
                if len(child_slug) > len(best_slug):
                    best_slug, best_dir = child_slug, child
        if best_dir is None:
            break
        current = best_dir
        rest = rest[len(best_slug):].lstrip("-")

    if rest:
        remaining = [p for p in rest.split("-") if p]
        return str(current.joinpath(*remaining)) if remaining else str(current)
    return str(current)


def known_cwd_candidates(root: Path) -> list[str]:
    """Workspace paths we can name, used to reverse CLI chat buckets."""
    candidates: list[str] = [str(Path.home())]
    pdir = projects_dir(root)
    if pdir.is_dir():
        for child in sorted(pdir.iterdir()):
            if not child.is_dir() or child.name in NON_PROJECT_DIRS:
                continue
            decoded = decode_slug(child.name)
            if decoded:
                candidates.append(decoded)
    seen: set[str] = set()
    unique: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


def bucket_to_cwd(root: Path) -> dict[str, str]:
    """Map ``chats/<md5>`` bucket names back to workspace paths we recognise."""
    return {cwd_bucket(cwd): cwd for cwd in known_cwd_candidates(root)}


def agent_stores_dir() -> Path:
    """Per-conversation agent store files (outside the Cursor data dir)."""
    base = os.environ.get("XDG_STATE_HOME", "").strip()
    state = Path(base).expanduser() if base else Path.home() / ".local" / "state"
    return state / "cursor" / "agent-stores" / "cursor_agent_stores"
