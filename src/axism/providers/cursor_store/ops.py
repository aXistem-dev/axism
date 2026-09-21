"""Rename and move operations for Cursor chats."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from axism.discover import ProjectInfo, SessionMeta
from axism.move import MoveResult, normalize_dest_cwd
from axism.providers.cursor_store.discover import locate_sessions, write_cli_meta_name
from axism.providers.cursor_store.paths import (
    TITLE_SIDECAR,
    TRANSCRIPTS_DIRNAME,
    chats_dir,
    cwd_bucket,
    projects_dir,
    slugify_path,
)


def _is_agent_dir(directory: Path) -> bool:
    return directory.parent.name == TRANSCRIPTS_DIRNAME


def rename_session(root: Path, session_id: str, title: str) -> tuple[bool, str]:
    """Set a chat title.

    CLI chats store a name in ``store.db``; agent transcripts have no title
    field, so aXism keeps one in a sidecar next to the transcript.
    """
    clean = " ".join(title.split()).strip()
    if not clean:
        return False, "title must not be empty"
    if len(clean) > 200:
        return False, "title too long (max 200 characters)"

    directories = locate_sessions(root, session_id)
    if not directories:
        return False, f"session not found: {session_id}"

    ok = False
    problems: list[str] = []
    for directory in directories:
        if not _is_agent_dir(directory):
            done, msg = write_cli_meta_name(directory / "store.db", clean)
            ok = ok or done
            if not done:
                problems.append(msg)
            continue
        sidecar = directory / TITLE_SIDECAR
        try:
            sidecar.write_text(
                json.dumps({"title": clean}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            ok = True
        except OSError as exc:
            problems.append(f"failed to write title: {exc}")

    if not ok:
        return False, "; ".join(problems) or "rename failed"
    return True, f"renamed to {clean!r}"


def _dest_dir_for(root: Path, directory: Path, dest_cwd: str) -> Path:
    if _is_agent_dir(directory):
        return projects_dir(root) / slugify_path(dest_cwd) / TRANSCRIPTS_DIRNAME
    return chats_dir(root) / cwd_bucket(dest_cwd)


def move_sessions(
    root: Path,
    sessions: list[SessionMeta],
    dest: str,
    *,
    dry_run: bool = False,
) -> MoveResult:
    """Relocate chat directories into the store folder for ``dest``."""
    if not sessions:
        return MoveResult(False, "no sessions to move")
    dest_cwd = normalize_dest_cwd(dest)
    if not dest_cwd:
        return MoveResult(False, f"invalid destination: {dest!r}")
    dest_slug = slugify_path(dest_cwd)

    actions: list[str] = []
    moved = 0
    for session in sessions:
        short = session.session_id[:8]
        directories = locate_sessions(root, session.session_id)
        if not directories:
            actions.append(f"{short}…: not found on disk — skipped")
            continue
        session_moved = False
        for directory in directories:
            parent = _dest_dir_for(root, directory, dest_cwd)
            target = parent / directory.name
            if target.resolve() == directory.resolve():
                actions.append(f"{short}…: already in destination")
                continue
            if target.exists():
                actions.append(f"{short}…: collision in dest — skipped")
                continue
            if dry_run:
                actions.append(f"would move {short}… → {parent}")
                session_moved = True
                continue
            try:
                parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(directory), str(target))
                actions.append(f"moved {short}… → {parent}")
                session_moved = True
            except OSError as exc:
                actions.append(f"error moving {short}…: {exc}")
        if session_moved:
            moved += 1

    if not moved:
        return MoveResult(
            False, "no sessions moved", actions=actions,
            dest_cwd=dest_cwd, dest_slug=dest_slug,
        )
    verb = "would move" if dry_run else "moved"
    return MoveResult(
        True,
        f"{verb} {moved} chat(s) → {dest_cwd}",
        actions=actions,
        dest_cwd=dest_cwd,
        dest_slug=dest_slug,
    )


def move_projects(
    root: Path,
    projects: list[ProjectInfo],
    dest: str,
    *,
    dry_run: bool = False,
) -> MoveResult:
    """Rename a workspace's store dirs, or merge its chats into an existing one."""
    if not projects:
        return MoveResult(False, "no projects to move")
    dest_cwd = normalize_dest_cwd(dest)
    if not dest_cwd:
        return MoveResult(False, f"invalid destination: {dest!r}")
    dest_slug = slugify_path(dest_cwd)

    sources = [p for p in projects if p.slug != dest_slug]
    if not sources:
        return MoveResult(
            True, "already at destination", dest_cwd=dest_cwd, dest_slug=dest_slug
        )

    dest_project = projects_dir(root) / dest_slug
    dest_bucket = chats_dir(root) / cwd_bucket(dest_cwd)

    if len(sources) == 1 and not dest_project.exists() and not dest_bucket.exists():
        src = sources[0]
        src_project = projects_dir(root) / src.slug
        src_bucket = (
            chats_dir(root) / cwd_bucket(src.cwd_guess) if src.cwd_guess else None
        )
        actions: list[str] = []
        if dry_run:
            if src_project.is_dir():
                actions.append(f"would rename {src_project} → {dest_project}")
            if src_bucket and src_bucket.is_dir():
                actions.append(f"would rename {src_bucket} → {dest_bucket}")
            return MoveResult(
                True,
                f"would move project {src.slug} → {dest_slug}",
                actions=actions,
                dest_cwd=dest_cwd,
                dest_slug=dest_slug,
            )
        try:
            if src_project.is_dir():
                dest_project.parent.mkdir(parents=True, exist_ok=True)
                os.replace(src_project, dest_project)
                actions.append(f"renamed project dir → {dest_slug}")
            if src_bucket and src_bucket.is_dir():
                dest_bucket.parent.mkdir(parents=True, exist_ok=True)
                os.replace(src_bucket, dest_bucket)
                actions.append(f"renamed CLI chat bucket → {dest_bucket.name}")
        except OSError as exc:
            return MoveResult(False, f"rename failed: {exc}", actions=actions)
        if not actions:
            return MoveResult(False, "nothing to move", dest_cwd=dest_cwd)
        return MoveResult(
            True,
            f"moved project → {dest_cwd}",
            actions=actions,
            dest_cwd=dest_cwd,
            dest_slug=dest_slug,
        )

    sessions: list[SessionMeta] = []
    for project in sources:
        sessions.extend(project.sessions)
    if not sessions:
        return MoveResult(False, "no chats to move", dest_cwd=dest_cwd)
    return move_sessions(root, sessions, dest_cwd, dry_run=dry_run)
