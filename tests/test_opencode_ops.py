"""OpenCode server selection, API wrapper, identity checks and mutations.

Every ``opencode`` call is stubbed; no test runs the real binary, signals a
process, or reads a real data/state folder.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from axism.providers.base import UnsupportedOperation
from axism.providers.opencode_store import ops
from axism.providers.opencode_store.discover import SessionRow
from tests.opencode_fixtures import not_found, server_info, session_info

SID = "ses_e7f000000000AliceDemo0001"
PROJ = "1111111111111111111111111111111111111111"
CWD = "/home/alice/src/demo"
DEST = "/home/alice/src/other"
CREATED = 1700000000000
SERVICE_PID = 4242
SERVICE_CMD = "/home/alice/.opencode/bin/opencode serve --service"


@pytest.fixture
def oc_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Default OpenCode data folder pinned under tmp (never the real one)."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    root = tmp_path / "xdg-data" / "opencode"
    root.mkdir(parents=True)
    (tmp_path / "xdg-state" / "opencode").mkdir(parents=True)
    return root


def _service_json(state: Path, **extra) -> None:
    body = {"pid": SERVICE_PID, "url": "http://127.0.0.1:4096", "version": "2.1.4"}
    body.update(extra)
    (state / "opencode" / "service.json").write_text(json.dumps(body))


@pytest.fixture
def service_up(oc_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _service_json(oc_root.parent.parent / "xdg-state")
    monkeypatch.setattr(ops, "pid_alive", lambda pid: pid == SERVICE_PID)
    monkeypatch.setattr(
        ops, "read_cmdline", lambda pid: SERVICE_CMD if pid == SERVICE_PID else None
    )
    return oc_root


def _row(**kw) -> SessionRow:
    base = {
        "id": SID,
        "project_id": PROJ,
        "parent_id": None,
        "directory": CWD,
        "title": "Demo",
        "time_created": CREATED,
        "time_updated": CREATED + 5000,
    }
    base.update(kw)
    return SessionRow(**base)


class FakeCLI:
    """Records argv/env and answers by ``(method, path)`` or ``argv[1]``."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.routes: dict[tuple[str, str], list[tuple[int, str, str]]] = {}

    def on(self, method: str, path: str, *responses) -> None:
        self.routes[(method, path)] = [
            (rc, json.dumps(out) if isinstance(out, (dict, list)) else out, err)
            for rc, out, err in responses
        ]

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": list(argv), **kwargs})
        args = list(argv[1:])
        if args and args[0] == "api":
            rest = [a for a in args[1:] if a != "--standalone"]
            key = (rest[0], rest[1])
        else:
            key = (args[0], args[1])
        queue = self.routes.get(key)
        if not queue:
            raise AssertionError(f"unexpected opencode call: {argv}")
        rc, out, err = queue[0] if len(queue) == 1 else queue.pop(0)
        return subprocess.CompletedProcess(argv, rc, out, err)

    @property
    def argvs(self) -> list[list[str]]:
        return [c["argv"] for c in self.calls]


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch) -> FakeCLI:
    fake = FakeCLI()
    monkeypatch.setattr(
        ops.shutil, "which", lambda b, *a, **k: "/opt/fake/bin/opencode"
    )
    monkeypatch.setattr(ops.subprocess, "run", fake)
    return fake


def _ok_session(cli: FakeCLI, *, directory: str = CWD, **kw) -> None:
    cli.on(
        "GET",
        f"/api/session/{SID}",
        (0, {"data": session_info(SID, PROJ, directory, created=CREATED, **kw)}, ""),
    )


# --- service probe ------------------------------------------------------------


