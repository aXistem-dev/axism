"""Tests for moving sessions and projects between Claude Code dirs."""

from __future__ import annotations

import json
from pathlib import Path

from axism.agents import detect_agents, pick_auto_agent, write_memory_merge_brief
from axism.discover import discover_all
from axism.move import (
    MEMORY_MAX_BYTES,
    MemoryStats,
    assess_memory_merge,
    merge_project_memory,
    move_projects,
    move_sessions,
    resolve_dest,
)
from axism.paths import encode_project_slug
from tests.conftest import DEMO_CWD, DEMO_SLUG, SESSION_A, SESSION_B, write_jsonl

OTHER_CWD = "/home/alice/src/other"
OTHER_SLUG = "-home-alice-src-other"


def test_resolve_dest_path_and_slug(fake_root: Path):
    projects = discover_all(fake_root)
    got = resolve_dest(DEMO_CWD, root=fake_root, projects=projects)
    assert got is not None
    assert got[0] == DEMO_CWD
    assert got[1] == DEMO_SLUG

    got2 = resolve_dest(DEMO_SLUG, root=fake_root, projects=projects)
    assert got2 is not None
    assert got2[1] == DEMO_SLUG

    got3 = resolve_dest(OTHER_CWD, root=fake_root, projects=projects)
    assert got3 is not None
    assert got3[1] == OTHER_SLUG
    assert not got3[2].exists()


def test_move_session_to_new_project(fake_root: Path):
    projects = discover_all(fake_root)
    sess = next(s for s in projects[0].sessions if s.session_id == SESSION_A)
    result = move_sessions([sess], OTHER_CWD, root=fake_root)
    assert result.ok is True
    assert result.dest_slug == OTHER_SLUG

    dest = fake_root / "projects" / OTHER_SLUG / f"{SESSION_A}.jsonl"
    assert dest.is_file()
    assert not (fake_root / "projects" / DEMO_SLUG / f"{SESSION_A}.jsonl").exists()
    assert (fake_root / "projects" / OTHER_SLUG / SESSION_A / "subagents").is_dir()
    text = dest.read_text(encoding="utf-8")
    assert OTHER_CWD in text
    assert f'"cwd": "{DEMO_CWD}"' not in text

    hist = (fake_root / "history.jsonl").read_text(encoding="utf-8")
    assert OTHER_CWD in hist
    assert (fake_root / "projects" / DEMO_SLUG / "memory" / "MEMORY.md").is_file()


def test_move_sessions_multi_to_existing(fake_root: Path):
    other = fake_root / "projects" / OTHER_SLUG
    other.mkdir(parents=True)
    write_jsonl(
        other / "33333333-3333-3333-3333-333333333333.jsonl",
        [
            {
                "type": "user",
                "sessionId": "33333333-3333-3333-3333-333333333333",
                "cwd": OTHER_CWD,
                "message": {"role": "user", "content": "other"},
            }
        ],
    )
    projects = discover_all(fake_root)
    demo = next(p for p in projects if p.slug == DEMO_SLUG)
    result = move_sessions(demo.sessions, OTHER_CWD, root=fake_root)
    assert result.ok is True
    assert (other / f"{SESSION_A}.jsonl").is_file()
    assert (other / f"{SESSION_B}.jsonl").is_file()
    job = json.loads(
        (fake_root / "jobs" / SESSION_B[:8] / "state.json").read_text(encoding="utf-8")
    )
    assert job["cwd"] == OTHER_CWD


def test_move_project_rename_to_new_path(fake_root: Path):
    projects = discover_all(fake_root)
    demo = next(p for p in projects if p.slug == DEMO_SLUG)
    result = move_projects([demo], OTHER_CWD, root=fake_root)
    assert result.ok is True
    assert result.dest_slug == OTHER_SLUG
    assert not (fake_root / "projects" / DEMO_SLUG).exists()
    assert (fake_root / "projects" / OTHER_SLUG / f"{SESSION_A}.jsonl").is_file()
    assert (fake_root / "projects" / OTHER_SLUG / "memory" / "MEMORY.md").is_file()
    text = (fake_root / "projects" / OTHER_SLUG / f"{SESSION_A}.jsonl").read_text(
        encoding="utf-8"
    )
    assert OTHER_CWD in text
    assert encode_project_slug(OTHER_CWD) == OTHER_SLUG
    assert result.memory_needs_agent is False


def test_move_project_rename_rewrites_nested_cwds(fake_root: Path):
    """Project rename must rewrite every cwd, not only decoded slug guess."""
    proj = fake_root / "projects" / DEMO_SLUG
    nested = f"{DEMO_CWD}/nested-hyphen-dir"
    write_jsonl(
        proj / f"{SESSION_A}.jsonl",
        [
            {
                "type": "user",
                "sessionId": SESSION_A,
                "cwd": DEMO_CWD,
                "message": {"role": "user", "content": "root"},
            },
            {
                "type": "user",
                "sessionId": SESSION_A,
                "cwd": nested,
                "message": {"role": "user", "content": "nested"},
            },
        ],
    )
    projects = discover_all(fake_root)
    demo = next(p for p in projects if p.slug == DEMO_SLUG)
    result = move_projects([demo], OTHER_CWD, root=fake_root)
    assert result.ok is True
    text = (fake_root / "projects" / OTHER_SLUG / f"{SESSION_A}.jsonl").read_text(
        encoding="utf-8"
    )
    assert f'"cwd": "{OTHER_CWD}"' in text
    assert DEMO_CWD not in text.replace(OTHER_CWD, "")
    assert nested not in text


