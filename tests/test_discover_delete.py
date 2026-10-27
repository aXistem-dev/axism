from pathlib import Path

from axism.delete import (
    build_delete_plan,
    build_project_delete_plan,
    execute_delete,
    execute_deletes,
    execute_project_delete,
)
from axism.discover import discover_all, find_session
from axism.fragments import collect_fragments
from axism.live import load_job_states

SESSION_A = "11111111-1111-1111-1111-111111111111"
SESSION_B = "22222222-2222-2222-2222-222222222222"
DEMO_SLUG = "-home-alice-src-demo"


def test_discover_sessions(fake_root: Path):
    projects = discover_all(fake_root)
    assert len(projects) == 1
    assert projects[0].slug == DEMO_SLUG
    assert len(projects[0].sessions) == 2
    titles = {s.display_title for s in projects[0].sessions}
    assert "Demo session A" in titles
    assert "Background demo" in titles
    a = next(s for s in projects[0].sessions if s.session_id == SESSION_A)
    assert a.subagent_count == 1
    assert a.first_prompt and "alice demo" in a.first_prompt


def test_find_session(fake_root: Path):
    found = find_session(SESSION_A[:8], fake_root)
    assert found is not None
    assert found[1].session_id == SESSION_A


def test_fragments_and_protect_memory(fake_root: Path):
    inv = collect_fragments(SESSION_A, project_slug=DEMO_SLUG, root=fake_root)
    kinds = {f.kind for f in inv.deletable}
    assert "transcript" in kinds
    assert "session_dir" in kinds
    assert "file_history" in kinds
    assert "history_lines" in kinds
    assert not any("memory" in str(f.path) for f in inv.deletable)


def test_job_state(fake_root: Path):
    jobs = load_job_states(fake_root)
    assert SESSION_B in jobs
    assert jobs[SESSION_B].state == "done"
    assert jobs[SESSION_B].kind == "background"


def test_delete_dry_run_and_execute(fake_root: Path):
    plan = build_delete_plan(
        SESSION_A, project_slug=DEMO_SLUG, root=fake_root, use_cli=False
    )
    assert plan.can_delete
    actions = execute_delete(plan, root=fake_root, dry_run=True)
    assert any("would remove" in a or "would rewrite" in a for a in actions)

    execute_delete(plan, root=fake_root, dry_run=False, force=True, stop_live=False)
    assert (fake_root / "projects" / DEMO_SLUG / f"{SESSION_A}.jsonl").exists() is False
    assert (fake_root / "projects" / DEMO_SLUG / SESSION_A).exists() is False
    assert (fake_root / "file-history" / SESSION_A).exists() is False
    assert (fake_root / "projects" / DEMO_SLUG / "memory" / "MEMORY.md").is_file()
    assert (fake_root / ".credentials.json").is_file()
    hist = (fake_root / "history.jsonl").read_text(encoding="utf-8")
    assert SESSION_A not in hist
    assert "keep me" in hist
    assert (fake_root / "projects" / DEMO_SLUG / f"{SESSION_B}.jsonl").is_file()


def test_multi_delete(fake_root: Path):
    plans = [
        build_delete_plan(
            SESSION_A, project_slug=DEMO_SLUG, root=fake_root, use_cli=False
        ),
        build_delete_plan(
            SESSION_B, project_slug=DEMO_SLUG, root=fake_root, use_cli=False
        ),
    ]
    actions = execute_deletes(
        plans, root=fake_root, dry_run=False, force=True, stop_live=False
    )
    assert any("history" in a for a in actions)
    assert (fake_root / "projects" / DEMO_SLUG / f"{SESSION_A}.jsonl").exists() is False
    assert (fake_root / "projects" / DEMO_SLUG / f"{SESSION_B}.jsonl").exists() is False
    assert (fake_root / "projects" / DEMO_SLUG / "memory" / "MEMORY.md").is_file()