def test_service_info_requires_live_serve_process(
    oc_root: Path, monkeypatch: pytest.MonkeyPatch
):
    state = oc_root.parent.parent / "xdg-state"
    assert ops.service_info(oc_root) is None  # no service.json

    _service_json(state, password="synthetic-placeholder-value")
    monkeypatch.setattr(ops, "read_cmdline", lambda pid: SERVICE_CMD)
    monkeypatch.setattr(ops, "pid_alive", lambda pid: False)
    assert ops.service_info(oc_root) is None  # dead pid

    monkeypatch.setattr(ops, "pid_alive", lambda pid: True)
    monkeypatch.setattr(ops, "read_cmdline", lambda pid: "/usr/bin/python3 serve.py")
    assert ops.service_info(oc_root) is None  # alive, not `serve --service`
    monkeypatch.setattr(ops, "read_cmdline", lambda pid: "opencode serve --stdio")
    assert ops.service_info(oc_root) is None

    monkeypatch.setattr(ops, "read_cmdline", lambda pid: SERVICE_CMD)
    info = ops.service_info(oc_root)
    assert info == ops.ServiceInfo(
        pid=SERVICE_PID, url="http://127.0.0.1:4096", version="2.1.4"
    )
    assert "synthetic-placeholder-value" not in repr(info)
    assert not hasattr(info, "password")


def test_service_info_only_for_default_root(
    service_up: Path, tmp_path: Path
):
    other = tmp_path / "elsewhere" / "opencode"
    other.mkdir(parents=True)
    assert ops.service_info(other) is None
    assert ops.service_info(service_up) is not None


def test_service_info_ignores_malformed_file(service_up: Path):
    path = service_up.parent.parent / "xdg-state" / "opencode" / "service.json"
    path.write_text("not json")
    assert ops.service_info(service_up) is None
    path.write_text(json.dumps({"pid": "4242"}))
    assert ops.service_info(service_up) is None


# --- server choice ------------------------------------------------------------


def test_choose_server_matrix(
    oc_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # default root, no service
    choice = ops.choose_server(oc_root)
    assert (choice.flags, choice.env, choice.via) == (["--standalone"], None, "standalone")

    # default root, live service
    _service_json(oc_root.parent.parent / "xdg-state")
    monkeypatch.setattr(ops, "pid_alive", lambda pid: True)
    monkeypatch.setattr(ops, "read_cmdline", lambda pid: SERVICE_CMD)
    choice = ops.choose_server(oc_root)
    assert (choice.flags, choice.env, choice.via) == ([], None, "service")

    # custom root named opencode
    custom = tmp_path / "alt" / "opencode"
    custom.mkdir(parents=True)
    choice = ops.choose_server(custom)
    assert choice.flags == ["--standalone"]
    assert choice.via == "standalone"
    assert choice.env is not None
    assert choice.env["XDG_DATA_HOME"] == str(custom.parent)

    # any other custom root
    odd = tmp_path / "alt" / "my-sessions"
    odd.mkdir()
    with pytest.raises(UnsupportedOperation, match="must be named 'opencode'"):
        ops.choose_server(odd)


# --- process wrapper / api ------------------------------------------------------


def test_timeout_and_missing_binary(oc_root: Path, monkeypatch: pytest.MonkeyPatch):
    # conftest guard: opencode is not on PATH in tests
    called: list = []
    monkeypatch.setattr(ops.subprocess, "run", lambda *a, **k: called.append(a))
    assert ops.run_opencode(oc_root, ["api", "GET", "/api/info"]) == (
        127,
        "opencode not found on PATH",
    )
    assert called == []

    monkeypatch.setattr(ops.shutil, "which", lambda b, *a, **k: "/opt/fake/bin/opencode")

    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))

    monkeypatch.setattr(ops.subprocess, "run", slow)
    assert ops.run_opencode(oc_root, ["api", "GET", "/api/info"]) == (
        124,
        "opencode timed out",
    )


def test_run_opencode_argv_list_with_timeout(oc_root: Path, cli: FakeCLI):
    cli.on("GET", "/api/info", (0, server_info(77), ""))
    rc, out = ops.run_opencode(oc_root, ["api", "--standalone", "GET", "/api/info"])
    assert rc == 0 and '"pid": 77' in out
    call = cli.calls[0]
    assert call["argv"] == ["opencode", "api", "--standalone", "GET", "/api/info"]
    assert call.get("shell") in (None, False)
    assert call["timeout"] == 30.0