def test_move_project_creates_workspace_when_parent_exists(
    fake_root: Path, tmp_path: Path
):
    parent = tmp_path / "workspaces"
    parent.mkdir()
    dest_cwd = str(parent / "adsb")
    projects = discover_all(fake_root)
    demo = next(p for p in projects if p.slug == DEMO_SLUG)
    result = move_projects([demo], dest_cwd, root=fake_root)
    assert result.ok is True
    assert Path(dest_cwd).is_dir()
    assert any("created workspace dir" in a for a in result.actions)


def test_move_project_merge_moves_memory(fake_root: Path):
    other = fake_root / "projects" / OTHER_SLUG
    other.mkdir(parents=True)
    write_jsonl(
        other / "33333333-3333-3333-3333-333333333333.jsonl",
        [
            {
                "type": "user",
                "sessionId": "33333333-3333-3333-3333-333333333333",
                "cwd": OTHER_CWD,
                "message": {"role": "user", "content": "other"},
            }
        ],
    )
    projects = discover_all(fake_root)
    demo = next(p for p in projects if p.slug == DEMO_SLUG)
    result = move_projects([demo], OTHER_CWD, root=fake_root)
    assert result.ok is True
    assert (other / f"{SESSION_A}.jsonl").is_file()
    assert (other / "memory" / "MEMORY.md").is_file()
    assert not (fake_root / "projects" / DEMO_SLUG / "memory").exists()


def test_memory_merge_collision_and_dual_index(fake_root: Path):
    other = fake_root / "projects" / OTHER_SLUG
    other_mem = other / "memory"
    other_mem.mkdir(parents=True)
    (other_mem / "MEMORY.md").write_text("- [dest](topic-a.md)\n", encoding="utf-8")
    (other_mem / "topic-a.md").write_text("dest topic a\n", encoding="utf-8")
    (other_mem / "shared.md").write_text("dest shared\n", encoding="utf-8")

    demo_mem = fake_root / "projects" / DEMO_SLUG / "memory"
    (demo_mem / "topic-b.md").write_text("source topic b\n", encoding="utf-8")
    (demo_mem / "shared.md").write_text("source shared different\n", encoding="utf-8")

    write_jsonl(
        other / "33333333-3333-3333-3333-333333333333.jsonl",
        [
            {
                "type": "user",
                "sessionId": "33333333-3333-3333-3333-333333333333",
                "cwd": OTHER_CWD,
                "message": {"role": "user", "content": "other"},
            }
        ],
    )
    projects = discover_all(fake_root)
    demo = next(p for p in projects if p.slug == DEMO_SLUG)
    result = move_projects([demo], OTHER_CWD, root=fake_root)
    assert result.ok is True
    assert result.memory_needs_agent is True
    assert (other_mem / "topic-b.md").is_file()
    assert (other_mem / "shared.md").read_text(encoding="utf-8") == "dest shared\n"
    assert list(other_mem.glob("shared-from-*.md"))
    assert list(other_mem.glob("MEMORY-from-*.md"))
    assert (other_mem / "MEMORY.md").is_file()


def test_assess_memory_over_limits():
    assert MemoryStats(file_count=81, total_bytes=10).over_limits() is True
    assert MemoryStats(file_count=1, total_bytes=MEMORY_MAX_BYTES + 1).over_limits() is True
    assert MemoryStats(file_count=1, total_bytes=10, collisions=26).over_limits() is True
    assert MemoryStats(
        file_count=1, total_bytes=10, multi_source_with_memory=True
    ).over_limits() is True
    assert MemoryStats(file_count=2, total_bytes=100, collisions=1).over_limits() is False


def test_detect_agents_and_brief(fake_root: Path, monkeypatch):
    monkeypatch.setattr(
        "axism.agents.shutil.which",
        lambda name: f"/usr/bin/{name}" if name == "claude" else None,
    )
    agents = detect_agents()
    assert [a.id for a in agents] == ["claude"]
    assert pick_auto_agent(agents).id == "claude"

    projects = discover_all(fake_root)
    demo = projects[0]
    other = fake_root / "projects" / OTHER_SLUG
    other.mkdir(parents=True)
    write_jsonl(
        other / "33333333-3333-3333-3333-333333333333.jsonl",
        [
            {
                "type": "user",
                "sessionId": "33333333-3333-3333-3333-333333333333",
                "cwd": OTHER_CWD,
                "message": {"role": "user", "content": "other"},
            }
        ],
    )
    (other / "memory").mkdir()
    (other / "memory" / "MEMORY.md").write_text("# dest\n", encoding="utf-8")
    result = move_projects([demo], OTHER_CWD, root=fake_root)
    assert result.memory_needs_agent
    brief = write_memory_merge_brief(result, root=fake_root, force=False)
    assert brief.is_file()
    text = brief.read_text(encoding="utf-8")
    assert "Hard boundaries" in text
    assert str(result.memory_dest) in text


def test_merge_project_memory_source_only(fake_root: Path):
    other = fake_root / "projects" / OTHER_SLUG
    other.mkdir(parents=True)
    projects = discover_all(fake_root)
    demo = next(p for p in projects if p.slug == DEMO_SLUG)
    actions, needs, _stats, parked, _renamed = merge_project_memory(
        [demo], other, dry_run=False
    )
    assert needs is False
    assert not parked
    assert (other / "memory" / "MEMORY.md").is_file()
    assert assess_memory_merge([demo], other).file_count >= 0
    assert any("moved memory" in a for a in actions)
