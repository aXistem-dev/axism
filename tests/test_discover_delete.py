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
