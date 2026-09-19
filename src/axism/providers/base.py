"""Provider protocol for AI coding-agent session backends."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from axism.discover import ProjectInfo, SessionMeta
from axism.live import LiveSession


@runtime_checkable
class SessionProvider(Protocol):
    """Discover, enrich, resume, and stop sessions for one agent tool."""

    name: str
    label: str

    def config_root(self) -> Path:
        """Root directory where this provider stores sessions."""
        ...

    def discover(self, root: Path | None = None) -> list[ProjectInfo]:
        ...

    def merge_live(
        self, root: Path | None = None, *, use_cli: bool = True
    ) -> dict[str, LiveSession]:
        ...

    def resume(self, session: SessionMeta, live: LiveSession | None = None) -> None:
        """Replace the current process with attach or resume of ``session``."""
        ...

    def slash_stop(
        self, session_id: str, live: LiveSession | None = None
    ) -> tuple[bool, str]:
        """Stop a live background session (``/stop`` / ``claude stop``)."""
        ...

    def stop(self, session_id: str, live: LiveSession | None = None) -> tuple[bool, str]:
        ...
