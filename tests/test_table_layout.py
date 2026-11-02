"""Pure layout helpers for responsive TUI tables."""

from __future__ import annotations

from pathlib import Path

from axism.discover import SessionMeta
from axism.live import LiveSession
from axism.tui.app import (
    _agent_glyph,
    _badges,
    _column_tags,
    _ellipsis,
    _short_path,
    plan_projects_layout,
    plan_sessions_layout,
)


def test_short_path_prefers_basename_tail():
    assert _short_path("/home/alice/src/demo", 40) == "/home/alice/src/demo"
    assert _short_path("/home/alice/src/very/long/path/demo", 12) == "…/path/demo"
    assert len(_short_path("/home/alice/src/very/long/path/demo", 10)) <= 10
    assert _ellipsis("hello world", 5) == "hell…"


def test_projects_layout_densities():
    full = plan_projects_layout(40, multi_agent=True)
    assert full.density == "full"
    assert full.show_agent_col and full.show_size and full.show_count

    cozy = plan_projects_layout(30, multi_agent=True)
    assert cozy.density == "cozy"
    assert cozy.show_agent_col
    assert not cozy.show_size

    tight = plan_projects_layout(12, multi_agent=True)
    assert tight.density == "tight"
    assert not tight.show_agent_col
    assert tight.agent_in_project
    assert not tight.show_count


def test_sessions_layout_densities():
    full = plan_sessions_layout(60)
    assert full.show_tags and full.show_updated and full.show_size

    mid = plan_sessions_layout(32)
    assert mid.show_updated and not mid.show_tags
    assert mid.updated_width == 5

    tight = plan_sessions_layout(18)
    assert not tight.show_updated
    assert tight.title_width >= 6


def test_agent_glyph():
    assert _agent_glyph("hermes") == "H"
    assert _agent_glyph("claude_code") == "A"
    assert _agent_glyph("cursor") == "C"


def test_column_plans_budget_includes_cell_padding():
    for budget in range(10, 201):
        for multi in (False, True):
            p = plan_projects_layout(budget, multi_agent=multi)
            widths = p.column_widths()
            assert sum(widths) + 2 * len(widths) <= budget, (budget, multi, p)
        s = plan_sessions_layout(budget)
        widths = s.column_widths()
        assert sum(widths) + 2 * len(widths) <= budget, (budget, s)


def test_primary_column_never_shrinks_as_budget_grows():
    for multi in (False, True):
        prev = 0
        for budget in range(10, 201):
            w = plan_projects_layout(budget, multi_agent=multi).project_width
            assert w >= prev, (budget, multi, w, prev)
            prev = w
    prev = 0
    for budget in range(10, 201):
        w = plan_sessions_layout(budget).title_width
        assert w >= prev, (budget, w, prev)
        prev = w


def test_optional_columns_need_a_roomy_primary_column():
    for budget in range(10, 201):
        p = plan_projects_layout(budget, multi_agent=False)
        if p.show_count:
            assert p.project_width >= 12
        s = plan_sessions_layout(budget)
        if s.show_updated:
            assert s.title_width >= 20
    # compact date comes before the full date plus Size
    mid = [plan_sessions_layout(b) for b in range(10, 80)]
    first_full = next(i for i, x in enumerate(mid) if x.show_updated and x.updated_width == 11)
    first_compact = next(i for i, x in enumerate(mid) if x.show_updated)
    assert first_compact < first_full


def _sess(subagents: int = 0, bridge: bool = False) -> SessionMeta:
    return SessionMeta(
        session_id="s1",
        project_slug="p",
        cwd="/w/p",
        title="t",
        first_prompt=None,
        transcript_path=Path("/w/p/s1.jsonl"),
        mtime=0.0,
        size_bytes=0,
        subagent_count=subagents,
        has_bridge=bridge,
    )


def test_column_tags_are_short_and_complete():
    live = LiveSession(session_id="s1", kind="interactive", state="busy", pid=1)
    sess = _sess(subagents=3, bridge=True)
    assert _badges(sess, live) == "interactive live remote busy subagents:3"
    assert _column_tags(sess, live) == "int busy rc sub:3"
    # "live" stays when no state says more.
    bare = LiveSession(session_id="s1", kind="interactive", state=None, pid=1)
    assert _column_tags(_sess(), bare) == "int live"
    bg = LiveSession(session_id="s1", kind="background", state="working")
    assert _column_tags(_sess(), bg) == "bg working"
    assert _column_tags(_sess(bridge=True), None) == "rc"
    assert _column_tags(_sess(), None) == "-"


def test_tags_column_sizes_to_its_content():
    # 180-column terminal: 68-cell budget, title keeps its 20-cell floor.
    s = plan_sessions_layout(68, tags_need=len("int busy rc sub:3"))
    assert s.show_tags
    assert s.tags_width == 17 and s.title_width == 24
    # Longer tags than fit: tags take the room down to the title floor.
    s = plan_sessions_layout(68, tags_need=40)
    assert s.title_width == 20 and s.tags_width == 21
    # Short tags never shrink below the header.
    s = plan_sessions_layout(84, tags_need=1)
    assert s.tags_width == 4 and s.title_width == 53


def test_tags_need_keeps_layout_invariants():
    for need in (1, 10, 17, 40):
        prev = 0
        for budget in range(10, 201):
            s = plan_sessions_layout(budget, tags_need=need)
            widths = s.column_widths()
            assert sum(widths) + 2 * len(widths) <= budget, (budget, need, s)
            if s.show_updated:
                assert s.title_width >= 20
            assert s.title_width >= prev, (budget, need, s)
            prev = s.title_width