def test_api_builds_argv(oc_root: Path, cli: FakeCLI):
    cli.on("GET", "/api/session", (0, {"data": []}, ""))
    ok, body = ops.api(
        oc_root, "GET", "/api/session", params={"limit": "5"}, data={"a": 1}
    )
    assert ok and body == {"data": []}
    assert cli.argvs[0] == [
        "opencode",
        "api",
        "--standalone",
        "GET",
        "/api/session",
        "--param",
        "limit=5",
        "-d",
        '{"a": 1}',
    ]


def test_api_success_requires_json_and_no_error(oc_root: Path, cli: FakeCLI):
    # 204 No Content: exit 0, empty stdout
    cli.on("PATCH", f"/api/session/{SID}", (0, "", ""))
    assert ops.api(oc_root, "PATCH", f"/api/session/{SID}", data={"title": "x"}) == (
        True,
        None,
    )
    # HTTP error: exit 1, error envelope on stdout, status on stderr
    cli.on("GET", f"/api/session/{SID}", (1, not_found(SID), "HTTP 404 Not Found\n"))
    ok, msg = ops.api(oc_root, "GET", f"/api/session/{SID}")
    assert not ok
    assert f"Session not found: {SID}" in msg and "HTTP 404 Not Found" in msg
    # exit 0 but an error envelope
    cli.on("DELETE", f"/api/session/{SID}", (0, not_found(SID), ""))
    ok, msg = ops.api(oc_root, "DELETE", f"/api/session/{SID}")
    assert not ok and "Session not found" in msg
    # exit 0, not JSON (e.g. an HTML page)
    cli.on("GET", "/doc", (0, "<!doctype html><html></html>", ""))
    ok, msg = ops.api(oc_root, "GET", "/doc")
    assert not ok and "JSON" in msg
    # exit != 0 without a body (unknown route)
    cli.on("GET", "/api/nope", (1, "", "HTTP 404 Not Found\n"))
    assert ops.api(oc_root, "GET", "/api/nope") == (False, "HTTP 404 Not Found")
    # multi-line envelope message is collapsed
    cli.on(
        "GET",
        "/api/session/bad",
        (
            1,
            {
                "_tag": "InvalidRequestError",
                "message": 'Expected a string starting with "ses"\n  at ["sessionID"]',
                "kind": "Params",
            },
            "HTTP 400 Bad Request",
        ),
    )
    ok, msg = ops.api(oc_root, "GET", "/api/session/bad")
    assert not ok and "\n" not in msg and "HTTP 400 Bad Request" in msg


def test_api_unsupported_root_reports_failure(tmp_path: Path, cli: FakeCLI):
    odd = tmp_path / "my-sessions"
    odd.mkdir()
    ok, msg = ops.api(odd, "GET", "/api/info")
    assert not ok and msg.startswith("unsupported:")
    assert cli.calls == []


# --- identity checks ------------------------------------------------------------


def test_verify_refuses_pid_mismatch(service_up: Path, cli: FakeCLI):
    cli.on("GET", "/api/info", (0, server_info(9999), ""))
    _ok_session(cli)
    ok, msg = ops.verify_session(service_up, _row())
    assert not ok and msg.startswith("refused:")
    assert cli.argvs == [["opencode", "api", "GET", "/api/info"]]

    cli.on("GET", "/api/info", (0, server_info(SERVICE_PID), ""))
    assert ops.verify_session(service_up, _row()) == (True, "ok")
    # the service path never passes --standalone
    assert all("--standalone" not in a for a in cli.argvs)


def test_verify_refuses_project_or_created_mismatch_and_404(
    oc_root: Path, cli: FakeCLI
):
    _ok_session(cli)
    assert ops.verify_session(oc_root, _row())[0]

    ok, msg = ops.verify_session(oc_root, _row(project_id="2" * 40))
    assert not ok and msg.startswith("refused:")
    ok, msg = ops.verify_session(oc_root, _row(time_created=CREATED + 1))
    assert not ok and msg.startswith("refused:")

    cli.on("GET", f"/api/session/{SID}", (1, not_found(SID), "HTTP 404 Not Found"))
    ok, msg = ops.verify_session(oc_root, _row())
    assert not ok and msg.startswith("refused:") and "not found" in msg.lower()


