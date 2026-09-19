"""CLI entrypoint for axism."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from axism import version_string
from axism.delete import (
    build_delete_plan,
    build_project_delete_plan,
    execute_deletes,
    execute_project_delete,
)
from axism.discover import discover_all, filter_projects_by_cwd, find_session
from axism.fragments import collect_fragments
from axism.live import exec_open, merge_live, open_command, purge_project_cli, slash_stop
from axism.move import move_projects, move_sessions
from axism.paths import config_root
from axism.rename import rename_session


def _fmt_size(n: int) -> str:
    x: float = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if x < 1024:
            return f"{x:.0f}{unit}" if unit == "B" else f"{x:.1f}{unit}"
        x /= 1024
    return f"{x:.1f}TB"


def cmd_list(args: argparse.Namespace) -> int:
    root = Path(args.config_dir).expanduser() if args.config_dir else config_root()
    projects = discover_all(root)
    if args.project:
        projects = filter_projects_by_cwd(projects, args.project)
    live = merge_live(root, use_cli=not args.no_cli)

    if args.json:
        payload = []
        for p in projects:
            for s in p.sessions:
                lv = live.get(s.session_id)
                payload.append(
                    {
                        "session_id": s.session_id,
                        "project_slug": s.project_slug,
                        "cwd": s.cwd,
                        "title": s.display_title,
                        "size_bytes": s.size_bytes,
                        "mtime": s.mtime,
                        "subagent_count": s.subagent_count,
                        "has_bridge": s.has_bridge,
                        "live": None
                        if not lv
                        else {
                            "kind": lv.kind,
                            "state": lv.state,
                            "name": lv.name,
                            "is_live": lv.is_live,
                        },
                    }
                )
        print(json.dumps(payload, indent=2))
        return 0

    for p in projects:
        print(
            f"\n{p.cwd_guess}  ({p.slug})  sessions={p.session_count}  "
            f"{_fmt_size(p.total_bytes)}"
        )
        for s in p.sessions:
            lv = live.get(s.session_id)
            tags = []
            if lv:
                tags.append(lv.kind)
                if lv.is_live:
                    tags.append("live")
                if lv.state:
                    tags.append(str(lv.state))
            if s.has_bridge:
                tags.append("remote")
            tag_s = f" [{', '.join(tags)}]" if tags else ""
            title = s.display_title.replace("\n", " ")[:60]
            print(
                f"  {s.session_id}  {_fmt_size(s.size_bytes):>8}  "
                f"{title}{tag_s}"
            )
    if not projects:
        print(f"No projects found under {root}", file=sys.stderr)
        return 1
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    root = Path(args.config_dir).expanduser() if args.config_dir else config_root()
    found = find_session(args.session_id, root)
    if not found:
        print(f"Session not found: {args.session_id}", file=sys.stderr)
        return 1
    proj, sess = found
    live = merge_live(root, use_cli=not args.no_cli).get(sess.session_id)
    inv = collect_fragments(
        sess.session_id, project_slug=sess.project_slug, root=root
    )
    print(f"title:     {sess.display_title}")
    print(f"session:   {sess.session_id}")
    print(f"project:   {proj.slug}")
    print(f"cwd:       {sess.cwd}")
    print(f"size:      {_fmt_size(sess.size_bytes)}")
    print(f"transcript:{sess.transcript_path}")
    if live:
        print(f"live:      kind={live.kind} state={live.state} name={live.name!r}")
    print("fragments:")
    for f in inv.fragments:
        prot = " PROTECTED" if f.is_protected() else ""
        print(f"  [{f.kind}] {_fmt_size(f.size_bytes):>8}{prot}  {f.path}")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    root = Path(args.config_dir).expanduser() if args.config_dir else config_root()
    ids: list[str] = list(args.session_ids)
    plans = [
        build_delete_plan(
            sid,
            root=root,
            force=args.force,
            use_cli=not args.no_cli,
        )
        for sid in ids
    ]
    for plan in plans:
        for line in plan.summary_lines():
            print(line)
        print()

    if args.dry_run or not args.yes:
        actions = execute_deletes(
            plans, root=root, dry_run=True, force=args.force
        )
        print("-- dry-run --")
        for a in actions:
            print(a)
        if not args.yes:
            if not args.dry_run:
                print(
                    "Refusing to delete without --yes (pass --dry-run to preview only).",
                    file=sys.stderr,
                )
            blocked = [p for p in plans if not p.can_delete and not args.force]
            return 0 if not blocked else 2
        return 0

    blocked = [p for p in plans if not p.can_delete and not args.force]
    if blocked:
        for p in blocked:
            print(f"Blocked: {p.session_id}: {p.blocked_reason}", file=sys.stderr)
        return 2

    actions = execute_deletes(
        plans,
        root=root,
        dry_run=False,
        force=args.force,
        stop_live=not args.no_stop,
    )
    for a in actions:
        print(a)
    return 0


def cmd_delete_project(args: argparse.Namespace) -> int:
    root = Path(args.config_dir).expanduser() if args.config_dir else config_root()
    plan = build_project_delete_plan(
        args.target,
        root=root,
        force=args.force,
        use_cli=not args.no_cli,
        keep_memory=args.keep_memory,
    )
    for line in plan.summary_lines():
        print(line)

    if args.dry_run or not args.yes:
        actions = execute_project_delete(
            plan, root=root, dry_run=True, force=args.force
        )
        print("-- dry-run --")
        for a in actions:
            print(a)
        if not args.yes:
            if not args.dry_run:
                print(
                    "Refusing to delete without --yes (pass --dry-run to preview only).",
                    file=sys.stderr,
                )
            return 0 if plan.can_delete or args.force else 2
        return 0

    if not plan.can_delete and not args.force:
        print(f"Blocked: {plan.blocked_reason}", file=sys.stderr)
        return 2

    actions = execute_project_delete(
        plan,
        root=root,
        dry_run=False,
        force=args.force,
        stop_live=not args.no_stop,
    )
    for a in actions:
        print(a)
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    root = Path(args.config_dir).expanduser() if args.config_dir else config_root()
    found = find_session(args.session_id, root)
    if not found:
        print(f"Session not found: {args.session_id}", file=sys.stderr)
        return 1
    _proj, sess = found
    live = merge_live(root, use_cli=not args.no_cli).get(sess.session_id)
    cwd = sess.cwd
    if args.print_only:
        argv, _ = open_command(sess.session_id, cwd=cwd, live=live)
        print(" ".join(argv))
        if cwd:
            print(f"# cwd: {cwd}")
        return 0
    try:
        exec_open(sess.session_id, cwd=cwd, live=live)
    except FileNotFoundError as exc:
        print(f"axism: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"axism: failed to open session: {exc}", file=sys.stderr)
        return 1
    return 0  # unreachable on success


def cmd_stop(args: argparse.Namespace) -> int:
    """Stop a background session (same as /stop while attached)."""
    root = Path(args.config_dir).expanduser() if args.config_dir else config_root()
    found = find_session(args.session_id, root)
    if not found:
        print(f"Session not found: {args.session_id}", file=sys.stderr)
        return 1
    _proj, sess = found
    live = merge_live(root, use_cli=not args.no_cli).get(sess.session_id)
    if not args.yes:
        state = live.state if live else "?"
        kind = live.kind if live else "?"
        print(f"Would stop {sess.session_id} kind={kind} state={state} (claude stop)")
        print("Pass --yes to execute.", file=sys.stderr)
        return 0
    ok, msg = slash_stop(sess.session_id, live, root=root)
    print(msg)
    return 0 if ok else 1



def cmd_rename(args: argparse.Namespace) -> int:
    root = Path(args.config_dir).expanduser() if args.config_dir else config_root()
    ok, msg = rename_session(args.session_id, args.title, root=root)
    print(msg)
    return 0 if ok else 1


def cmd_move(args: argparse.Namespace) -> int:
    root = Path(args.config_dir).expanduser() if args.config_dir else config_root()
    if args.project:
        projects = discover_all(root)
        by_slug = {p.slug: p for p in projects}
        by_cwd = {p.cwd_guess: p for p in projects}
        targets = []
        for key in args.ids:
            p = by_slug.get(key) or by_cwd.get(key)
            if not p:
                from axism.paths import encode_project_slug

                slug = encode_project_slug(key)
                p = by_slug.get(slug)
            if not p:
                print(f"Project not found: {key}", file=sys.stderr)
                return 1
            targets.append(p)
        merge_memory = not args.no_merge_memory
        result = move_projects(
            targets,
            args.to,
            root=root,
            dry_run=args.dry_run,
            merge_memory=merge_memory,
        )
    else:
        sessions = []
        for sid in args.ids:
            found = find_session(sid, root)
            if not found:
                print(f"Session not found: {sid}", file=sys.stderr)
                return 1
            _proj, sess = found
            sessions.append(sess)
        result = move_sessions(sessions, args.to, root=root, dry_run=args.dry_run)
    for line in result.actions:
        print(line)
    print(result.message)

    if (
        args.project
        and result.ok
        and not args.dry_run
        and result.memory_needs_agent
        and args.memory_agent != "none"
    ):
        from axism.agents import (
            detect_agents,
            exec_agent_handoff,
            pick_auto_agent,
            write_memory_merge_brief,
        )

        stats = result.memory_stats
        force = bool(args.memory_agent_force)
        if stats and stats.over_limits() and not force:
            print(
                f"Memory merge needs an agent but exceeds limits ({stats.summary()}). "
                "Pass --memory-agent-force to hand off anyway, or merge MEMORY.md manually.",
                file=sys.stderr,
            )
            return 2
        brief = write_memory_merge_brief(result, root=root, force=force)
        print(f"Wrote memory merge brief: {brief}")
        agents = detect_agents()
        by_id = {a.id: a for a in agents}
        choice = args.memory_agent
        if choice == "auto":
            agent = pick_auto_agent(agents)
        elif choice == "none":
            agent = None
        else:
            agent = by_id.get(choice)
            if agent is None:
                print(
                    f"Agent {choice!r} not found on PATH; brief saved at {brief}",
                    file=sys.stderr,
                )
                return 0
        if agent is None:
            print("No agent CLI detected; open the brief in any agent.")
            return 0
        print(f"Handing off to {agent.label} ({agent.binary})…")
        try:
            exec_agent_handoff(agent, brief)
        except FileNotFoundError as exc:
            print(f"axism: {exc}", file=sys.stderr)
            return 1
        except OSError as exc:
            print(f"axism: failed to hand off: {exc}", file=sys.stderr)
            return 1
    return 0 if result.ok else 1


def cmd_purge_project(args: argparse.Namespace) -> int:
    rc, out = purge_project_cli(
        args.path,
        dry_run=args.dry_run or not args.yes,
        all_projects=args.all,
    )
    print(out)
    if not args.yes and not args.dry_run and not args.all:
        print(
            "Pass --yes to execute (or --dry-run). This wraps `claude project purge`.",
            file=sys.stderr,
        )
    return rc


def cmd_tui(_args: argparse.Namespace) -> int:
    from axism.tui.app import run_tui

    run_tui()
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config-dir",
        help="Override Claude config root (default: $CLAUDE_CONFIG_DIR or ~/.claude)",
    )
    common.add_argument(
        "--no-cli",
        action="store_true",
        help="Do not call `claude agents` for live enrichment",
    )

    p = argparse.ArgumentParser(
        prog="axism",
        description="aXism — Agent Interactive Session Manager",
        parents=[common],
    )
    p.add_argument("--version", action="version", version=f"axism {version_string()}")

    sub = p.add_subparsers(dest="command")

    tui = sub.add_parser("tui", help="Open the Textual TUI (default)", parents=[common])
    tui.set_defaults(func=cmd_tui)

    ls = sub.add_parser("list", help="List sessions across projects", parents=[common])
    ls.add_argument("--project", help="Filter by project cwd")
    ls.add_argument("--json", action="store_true", help="JSON output")
    ls.set_defaults(func=cmd_list)

    show = sub.add_parser(
        "show", help="Show session detail and fragments", parents=[common]
    )
    show.add_argument("session_id")
    show.set_defaults(func=cmd_show)

    resume = sub.add_parser(
        "resume",
        help="Open a session via `claude attach` (live bg) or `claude --resume`",
        parents=[common],
    )
    resume.add_argument("session_id")
    resume.add_argument(
        "--print-only",
        action="store_true",
        help="Print the resume command instead of exec'ing it",
    )
    resume.set_defaults(func=cmd_resume)

    stop = sub.add_parser(
        "stop",
        help="Stop a background session (same as /stop while attached)",
        parents=[common],
    )
    stop.add_argument("session_id")
    stop.add_argument("--yes", action="store_true", help="Execute stop")
    stop.set_defaults(func=cmd_stop)

    rename = sub.add_parser(
        "rename",
        help="Rename a session (append custom-title, like /rename)",
        parents=[common],
    )
    rename.add_argument("session_id")
    rename.add_argument("title", help="New session title")
    rename.set_defaults(func=cmd_rename)

    move = sub.add_parser(
        "move",
        help="Move session(s) or project(s) to another project path",
        parents=[common],
    )
    move.add_argument(
        "ids",
        nargs="+",
        help="Session UUID(s)/prefixes, or project slug/cwd with --project",
    )
    move.add_argument(
        "--to",
        required=True,
        help="Destination absolute cwd or existing project slug",
    )
    move.add_argument(
        "--project",
        action="store_true",
        help="Treat ids as project slugs or cwds (move whole project)",
    )
    move.add_argument(
        "--dry-run",
        action="store_true",
        help="Show plan without moving",
    )
    move.add_argument(
        "--no-merge-memory",
        action="store_true",
        help="With --project, do not merge memory/ into the destination",
    )
    move.add_argument(
        "--memory-agent",
        default="none",
        choices=["auto", "claude", "cursor", "opencode", "aider", "codex", "none"],
        help=(
            "After a project move that needs MEMORY.md help, hand off to an agent "
            "(default: none — only write a brief when auto/forced)"
        ),
    )
    move.add_argument(
        "--memory-agent-force",
        action="store_true",
        help="Hand off even when memory merge exceeds size/complexity limits",
    )
    move.set_defaults(func=cmd_move)

    delete = sub.add_parser(
        "delete",
        help="Delete one or more sessions and fragments",
        parents=[common],
    )
    delete.add_argument(
        "session_ids",
        nargs="+",
        help="Session UUID(s) or unique prefixes",
    )
    delete.add_argument(
        "--dry-run", action="store_true", help="Show plan without deleting"
    )
    delete.add_argument("--yes", action="store_true", help="Execute delete")
    delete.add_argument(
        "--force",
        action="store_true",
        help="Allow delete even if session looks live",
    )
    delete.add_argument(
        "--no-stop",
        action="store_true",
        help="Do not call claude stop/rm before delete",
    )
    delete.set_defaults(func=cmd_delete)

    del_proj = sub.add_parser(
        "delete-project",
        help="Delete a projects/<slug> dir (empty or full) and session fragments",
        parents=[common],
    )
    del_proj.add_argument(
        "target",
        help="Project slug (e.g. -home-alice-src-demo) or project cwd",
    )
    del_proj.add_argument("--dry-run", action="store_true")
    del_proj.add_argument("--yes", action="store_true")
    del_proj.add_argument(
        "--force",
        action="store_true",
        help="Allow delete even if sessions look live",
    )
    del_proj.add_argument(
        "--keep-memory",
        action="store_true",
        help="Keep projects/<slug>/memory/ when wiping the project",
    )
    del_proj.add_argument(
        "--no-stop",
        action="store_true",
        help="Do not call claude stop/rm before delete",
    )
    del_proj.set_defaults(func=cmd_delete_project)

    purge = sub.add_parser(
        "purge-project",
        help="Wrap `claude project purge` for whole-project wipe",
        parents=[common],
    )
    purge.add_argument("path", nargs="?", help="Project path")
    purge.add_argument("--all", action="store_true", help="Purge all projects")
    purge.add_argument("--dry-run", action="store_true")
    purge.add_argument("--yes", action="store_true")
    purge.set_defaults(func=cmd_purge_project)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        args.func = cmd_tui
    if getattr(args, "config_dir", None):
        import os

        os.environ["CLAUDE_CONFIG_DIR"] = str(
            Path(args.config_dir).expanduser().resolve()
        )
    if not hasattr(args, "no_cli"):
        args.no_cli = False
    rc = args.func(args)
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
