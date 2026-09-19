"""Resolve Claude Code config paths without hardcoding machine identity."""

from __future__ import annotations

import os
import re
from pathlib import Path

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def config_root() -> Path:
    """Return Claude Code config root ($CLAUDE_CONFIG_DIR or ~/.claude)."""
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".claude").resolve()


def projects_dir(root: Path | None = None) -> Path:
    return (root or config_root()) / "projects"


def encode_project_slug(cwd: str | Path) -> str:
    """Encode an absolute cwd the way Claude Code names project dirs.

    Non-alphanumeric characters become ``-``. Leading slash becomes a leading dash.
    If ``CLAUDE_CODE_PROJECT_DIR_NAME`` is set (with ``CLAUDE_CONFIG_DIR``), use it.
    """
    if os.environ.get("CLAUDE_CONFIG_DIR") and os.environ.get(
        "CLAUDE_CODE_PROJECT_DIR_NAME"
    ):
        return os.environ["CLAUDE_CODE_PROJECT_DIR_NAME"]
    text = str(cwd)
    if not text:
        return ""
    # Claude Code: replace each non [A-Za-z0-9] with '-'
    return re.sub(r"[^A-Za-z0-9]", "-", text)


def decode_project_slug(slug: str) -> str:
    """Best-effort reverse of encode: leading ``-`` → ``/``, remaining ``-`` → ``/``.

    Ambiguous (underscores and other chars all became dashes), so this is display-only.
    """
    if not slug:
        return ""
    if slug.startswith("-"):
        return "/" + slug[1:].replace("-", "/")
    return slug.replace("-", "/")


def short_session_id(session_id: str) -> str:
    return session_id[:8].lower()


def is_session_uuid(name: str) -> bool:
    return bool(UUID_RE.match(name))


def tmp_claude_roots() -> list[Path]:
    """Candidate ephemeral scratch roots used by Claude Code."""
    uid = os.getuid() if hasattr(os, "getuid") else None
    candidates: list[Path] = []
    tmpdir = os.environ.get("TMPDIR") or os.environ.get("TMP") or "/tmp"
    bases = [Path(tmpdir), Path("/tmp")]
    seen: set[Path] = set()
    for base in bases:
        try:
            base = base.resolve()
        except OSError:
            continue
        if base in seen:
            continue
        seen.add(base)
        if uid is not None:
            candidates.append(base / f"claude-{uid}")
        candidates.append(base / "claude")
    return candidates


def path_size(path: Path) -> int:
    """Total size of a file or directory tree in bytes."""
    try:
        if path.is_file():
            return path.stat().st_size
        if path.is_dir():
            total = 0
            for root, _dirs, files in os.walk(path, followlinks=False):
                for name in files:
                    fp = Path(root) / name
                    try:
                        total += fp.stat().st_size
                    except OSError:
                        pass
            return total
    except OSError:
        return 0
    return 0