def test_verify_refuses_malformed_id(oc_root: Path, cli: FakeCLI):
    ok, msg = ops.verify_session(oc_root, _row(id="../etc"))
    assert not ok and msg.startswith("refused:")
    assert cli.calls == []


# --- running state ---------------------------------------------------------------


def test_active_ids_empty_without_service_and_never_runs_cli(
    oc_root: Path, monkeypatch: pytest.MonkeyPatch
):
    called: list = []
    monkeypatch.setattr(ops.shutil, "which", lambda b, *a, **k: "/opt/fake/bin/opencode")
    monkeypatch.setattr(ops.subprocess, "run", lambda *a, **k: called.append(a))
    assert ops.active_session_ids(oc_root) == set()
    assert called == []


def test_active_ids_from_service(service_up: Path, cli: FakeCLI):
    other = "ses_e7f000000000AliceDemo0002"
    cli.on(
        "GET",
        "/api/session/active",
        (0, {"data": {SID: {"type": "running"}, other: {"type": "running"}}}, ""),
    )
    assert ops.active_session_ids(service_up) == {SID, other}
    assert cli.argvs == [["opencode", "api", "GET", "/api/session/active"]]

    cli.on("GET", "/api/session/active", (1, "", "HTTP 500 Internal Server Error"))
    assert ops.active_session_ids(service_up) == set()


# --- mutations ---------------------------------------------------------------------


def test_mutations_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli: FakeCLI):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "default-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "default-state"))
    custom = tmp_path / "alt" / "opencode"
    custom.mkdir(parents=True)
    _ok_session(cli)
    cli.on("PATCH", f"/api/session/{SID}", (0, "", ""))
    cli.on("DELETE", f"/api/session/{SID}", (0, "", ""))
    cli.on("POST", f"/api/session/{SID}/interrupt", (0, {"interrupted": True}, ""))

    assert ops.rename_session(custom, _row(), "  New   name ")[0]
    assert ops.delete_session(custom, _row())[0]
    assert ops.interrupt_session(custom, _row())[0]

    mutations = [a for a in cli.argvs if a[3] != "GET"]
    assert mutations == [
        [
            "opencode",
            "api",
            "--standalone",
            "PATCH",
            f"/api/session/{SID}",
            "-d",
            json.dumps({"title": "New name"}),
        ],
        ["opencode", "api", "--standalone", "DELETE", f"/api/session/{SID}"],
        ["opencode", "api", "--standalone", "POST", f"/api/session/{SID}/interrupt"],
    ]
    for call in cli.calls:
        assert call["env"]["XDG_DATA_HOME"] == str(custom.parent)
    # every mutation was preceded by its identity check
    kinds = [a[3] for a in cli.argvs]
    assert kinds == ["GET", "PATCH", "GET", "DELETE", "GET", "POST"]


def test_mutations_return_refusal_unchanged(oc_root: Path, cli: FakeCLI):
    cli.on("GET", f"/api/session/{SID}", (1, not_found(SID), "HTTP 404 Not Found"))
    expected = ops.verify_session(oc_root, _row())
    assert not expected[0]
    assert ops.rename_session(oc_root, _row(), "x") == expected
    assert ops.delete_session(oc_root, _row()) == expected
    assert ops.interrupt_session(oc_root, _row()) == expected
    assert {a[3] for a in cli.argvs} == {"GET"}


def test_mutations_on_unsupported_root(tmp_path: Path, cli: FakeCLI):
    odd = tmp_path / "my-sessions"
    odd.mkdir()
    for result in (
        ops.rename_session(odd, _row(), "x"),
        ops.delete_session(odd, _row()),
        ops.interrupt_session(odd, _row()),
        ops.move_session(odd, _row(), DEST),
    ):
        assert not result[0]
    assert cli.calls == []


def test_rename_rejects_empty_title(oc_root: Path, cli: FakeCLI):
    ok, msg = ops.rename_session(oc_root, _row(), "   ")
    assert not ok and "empty" in msg
    assert cli.calls == []


