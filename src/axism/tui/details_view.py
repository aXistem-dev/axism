"""Text of the details pane for one session: facts first, then the last exchange.

Pure functions (no widgets, no provider calls) so the layout is easy to test
with a fixed clock. The result is a ``Content``: aXism's own labels carry the
styles, and every piece of session text (title, folder, prompts, replies,
model, note) goes in as plain text, so it is never read as markup.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from textual.content import Content

from axism.details import SessionDetails
from axism.discover import SessionMeta

# The prompt and reply blocks stay short; the reader shows everything.
BLOCK_LINES = 6
FIRST_PROMPT_LINES = 3
LINE_CHARS = 200
BLOCK_CHARS = 600

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_LABEL_WIDTH = 9


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def relative_age(seconds: float) -> str:
    """``just now``, ``20 min ago``, ``2 h ago``, ``3 days ago``, ``3 weeks ago``, …"""
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} h ago"
    days = hours // 24
    if days < 14:
        return f"{_plural(days, 'day')} ago"
    if days < 60:
        return f"{_plural(days // 7, 'week')} ago"
    if days < 730:
        return f"{_plural(days // 30, 'month')} ago"
    return f"{_plural(days // 365, 'year')} ago"


def readable_time(ts: float, now: datetime) -> str:
    """Local time as ``today 05:01``, ``yesterday 18:22`` or ``3 Oct 2026 09:15``,
    followed by the age: ``(2 h ago)``. ``now`` is an aware local datetime."""
    when = datetime.fromtimestamp(ts).astimezone()
    clock = f"{when:%H:%M}"
    if when.date() == now.date():
        day = f"today {clock}"
    elif when.date() == (now - timedelta(days=1)).date():
        day = f"yesterday {clock}"
    else:
        day = f"{when.day} {_MONTHS[when.month - 1]} {when.year} {clock}"
    return f"{day} ({relative_age((now - when).total_seconds())})"


def trim_block(
    text: str,
    *,
    max_lines: int = BLOCK_LINES,
    line_chars: int = LINE_CHARS,
    max_chars: int = BLOCK_CHARS,
) -> str:
    """At most ``max_lines`` lines and ``max_chars`` characters, ``…`` when cut.

    Leading and trailing blank lines go; runs of blank lines become one.
    """
    lines: list[str] = []
    for raw in text.strip().splitlines():
        line = raw.rstrip()
        if not line.strip() and lines and not lines[-1].strip():
            continue
        lines.append(line)
    cut = len(lines) > max_lines
    lines = lines[:max_lines]
    out: list[str] = []
    used = 0
    for line in lines:
        if len(line) > line_chars:
            line = line[: line_chars - 1] + "…"
        if used + len(line) > max_chars:
            room = max(0, max_chars - used)
            if room:
                out.append(line[:room])
            cut = True
            break
        out.append(line)
        used += len(line) + 1
    result = "\n".join(out)
    if cut:
        result = result.rstrip() + ("\n…" if len(out) == len(lines) else "…")
    return result


def tilde(path: str, home: str) -> str:
    """``path`` with the home folder shown as ``~``."""
    home = home.rstrip("/")
    if home and (path == home or path.startswith(home + "/")):
        return "~" + path[len(home):]
    return path


def fmt_size(n: int) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if x < 1024:
            return f"{x:.0f}{unit}" if unit == "B" else f"{x:.1f}{unit}"
        x /= 1024
    return f"{x:.1f}TB"


def storage_line(session: SessionMeta, *, database: bool) -> str:
    """``12.0KB · transcript + 2 sub-agent transcripts`` or ``… · stored in the
    agent's database``."""
    size = fmt_size(session.size_bytes)
    if database:
        return f"{size} · stored in the agent's database"
    line = f"{size} · transcript"
    if session.subagent_count:
        line += f" + {_plural(session.subagent_count, 'sub-agent transcript')}"
    elif session.has_subdir:
        line += " + session folder"
    return line


def _cost(value: object) -> str:
    try:
        cost = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(value)
    return f"${cost:.2f}" if cost >= 0.01 or cost == 0 else f"${cost:.4f}"


def _count(value: object) -> str:
    try:
        return f"{int(value):,}"  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return str(value)


def _fact(label: str, value: str) -> Content:
    return Content.assemble((f"{label:<{_LABEL_WIDTH}}", "$text-muted"), value)


def _block(heading: str, text: str, *, max_lines: int = BLOCK_LINES) -> list[Content]:
    return [
        Content(""),
        Content.styled(heading, "bold $primary"),
        Content(trim_block(text, max_lines=max_lines)),
    ]


def _join(lines: list[Content]) -> Content:
    return Content("\n").join(lines)


def session_details_text(
    session: SessionMeta,
    details: SessionDetails | None,
    *,
    agent: str,
    state: str,
    database: bool,
    now: datetime,
    home: str,
) -> Content:
    """The details pane's content. ``details`` is None while it is loading."""
    title = session.display_title.replace("\n", " ")
    lines: list[Content] = [
        Content.styled(title, "bold $accent"),
        _fact("agent", agent),
        _fact("state", state),
        _fact("folder", tilde(session.cwd, home) if session.cwd else "-"),
    ]
    if details is not None and details.created:
        try:
            lines.append(_fact("created", readable_time(float(details.created), now)))
        except (TypeError, ValueError, OverflowError, OSError):
            pass
    lines.append(_fact("updated", readable_time(session.mtime, now)))
    lines.append(_fact("storage", storage_line(session, database=database)))
    if details is None:
        lines += [Content(""), Content.styled("Loading the conversation…", "$text-muted")]
        return _join(lines)
    if details.turns is not None and not details.note:
        lines.append(_fact("turns", str(details.turns)))
    if details.model:
        lines.append(_fact("model", str(details.model)))
    if details.cost_usd is not None:
        lines.append(_fact("cost", _cost(details.cost_usd)))
    tokens = []
    if details.tokens_in is not None:
        tokens.append(f"{_count(details.tokens_in)} in")
    if details.tokens_out is not None:
        tokens.append(f"{_count(details.tokens_out)} out")
    if tokens:
        lines.append(_fact("tokens", " · ".join(tokens)))
    if details.note:
        lines += [Content(""), Content.styled(details.note, "$text-warning")]
        return _join(lines)
    first = details.first_prompt or session.first_prompt
    if first and first.strip() != (details.last_prompt or "").strip():
        lines += _block("First prompt", first, max_lines=FIRST_PROMPT_LINES)
    if details.last_prompt:
        lines += _block("Last prompt", details.last_prompt)
    if details.last_reply:
        lines += _block("Last reply", details.last_reply)
    lines += [
        Content(""),
        Content.styled("Enter reads the whole conversation", "$text-muted"),
    ]
    return _join(lines)