def test_delete_project_full(fake_root: Path):
    plan = build_project_delete_plan(
        DEMO_SLUG, root=fake_root, use_cli=False, keep_memory=False
    )
    assert plan.can_delete
    assert len(plan.session_plans) == 2
    actions = execute_project_delete(
        plan, root=fake_root, dry_run=False, force=True, stop_live=False
    )
    assert any("removed dir" in a and DEMO_SLUG in a for a in actions)
    assert (fake_root / "projects" / DEMO_SLUG).exists() is False
    assert (fake_root / ".credentials.json").is_file()


def test_delete_project_keep_memory(fake_root: Path):
    plan = build_project_delete_plan(
        DEMO_SLUG, root=fake_root, use_cli=False, keep_memory=True
    )
    execute_project_delete(
        plan, root=fake_root, dry_run=False, force=True, stop_live=False
    )
    assert (fake_root / "projects" / DEMO_SLUG / "memory" / "MEMORY.md").is_file()
    assert (fake_root / "projects" / DEMO_SLUG / f"{SESSION_A}.jsonl").exists() is False


def test_delete_empty_project(fake_root: Path):
    empty_slug = "-home-alice-src-empty"
    empty = fake_root / "projects" / empty_slug
    empty.mkdir(parents=True)
    plan = build_project_delete_plan(empty_slug, root=fake_root, use_cli=False)
    assert plan.can_delete
    assert plan.session_plans == []
    execute_project_delete(plan, root=fake_root, dry_run=False, force=True)
    assert empty.exists() is False


# --- delete safety (project target resolution, removal guard, ids, symlinks) ---


def test_delete_project_dot_resolves_to_cwd_project(fake_root, tmp_path, monkeypatch):
    from axism.paths import encode_project_slug

    cwd = tmp_path / "work" / "demo"
    cwd.mkdir(parents=True)
    slug = encode_project_slug(str(cwd.resolve()))
    (fake_root / "projects" / slug).mkdir()
    monkeypatch.chdir(cwd)
    plan = build_project_delete_plan(".", root=fake_root, use_cli=False)
    assert plan.blocked_reason is None
    assert plan.slug == slug
    assert plan.project_path == fake_root / "projects" / slug


import pytest


@pytest.mark.parametrize("target", ["", "..", "/", "/home/alice/nowhere", "../projects"])
def test_delete_project_bad_target_blocked_even_with_force(
    fake_root, tmp_path, monkeypatch, target
):
    monkeypatch.chdir(tmp_path)
    plan = build_project_delete_plan(target, root=fake_root, force=True, use_cli=False)
    assert plan.blocked_reason and "not found" in plan.blocked_reason
    assert plan.hard_blocked
    assert plan.extra_paths == []
    actions = execute_project_delete(
        plan, root=fake_root, dry_run=False, force=True
    )
    assert actions and actions[0].startswith("aborted")
    assert (fake_root / "projects" / DEMO_SLUG).is_dir()
    assert (fake_root / "projects").is_dir()


def test_removal_guard_refuses_roots(fake_root):
    from axism.delete import ProjectDeletePlan

    plan = ProjectDeletePlan(
        slug="x",
        project_path=fake_root / "projects",
        extra_paths=[fake_root],
    )
    actions = execute_project_delete(plan, root=fake_root, dry_run=False)
    assert any(a.startswith("refused: ") for a in actions)
    assert (fake_root / "projects" / DEMO_SLUG).is_dir()
    assert (fake_root / "settings.json").is_file()


def test_removal_guard_refuses_fragment_outside_root(fake_root, tmp_path):
    from axism.fragments import Fragment

    outside = tmp_path.parent / "axism-outside-victim"
    outside.mkdir(exist_ok=True)
    plan = build_delete_plan(
        SESSION_A, project_slug=DEMO_SLUG, root=fake_root, use_cli=False
    )
    plan.inventory.fragments = [Fragment("uploads", outside, 0)]
    actions = execute_delete(plan, root=fake_root, dry_run=False)
    assert any(a.startswith("refused: ") for a in actions)
    assert outside.is_dir()
    outside.rmdir()


