"""Best-effort liveness for Cursor chats.

Cursor has no live-session registry like Claude Code's ``sessions/*.json``, so
a chat counts as live only while a ``cursor-agent`` process mentions its id.
"""

from __future__ import annotations

from axism.live import LiveSession
from axism.providers.common import find_agent_sessions

BINARIES = {"cursor-agent", "cursor"}


def merge_live(session_ids: set[str]) -> dict[str, LiveSession]:
    """Map chat id → live session for ids named in a running agent process."""
    return {
        session_id: LiveSession(
            session_id=session_id,
            kind="interactive",
            state="running",
            pid=pid,
            source="filesystem",
        )
        for session_id, pid in find_agent_sessions(session_ids, BINARIES).items()
    }
