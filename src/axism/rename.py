"""Rename Claude Code sessions by appending custom-title records."""

from __future__ import annotations

import json
from pathlib import Path

from axism.discover import find_session
from axism.paths import config_root


def rename_session(
    session_id: str,
    new_title: str,
    *,
    root: Path | None = None,
    project_slug: str | None = None,
) -> tuple[bool, str]:
    """Rename a session the way Claude's ``/rename`` does.

    Appends ``{"type":"custom-title","customTitle":...}`` to the transcript
    JSONL and updates ``jobs/<short>/state.json`` name when present.
    """
    title = " ".join(new_title.split()).strip()
    if not title:
        return False, "title must not be empty"
    if len(title) > 200:
        return False, "title too long (max 200 characters)"

    root = root or config_root()
    found = find_session(session_id, root)
    if not found:
        return False, f"session not found: {session_id}"
    _proj, sess = found
    path = sess.transcript_path
    if not path.is_file():
        return False, f"transcript missing: {path}"

    record = {
        "type": "custom-title",
        "customTitle": title,
        "sessionId": sess.session_id,
    }
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        return False, f"failed to write transcript: {exc}"

    messages = [f"renamed to {title!r}"]
    job_state = root / "jobs" / sess.session_id[:8] / "state.json"
    if job_state.is_file():
        try:
            data = json.loads(job_state.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data["name"] = title
                job_state.write_text(
                    json.dumps(data, indent=2) + "\n", encoding="utf-8"
                )
                messages.append("updated jobs/*/state.json name")
        except (OSError, json.JSONDecodeError) as exc:
            messages.append(f"job state not updated: {exc}")

    return True, "; ".join(messages)
