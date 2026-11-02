"""Resolve Claude Code config paths without hardcoding machine identity."""

from __future__ import annotations

import errno
import os
import re
import stat
from pathlib import Path

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


# stat errors that mean "nothing there", as Path.exists treated them up to 3.13.
_ABSENT_ERRNOS = frozenset({errno.ENOENT, errno.ENOTDIR, errno.EBADF, errno.ELOOP})


def _stat_or_none(path: Path) -> os.stat_result | None:
    """``os.stat`` result, ``None`` when absent; other errors (``PermissionError``) raise.

    ``Path.exists``/``is_file``/``is_dir`` return ``False`` for every error from
    Python 3.14 on, so a locked folder would look empty. These probes keep the
    error visible on every version.
    """
    try:
        return os.stat(path)
    except OSError as exc:
        if exc.errno in _ABSENT_ERRNOS:
            return None
        raise


def exists_strict(path: Path) -> bool:
    """``Path.exists`` that raises ``PermissionError`` when it cannot tell."""
    return _stat_or_none(path) is not None


def is_file_strict(path: Path) -> bool:
    """``Path.is_file`` that raises ``PermissionError`` when it cannot tell."""
    st = _stat_or_none(path)
    return st is not None and stat.S_ISREG(st.st_mode)


def listable_dir(path: Path) -> bool:
    """Whether ``path`` is a folder whose contents can be listed.

    ``False`` when it does not exist or is not a folder. A folder that exists
    but cannot be read raises ``PermissionError``, on every Python version
    (``Path.is_dir`` hides that error on newer ones).
    """
    st = _stat_or_none(path)
    if st is None or not stat.S_ISDIR(st.st_mode):
        return False
    if not os.access(path, os.R_OK | os.X_OK):
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(path))
    return True


def readable_or_absent(path: Path) -> bool:
    """``False`` when ``path`` exists but its folder contents cannot be read."""
    try:
        listable_dir(path)
    except OSError:
        return False
    return True


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
    return bool(UUID_RE.fullmatch(name))


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


def strictly_inside(path: Path, roots: list[Path]) -> bool:
    """True when ``path`` lies below (never equal to) one of ``roots``.

    The final component is not resolved, so a symlink counts as the link itself.
    """
    try:
        parent = path.parent.resolve()
    except (OSError, RuntimeError):
        return False
    if path.name in {"", ".", ".."}:
        return False
    target = parent / path.name
    for root in roots:
        try:
            r = root.resolve()
        except (OSError, RuntimeError):
            continue
        if target != r and r in target.parents:
            return True
    return False
