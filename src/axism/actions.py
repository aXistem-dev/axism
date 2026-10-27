"""Classification of backend action lines (shared by the CLI and the TUI)."""

from __future__ import annotations

_FAILURE_PREFIXES = ("aborted", "refused:", "error")
_FAILURE_MARKERS = (
    " failed",
    "still alive",
    "no longer runs",
    "stop/kill/rm failed",
)


def is_failure_line(line: str) -> bool:
    """True for a backend action line that reports a failed or refused step."""
    if line.startswith(_FAILURE_PREFIXES):
        return True
    low = line.lower()
    return any(marker in low for marker in _FAILURE_MARKERS)


def actions_failed(actions: list[str]) -> bool:
    return any(is_failure_line(a) for a in actions)


def first_failure(actions: list[str]) -> str:
    """The first failure line, which names the cause before any follow-up abort."""
    return next(
        (a for a in actions if is_failure_line(a)),
        actions[-1] if actions else "unknown error",
    )


def project_kept_line(
    total_sessions: int, actions: list[str], *, has_project_dir: bool
) -> str:
    """Abort line for a project delete stopped by a live session that would not stop."""
    stuck = sum(1 for x in actions if x.startswith(_NOT_DELETED_PREFIXES))
    others = max(total_sessions - stuck, 0)
    head = (
        "aborted: project folder kept because a live session could not be stopped"
        if has_project_dir
        else "aborted: a live session could not be stopped"
    )
    return f"{head}; {others} other session(s) deleted"


STOP_ABORT_PREFIX = "aborted: could not stop live session"
CLI_DELETE_FAILED_PREFIX = "error: hermes sessions delete"
_NOT_DELETED_PREFIXES = (STOP_ABORT_PREFIX, CLI_DELETE_FAILED_PREFIX)
