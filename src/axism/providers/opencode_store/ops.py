"""OpenCode 2 changes, driven through OpenCode's own server.

aXism never writes ``opencode.db``: every change goes through ``opencode api``
(or ``opencode session import``) against the server that owns the database.
Which server that is depends on the data folder:

* the default folder with the background service running → that service;
* the default folder without it → ``--standalone`` (a private server for one
  call; the background service is never started);
* a custom folder named ``opencode`` → ``--standalone`` with
  ``XDG_DATA_HOME`` pointing at its parent.

Before every change the session is re-read through that same server and must
match the row aXism listed, so a change can never land on another database.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import string
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from axism.providers.base import UnsupportedOperation
from axism.providers.common import pid_alive, read_cmdline
from axism.providers.opencode_store.discover import SessionRow
from axism.providers.opencode_store.paths import (
    default_state_dir,
    is_default_root,
    is_session_id,
    short_id,
)

BINARY = "opencode"
PROCESS_NAMES = {"opencode", "opencode.exe"}
FOLDER_NAME = "opencode"
UNSUPPORTED_ROOT = f"refused: the OpenCode data folder must be named '{FOLDER_NAME}'"
MOVE_NEEDS_SERVICE = (
    "refused: moving OpenCode sessions needs OpenCode's background service — "
    "start it with `opencode service start` and try again"
)
MOVE_NEEDS_DEFAULT_ROOT = (
    "refused: moving OpenCode sessions needs OpenCode's background service, "
    "which only runs for the default data folder"
)

_ID_ALPHABET = string.digits + string.ascii_letters
_ID_RANDOM_LEN = 14


@dataclass
class ServiceInfo:
    pid: int
    url: str
    version: str


@dataclass
class ServerChoice:
    flags: list[str]
    env: dict[str, str] | None
    via: str  # "service" or "standalone"
    service: ServiceInfo | None = None


def service_info(root: Path) -> ServiceInfo | None:
    """The running background service for the default data folder, if any.

    Only ``pid``, ``url`` and ``version`` are taken from ``service.json``;
    the pid must be alive and run ``serve --service``.
    """
    if not is_default_root(root):
        return None
    path = default_state_dir() / "service.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    pid, url, version = raw.get("pid"), raw.get("url"), raw.get("version")
    del raw
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    if not pid_alive(pid):
        return None
    tokens = (read_cmdline(pid) or "").split()
    if "serve" not in tokens or "--service" not in tokens:
        return None
    return ServiceInfo(
        pid=pid,
        url=url if isinstance(url, str) else "",
        version=version if isinstance(version, str) else "",
    )


def choose_server(root: Path) -> ServerChoice:
    if is_default_root(root):
        info = service_info(root)
        if info is not None:
            return ServerChoice(flags=[], env=None, via="service", service=info)
        return ServerChoice(flags=["--standalone"], env=None, via="standalone")
    if root.name == FOLDER_NAME:
        env = {**os.environ, "XDG_DATA_HOME": str(root.parent)}
        return ServerChoice(flags=["--standalone"], env=env, via="standalone")
    raise UnsupportedOperation(UNSUPPORTED_ROOT)


def can_move(root: Path) -> bool:
    """Whether a move can go through the background service for this folder."""
    try:
        return choose_server(root).via == "service"
    except UnsupportedOperation:
        return False


def _exec(
    choice: ServerChoice, args: list[str], *, timeout: float = 30.0
) -> tuple[int, str, str]:
    """``(exit code, stdout, stderr)`` of ``opencode <args>``."""
    if shutil.which(BINARY) is None:
        return 127, "", f"{BINARY} not found on PATH"
    try:
        proc = subprocess.run(
            [BINARY, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=choice.env,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"{BINARY} timed out"
    except OSError as exc:
        return 126, "", f"{BINARY} failed to start: {exc}"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def run_opencode(
    root: Path, args: list[str], *, timeout: float = 30.0
) -> tuple[int, str]:
    """Run ``opencode <args>`` with the environment the root needs.

    Raises :class:`UnsupportedOperation` for a custom folder not named
    ``opencode``.
    """
    rc, out, err = _exec(choose_server(root), args, timeout=timeout)
    return rc, out.strip() or err.strip()


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _error_tag(body: object) -> bool:
    return (
        isinstance(body, dict)
        and isinstance(body.get("_tag"), str)
        and body["_tag"].endswith("Error")
    )


def _failure(body: object, err: str, rc: int) -> str:
    parts: list[str] = []
    if isinstance(body, dict) and isinstance(body.get("message"), str):
        parts.append(" ".join(body["message"].split()))
    status = _first_line(err)
    if status:
        parts.append(f"({status})" if parts else status)
    return " ".join(parts) or f"{BINARY} api exited with {rc}"


def _api(
    choice: ServerChoice,
    method: str,
    path: str,
    *,
    params: dict[str, str] | None = None,
    data: dict | None = None,
) -> tuple[bool, object]:
    """Like :func:`api`, but a failure carries only the detail (no prefix)."""
    args = ["api", *choice.flags, method, path]
    for key, value in (params or {}).items():
        args += ["--param", f"{key}={value}"]
    if data is not None:
        args += ["-d", json.dumps(data)]
    rc, out, err = _exec(choice, args)
    text = out.strip()
    body: object = None
    parsed = False
    if text:
        try:
            body = json.loads(text)
            parsed = True
        except ValueError:
            parsed = False
    if rc != 0 or _error_tag(body):
        return False, _failure(body, err, rc)
    if not text:
        return True, None
    if not parsed:
        return False, "the reply was not JSON"
    return True, body


def api(
    root: Path,
    method: str,
    path: str,
    *,
    params: dict[str, str] | None = None,
    data: dict | None = None,
) -> tuple[bool, object]:
    """Call OpenCode's HTTP API through ``opencode api``.

    Success needs exit code 0 and either no body (204) or a JSON body that is
    not an error object; anything else is ``(False, message)``.
    """
    try:
        choice = choose_server(root)
    except UnsupportedOperation as exc:
        return False, str(exc)
    ok, body = _api(choice, method, path, params=params, data=data)
    if not ok:
        return False, f"error: {BINARY} api {method} {path} failed: {body}"
    return True, body


def _session_data(body: object) -> dict | None:
    data = body.get("data") if isinstance(body, dict) else None
    return data if isinstance(data, dict) else None


def _directory_of(data: dict) -> str | None:
    location = data.get("location")
    if isinstance(location, dict) and isinstance(location.get("directory"), str):
        return location["directory"]
    return None


def _service_answers(choice: ServerChoice) -> bool:
    """The server that answers ``/api/info`` is the background service checked."""
    ok, body = _api(choice, "GET", "/api/info")
    pid = body.get("pid") if ok and isinstance(body, dict) else None
    return choice.service is not None and pid == choice.service.pid


def _not_the_service(outcome: str) -> str:
    return (
        "refused: the OpenCode server that answered is not the background "
        f"service aXism checked; {outcome}"
    )


def _verify(choice: ServerChoice, row: SessionRow) -> tuple[bool, str]:
    if not is_session_id(row.id):
        return False, f"refused: not an OpenCode session id: {row.id!r}"
    short = short_id(row.id)
    if choice.via == "service" and not _service_answers(choice):
        return False, _not_the_service(f"{short} left unchanged")
    ok, body = _api(choice, "GET", f"/api/session/{row.id}")
    if not ok:
        return False, f"refused: session {short} not found on the OpenCode server ({body})"
    data = _session_data(body)
    created = (data or {}).get("time", {})
    created = created.get("created") if isinstance(created, dict) else None
    if (
        data is None
        or data.get("id") != row.id
        or data.get("projectID") != row.project_id
        or not isinstance(created, (int, float))
        or int(created) != row.time_created
    ):
        return False, (
            f"refused: the OpenCode server reports a different session {short} "
            "than the one listed; left unchanged"
        )
    return True, "ok"


def verify_session(root: Path, row: SessionRow) -> tuple[bool, str]:
    """The chosen server must serve exactly the session aXism listed."""
    try:
        choice = choose_server(root)
    except UnsupportedOperation as exc:
        return False, str(exc)
    return _verify(choice, row)


def active_session_ids(root: Path) -> set[str]:
    """Sessions the running background service is executing (never starts it)."""
    info = service_info(root)
    if info is None:
        return set()
    choice = ServerChoice(flags=[], env=None, via="service", service=info)
    ok, body = _api(choice, "GET", "/api/session/active")
    data = body.get("data") if ok and isinstance(body, dict) else None
    if not isinstance(data, dict):
        return set()
    return {k for k in data if is_session_id(k)}


def _checked(root: Path, row: SessionRow) -> tuple[ServerChoice | None, str]:
    try:
        choice = choose_server(root)
    except UnsupportedOperation as exc:
        return None, str(exc)
    ok, msg = _verify(choice, row)
    return (choice, msg) if ok else (None, msg)


def rename_session(root: Path, row: SessionRow, title: str) -> tuple[bool, str]:
    clean = " ".join(title.split())
    if not clean:
        return False, "refused: title must not be empty"
    choice, msg = _checked(root, row)
    if choice is None:
        return False, msg
    ok, body = _api(choice, "PATCH", f"/api/session/{row.id}", data={"title": clean})
    if not ok:
        return False, f"error: {BINARY} rename of {short_id(row.id)} failed: {body}"
    return True, f"renamed to {clean!r}"


def delete_session(root: Path, row: SessionRow) -> tuple[bool, str]:
    choice, msg = _checked(root, row)
    if choice is None:
        return False, msg
    ok, body = _api(choice, "DELETE", f"/api/session/{row.id}")
    if not ok:
        return False, f"error: {BINARY} delete of {short_id(row.id)} failed: {body}"
    kids = len(row.child_ids)
    extra = f" and {kids} child session{'s' if kids != 1 else ''}" if kids else ""
    return True, f"deleted session {short_id(row.id)}{extra}"


def interrupt_session(root: Path, row: SessionRow) -> tuple[bool, str]:
    choice, msg = _checked(root, row)
    if choice is None:
        return False, msg
    ok, body = _api(choice, "POST", f"/api/session/{row.id}/interrupt")
    if not ok:
        return False, f"error: {BINARY} interrupt of {short_id(row.id)} failed: {body}"
    if not (isinstance(body, dict) and body.get("interrupted") is True):
        return False, f"error: {short_id(row.id)} was not running on the OpenCode server"
    return True, f"interrupted {short_id(row.id)}"


def _same_dir(a: str, b: str) -> bool:
    if os.path.normpath(a) == os.path.normpath(b):
        return True
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return False


def move_session(
    root: Path,
    row: SessionRow,
    directory: str,
    *,
    poll_interval: float = 0.25,
    poll_timeout: float = 5.0,
) -> tuple[bool, str]:
    """Move through the background service and wait until OpenCode applied it.

    OpenCode queues moves and applies them from its long-running service; a
    private ``--standalone`` server exits before that, so without the service
    nothing is sent.
    """
    try:
        choice = choose_server(root)
    except UnsupportedOperation:
        return False, MOVE_NEEDS_SERVICE
    if choice.via != "service":
        return False, MOVE_NEEDS_SERVICE
    if not os.path.isabs(directory):
        return False, f"refused: move destination must be an absolute path: {directory}"
    ok, msg = _verify(choice, row)
    if not ok:
        return False, msg
    short = short_id(row.id)
    ok, body = _api(
        choice,
        "POST",
        f"/api/session/{row.id}/move",
        data={"directory": directory, "delivery": "queue"},
    )
    if not ok:
        return False, f"error: {BINARY} move of {short} failed: {body}"
    deadline = time.monotonic() + poll_timeout
    while True:
        ok, body = _api(choice, "GET", f"/api/session/{row.id}")
        data = _session_data(body) if ok else None
        current = _directory_of(data) if data else None
        if current is not None and _same_dir(current, directory):
            return True, f"moved {short} → {directory}"
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_interval)
    return False, (
        f"error: move of {short} to {directory} was requested but not applied yet; "
        "OpenCode applies it when the session next runs"
    )


def new_session_id(now_ms: int | None = None) -> str:
    """A fresh id in OpenCode's session id shape (newest sorts first)."""
    ms = int(time.time() * 1000) if now_ms is None else now_ms
    stamp = ~(ms * 0x1000) & ((1 << 48) - 1)
    tail = "".join(secrets.choice(_ID_ALPHABET) for _ in range(_ID_RANDOM_LEN))
    return f"ses_{stamp:012x}{tail}"


