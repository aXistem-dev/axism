"""aXism: Agent Interactive Session Manager."""

from __future__ import annotations

import subprocess
from pathlib import Path

__version__ = "0.1.1"


def _commit_from_build_info() -> str:
    try:
        from axism._build_info import COMMIT
    except ImportError:
        return ""
    return (COMMIT or "").strip()


def _commit_from_git() -> str:
    """Short hash when running from a checkout (or any ancestor with .git)."""
    here = Path(__file__).resolve().parent
    for root in (here, *here.parents):
        if not (root / ".git").exists():
            continue
        try:
            out = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--short=7", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return out.stdout.strip()
    return ""


def _resolve_commit() -> str:
    return _commit_from_build_info() or _commit_from_git()


__commit__ = _resolve_commit()


def version_string() -> str:
    """Human-facing version, with short commit when known (e.g. ``0.1.0+a1b2c3d``)."""
    if __commit__:
        return f"{__version__}+{__commit__}"
    return __version__
