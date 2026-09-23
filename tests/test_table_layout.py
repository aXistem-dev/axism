"""Pure layout helpers for responsive TUI tables."""

from __future__ import annotations

from axism.tui.app import (
    _agent_glyph,
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

    tight = plan_projects_layout(16, multi_agent=True)
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