def import_session(
    root: Path, transfer_file: Path, directory: str, *, session_id: str
) -> tuple[bool, str, str]:
    """Import a transfer file whose ``info.id`` is ``session_id``.

    Returns ``(ok, message, new_id)``; OpenCode keeps the file's id, so a
    duplicate id is reported as a failure, never as a copy.
    """
    try:
        choice = choose_server(root)
    except UnsupportedOperation as exc:
        return False, str(exc), ""
    if choice.via == "service" and not _service_answers(choice):
        return False, _not_the_service("nothing imported"), ""
    args = [
        "session",
        "import",
        *choice.flags,
        str(transfer_file),
        "--directory",
        directory,
    ]
    failed = "error: import into OpenCode failed:"
    rc, out, err = _exec(choice, args, timeout=120.0)
    if "Session already exists" in err or "Session already exists" in out:
        return False, (
            f"{failed} a session with id {short_id(session_id)} already exists"
        ), ""
    if rc != 0:
        detail = _first_line(err) or _first_line(out) or f"{BINARY} exited with {rc}"
        return False, f"{failed} {detail}", ""
    prefix = "Imported session:"
    imported = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith(prefix):
            imported = line[len(prefix):].strip()
    if imported != session_id:
        return False, (
            f"{failed} OpenCode did not confirm importing {short_id(session_id)}"
            + (f" ({_first_line(err)})" if _first_line(err) else "")
        ), ""
    return True, f"imported {short_id(session_id)} into {directory}", session_id


def open_argv(
    root: Path, session_id: str, directory: str | None
) -> tuple[list[str], dict[str, str] | None]:
    """Command that opens a session in OpenCode for this data folder."""
    tail = [directory] if directory else []
    if is_default_root(root):
        return [BINARY, "--session", session_id, *tail], None
    if root.name == FOLDER_NAME:
        env = {**os.environ, "XDG_DATA_HOME": str(root.parent)}
        return [BINARY, "--standalone", "--session", session_id, *tail], env
    raise UnsupportedOperation(UNSUPPORTED_ROOT)