def test_delete_reports_failure(oc_root: Path, cli: FakeCLI):
    _ok_session(cli)
    cli.on("DELETE", f"/api/session/{SID}", (1, not_found(SID), "HTTP 404 Not Found"))
    ok, msg = ops.delete_session(oc_root, _row())
    assert not ok and "Session not found" in msg


def test_interrupt_requires_true(oc_root: Path, cli: FakeCLI):
    _ok_session(cli)
    cli.on("POST", f"/api/session/{SID}/interrupt", (0, {"interrupted": False}, ""))
    assert not ops.interrupt_session(oc_root, _row())[0]
    cli.on("POST", f"/api/session/{SID}/interrupt", (0, "", ""))
    assert not ops.interrupt_session(oc_root, _row())[0]
    cli.on("POST", f"/api/session/{SID}/interrupt", (0, {"interrupted": True}, ""))
    assert ops.interrupt_session(oc_root, _row())[0]


# --- move ----------------------------------------------------------------------------


def _move_service(service_up: Path, cli: FakeCLI, *, applied_after: int | None):
    cli.on("GET", "/api/info", (0, server_info(SERVICE_PID), ""))
    before = (0, {"data": session_info(SID, PROJ, CWD, created=CREATED)}, "")
    after = (
        0,
        {"data": session_info(SID, "3" * 40, DEST, created=CREATED)},
        "",
    )
    if applied_after is None:
        responses = [before]
    else:
        responses = [before] * (applied_after + 1) + [after]
    cli.on("GET", f"/api/session/{SID}", *responses)
    cli.on("POST", f"/api/session/{SID}/move", (0, "", ""))


def test_move_via_service_applied(service_up: Path, cli: FakeCLI):
    _move_service(service_up, cli, applied_after=2)
    ok, msg = ops.move_session(
        service_up, _row(), DEST, poll_interval=0, poll_timeout=5
    )
    assert ok, msg
    assert msg == f"moved {SID[:12]} → {DEST}"
    move = [a for a in cli.argvs if a[2:4] == ["POST", f"/api/session/{SID}/move"]]
    assert move == [
        [
            "opencode",
            "api",
            "POST",
            f"/api/session/{SID}/move",
            "-d",
            json.dumps({"directory": DEST, "delivery": "queue"}),
        ]
    ]
    # identity check (info + session) came before the move
    assert cli.argvs[0][2:4] == ["GET", "/api/info"]
    assert cli.argvs[1][2:4] == ["GET", f"/api/session/{SID}"]


def test_move_via_service_not_applied_in_time(service_up: Path, cli: FakeCLI):
    _move_service(service_up, cli, applied_after=None)
    ok, msg = ops.move_session(
        service_up, _row(), DEST, poll_interval=0, poll_timeout=0
    )
    assert not ok
    assert msg == (
        f"error: move of {SID[:12]} to {DEST} was requested but not applied yet; "
        "OpenCode applies it when the session next runs"
    )


def test_move_without_service_is_refused_and_runs_nothing(
    oc_root: Path, cli: FakeCLI
):
    ok, msg = ops.move_session(oc_root, _row(), DEST, poll_interval=0, poll_timeout=0)
    assert (ok, msg) == (False, ops.MOVE_NEEDS_SERVICE)
    assert msg == (
        "refused: moving OpenCode sessions needs OpenCode's background service — "
        "start it with `opencode service start` and try again"
    )
    assert cli.calls == []


def test_move_custom_root_is_refused_and_runs_nothing(tmp_path: Path, cli: FakeCLI):
    custom = tmp_path / "alt" / "opencode"
    custom.mkdir(parents=True)
    ok, msg = ops.move_session(custom, _row(), DEST, poll_interval=0, poll_timeout=0)
    assert (ok, msg) == (False, ops.MOVE_NEEDS_SERVICE)
    assert cli.calls == []


def test_move_requires_absolute_destination(service_up: Path, cli: FakeCLI):
    ok, msg = ops.move_session(service_up, _row(), "relative/dir", poll_timeout=0)
    assert not ok and "absolute" in msg
    assert cli.calls == []


