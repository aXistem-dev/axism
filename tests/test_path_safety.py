"""Path safety for project deletes."""

from pathlib import Path

from axism.delete import build_project_delete_plan


def test_project_delete_refuses_outside_projects(fake_root: Path, tmp_path: Path):
    """Symlink or path outside projects/ must be blocked."""
    outside = tmp_path / "outside-project"
    outside.mkdir()
    # Direct path that is not under fake_root/projects
    plan = build_project_delete_plan(str(outside), root=fake_root, use_cli=False)
    assert plan.can_delete is False
    assert plan.blocked_reason is not None
    assert "not found" in plan.blocked_reason or "outside" in plan.blocked_reason


def test_project_delete_refuses_symlink_escape(fake_root: Path, tmp_path: Path):
    escape_target = tmp_path / "escape-target"
    escape_target.mkdir()
    link = fake_root / "projects" / "evil-link"
    try:
        link.symlink_to(escape_target)
    except OSError:
        # Some environments disallow symlinks; skip soft
        return
    plan = build_project_delete_plan("evil-link", root=fake_root, use_cli=False)
    # Resolved path is outside projects/ → blocked
    assert plan.can_delete is False
    assert plan.blocked_reason and "outside" in plan.blocked_reason
