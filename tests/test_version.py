"""Version string: git commit only for a source checkout of aXism itself."""

from __future__ import annotations

import subprocess
from pathlib import Path

from axism import _commit_from_git


def _git_repo(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(
        ["git", "-C", str(root), *a], check=True, capture_output=True, text=True
    )
    run("init", "-q")
    run("-c", "user.name=t", "-c", "user.email=t@example.invalid",
        "commit", "-q", "--allow-empty", "-m", "x")
    return run("rev-parse", "--short=7", "HEAD").stdout.strip()


def _pkg(root: Path) -> Path:
    pkg = root / "src" / "axism"
    pkg.mkdir(parents=True)
    return pkg


def test_source_checkout_of_axism_reports_commit(tmp_path: Path):
    head = _git_repo(tmp_path / "repo")
    pkg = _pkg(tmp_path / "repo")
    (tmp_path / "repo" / "pyproject.toml").write_text(
        '[project]\nname = "axism"\nversion = "0.0.0"\n', encoding="utf-8"
    )
    assert _commit_from_git(pkg) == head


def test_install_inside_another_git_repo_has_no_commit(tmp_path: Path):
    _git_repo(tmp_path / "other")
    site = tmp_path / "other" / ".venv" / "lib" / "python3.12" / "site-packages"
    pkg = site / "axism"
    pkg.mkdir(parents=True)
    assert _commit_from_git(pkg) == ""


def test_other_projects_pyproject_is_not_aXism(tmp_path: Path):
    _git_repo(tmp_path / "repo")
    pkg = _pkg(tmp_path / "repo")
    (tmp_path / "repo" / "pyproject.toml").write_text(
        '[project]\nname = "something-else"\n', encoding="utf-8"
    )
    assert _commit_from_git(pkg) == ""
