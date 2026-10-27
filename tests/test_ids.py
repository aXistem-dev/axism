from __future__ import annotations

import sqlite3

import pytest

from axism import cli
from axism.fragments import PROTECTED_NAMES
from axism.ids import AmbiguousSessionId, match_session_id
from axism.providers.hermes_store import ops as hermes_ops
from axism.providers.hermes_store.paths import state_db
from tests.test_hermes_provider import CLI_SESSION, hermes_env  # noqa: F401


def test_exact_beats_prefix():
    assert match_session_id(["abc", "abcd"], "abc") == "abc"


def test_unique_prefix():
    assert match_session_id(["abc1", "xyz9"], "ab") == "abc1"
    assert match_session_id(["abc1", "xyz9"], "nope") is None


def test_ambiguous_raises():
    ids = ["20261022_100000_aaaaaa", "20261022_110000_bbbbbb", "other"]
    with pytest.raises(AmbiguousSessionId) as exc:
        match_session_id(ids, "20261022")
    assert exc.value.matches == sorted(ids[:2])


def test_empty_needle_none():
    assert match_session_id(["abc"], "") is None
    assert match_session_id(["abc"], "   ") is None


def test_case_insensitive():
    assert match_session_id(["AbC123"], "abc") == "AbC123"


def test_mcps_is_protected():
    assert "mcps" in PROTECTED_NAMES


def test_cli_delete_ambiguous_prefix_deletes_nothing(
    hermes_env, monkeypatch: pytest.MonkeyPatch, capsys  # noqa: F811
):
    root, workspace = hermes_env
    con = sqlite3.connect(state_db(root))
    with con:
        con.execute(
            "INSERT INTO sessions VALUES (?, 'cli', 'Twin', NULL, ?, NULL,"
            " 105.0, 305.0, 1, 0, 0, 0, 'default')",
            ("20260101_090000_cccccc", workspace),
        )
    con.close()
    calls: list[list[str]] = []
    monkeypatch.setattr(
        hermes_ops, "run_hermes", lambda _r, args, **_k: (calls.append(args) or (0, "ok"))
    )
    with pytest.raises(SystemExit) as exc:
        cli.main(["--provider", "hermes", "delete", "20260101", "--yes"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "ambiguous id '20260101' matches 2 sessions:" in err
    assert CLI_SESSION in err
    assert calls == []
