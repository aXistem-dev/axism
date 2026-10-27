"""aXism: Agent Interactive Session Manager."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

__version__ = "0.1.1"


def _commit_from_build_info() -> str:
    try:
        from axism._build_info import COMMIT
    except ImportError:
        return ""
    return (COMMIT or "").strip()


def _is_axism_checkout(root: Path) -> bool:
    """True when ``root`` is a source tree whose pyproject declares ``axism``."""
    try:
        text = (root / "pyproject.toml").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return re.search(r'^name\s*=\s*["\']axism["\']\s*$', text, re.MULTILINE) is not None


def _commit_from_git(package_dir: Path | None = None) -> str:
    """Short hash, only when the package lives in a source checkout of aXism."""
    here = package_dir if package_dir is not None else Path(__file__).resolve().parent
    root = here.resolve().parents[1]
    if not (root / ".git").exists() or not _is_axism_checkout(root):
        return ""
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


def _resolve_commit() -> str:
    return _commit_from_build_info() or _commit_from_git()


__commit__ = _resolve_commit()


def version_string() -> str:
    """Human-facing version, with short commit when known (e.g. ``0.1.0+a1b2c3d``)."""
    if __commit__:
        return f"{__version__}+{__commit__}"
    return __version__