def test_delete_traversal_id_removes_nothing(fake_root):
    plan = build_delete_plan(
        "../projects", root=fake_root, force=True, use_cli=False
    )
    assert plan.hard_blocked
    assert plan.inventory.deletable == []
    actions = execute_deletes([plan], root=fake_root, dry_run=False, force=True)
    assert actions[0].startswith("aborted")
    assert (fake_root / "projects" / DEMO_SLUG).is_dir()
    assert collect_fragments("../projects", root=fake_root).fragments == []


def test_symlinked_fragment_unlinks_link_only(fake_root, tmp_path):
    target = tmp_path.parent / "axism-link-target"
    target.mkdir(exist_ok=True)
    (target / "keep.txt").write_text("x")
    link = fake_root / "file-history" / SESSION_A
    link.parent.mkdir(exist_ok=True)
    if link.exists() or link.is_symlink():
        import shutil

        shutil.rmtree(link) if not link.is_symlink() else link.unlink()
    link.symlink_to(target, target_is_directory=True)
    plan = build_delete_plan(
        SESSION_A, project_slug=DEMO_SLUG, root=fake_root, use_cli=False
    )
    execute_delete(plan, root=fake_root, dry_run=False)
    assert not link.is_symlink() and not link.exists()
    assert (target / "keep.txt").is_file()
    import shutil

    shutil.rmtree(target)


# --- discover robustness ---

STEM = "44444444-4444-4444-4444-444444444444"


def _write_transcript(root, name, rows):
    from tests.conftest import write_jsonl

    write_jsonl(root / "projects" / DEMO_SLUG / f"{name}.jsonl", rows)


def test_session_id_is_filename_stem(fake_root):
    _write_transcript(
        fake_root,
        STEM,
        [{"type": "user", "sessionId": SESSION_B, "message": {"content": "hi"}}],
    )
    sessions = [s for p in discover_all(fake_root) for s in p.sessions]
    ids = {s.session_id for s in sessions}
    assert STEM in ids
    assert len([s for s in sessions if s.session_id == SESSION_B]) == 1
    plan = build_delete_plan(
        STEM, project_slug=DEMO_SLUG, root=fake_root, use_cli=False
    )
    execute_delete(plan, root=fake_root, dry_run=False)
    assert not (fake_root / "projects" / DEMO_SLUG / f"{STEM}.jsonl").exists()
    assert (fake_root / "projects" / DEMO_SLUG / f"{SESSION_B}.jsonl").is_file()


def test_custom_title_beats_ai_title(fake_root):
    _write_transcript(
        fake_root,
        STEM,
        [
            {"type": "custom-title", "customTitle": "MINE", "sessionId": STEM},
            {"type": "ai-title", "title": "AI", "sessionId": STEM},
        ],
    )
    _p, sess = find_session(STEM, fake_root)
    assert sess.title == "MINE"


def test_guard_is_per_root(fake_root):
    from axism.fragments import Fragment

    plan = build_delete_plan(
        SESSION_A, project_slug=DEMO_SLUG, root=fake_root, use_cli=False
    )
    plan.inventory.fragments = [
        Fragment("uploads", fake_root / "projects", 0),
        Fragment("uploads", fake_root / "projects" / DEMO_SLUG, 0),
        Fragment("transcript", fake_root / "uploads", 0),
        Fragment("tmp_scratch", fake_root / "projects" / DEMO_SLUG, 0),
    ]
    actions = execute_delete(plan, root=fake_root, dry_run=False)
    assert len([a for a in actions if a.startswith("refused: ")]) == 4
    assert (fake_root / "projects" / DEMO_SLUG).is_dir()
