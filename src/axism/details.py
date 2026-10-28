"""Neutral data types for the details pane and the conversation reader."""

from __future__ import annotations

from dataclasses import dataclass


class TranscriptUnavailable(RuntimeError):
    """A session's conversation could not be read; ``reason`` says why."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class SessionDetails:
    """Facts about one session beyond :class:`SessionMeta` (all optional)."""

    created: float | None = None  # epoch seconds
    turns: int | None = None  # text turns (messages)
    prompts: int | None = None  # of those, the user's
    model: str | None = None
    cost_usd: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    first_prompt: str | None = None
    last_prompt: str | None = None
    last_reply: str | None = None
    note: str = ""  # set when the transcript could not be read