def test_move_refusal_from_verify_posts_nothing(service_up: Path, cli: FakeCLI):
    cli.on("GET", "/api/info", (0, server_info(1), ""))
    ok, msg = ops.move_session(service_up, _row(), DEST, poll_timeout=0)
    assert not ok and msg.startswith("refused:")
    assert all("/move" not in " ".join(a) for a in cli.argvs)


# --- import ----------------------------------------------------------------------------


def test_new_session_id_shape():
    a = ops.new_session_id(now_ms=CREATED)
    b = ops.new_session_id(now_ms=CREATED + 1000)
    assert ops.is_session_id(a) and len(a) == 30
    assert a != ops.new_session_id(now_ms=CREATED)
    # OpenCode session ids sort newest first
    assert b < a


def test_import_session_argv_and_success(tmp_path: Path, oc_root: Path, cli: FakeCLI):
    transfer = tmp_path / "transfer.json"
    transfer.write_text("{}")
    cli.on("session", "import", (0, f"Imported session: {SID}\n", ""))
    ok, msg, new_id = ops.import_session(oc_root, transfer, DEST, session_id=SID)
    assert (ok, new_id) == (True, SID)
    assert SID[:12] in msg
    assert cli.argvs == [
        [
            "opencode",
            "session",
            "import",
            "--standalone",
            str(transfer),
            "--directory",
            DEST,
        ]
    ]


def test_import_new_id_from_output_then_failure(
    tmp_path: Path, oc_root: Path, cli: FakeCLI
):
    transfer = tmp_path / "transfer.json"
    transfer.write_text("{}")
    # duplicate id: exit 0, nothing on stdout, notice on stderr
    cli.on("session", "import", (0, "", "Session already exists\n"))
    ok, msg, new_id = ops.import_session(oc_root, transfer, DEST, session_id=SID)
    assert (ok, new_id) == (False, "") and "already exists" in msg
    # bad payload
    cli.on(
        "session",
        "import",
        (1, "", "Error: Failed to import session: Bad Request\n    at <anonymous>\n"),
    )
    ok, msg, new_id = ops.import_session(oc_root, transfer, DEST, session_id=SID)
    assert (ok, new_id) == (False, "")
    assert msg == "Error: Failed to import session: Bad Request"
    # exit 0 but no confirmation line
    cli.on("session", "import", (0, "", ""))
    assert not ops.import_session(oc_root, transfer, DEST, session_id=SID)[0]
    # confirmation names a different id
    other = "ses_e7f000000000AliceDemo0009"
    cli.on("session", "import", (0, f"Imported session: {other}\n", ""))
    assert not ops.import_session(oc_root, transfer, DEST, session_id=SID)[0]


def test_import_via_service_and_unsupported_root(
    tmp_path: Path, service_up: Path, cli: FakeCLI
):
    transfer = tmp_path / "transfer.json"
    transfer.write_text("{}")
    cli.on("session", "import", (0, f"Imported session: {SID}\n", ""))
    assert ops.import_session(service_up, transfer, DEST, session_id=SID)[0]
    assert cli.argvs[-1] == [
        "opencode", "session", "import", str(transfer), "--directory", DEST
    ]
    odd = tmp_path / "my-sessions"
    odd.mkdir()
    n = len(cli.calls)
    ok, msg, _ = ops.import_session(odd, transfer, DEST, session_id=SID)
    assert not ok and msg.startswith("unsupported:")
    assert len(cli.calls) == n


# --- open ------------------------------------------------------------------------------


def test_open_argv_default_custom_and_unsupported(oc_root: Path, tmp_path: Path):
    assert ops.open_argv(oc_root, SID, CWD) == (
        ["opencode", "--session", SID, CWD],
        None,
    )
    assert ops.open_argv(oc_root, SID, None) == (["opencode", "--session", SID], None)

    custom = tmp_path / "alt" / "opencode"
    custom.mkdir(parents=True)
    argv, env = ops.open_argv(custom, SID, CWD)
    assert argv == ["opencode", "--standalone", "--session", SID, CWD]
    assert env is not None and env["XDG_DATA_HOME"] == str(custom.parent)

    odd = tmp_path / "alt" / "my-sessions"
    odd.mkdir()
    with pytest.raises(UnsupportedOperation):
        ops.open_argv(odd, SID, CWD)
