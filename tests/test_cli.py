"""CLI behaviour: config-dir fallback, delete exit codes, move liveness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from axism import cli


def _run(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    code = exc.value.code
    return 0 if code is None else int(code)


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "axism-config"
    cfg.mkdir()
    monkeypatch.setenv("AXISM_CONFIG_DIR", str(cfg))
    return cfg


def test_config_dir_with_unknown_active_provider_falls_back(
    _isolated_settings: Path, fake_root: Path, monkeypatch: pytest.MonkeyPatch
):
    (_isolated_settings / "settings.json").write_text(
        json.dumps({"active_provider": "codex"}), encoding="utf-8"
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(fake_root))
    assert _run(["--config-dir", str(fake_root), "list"]) == 0
    assert _run(["list", "--config-dir", str(fake_root)]) == 0
