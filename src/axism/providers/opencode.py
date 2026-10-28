"""OpenCode 2 session provider — sessions in ``opencode.db``.

Reads go straight to the database (read-only); every change is made through
OpenCode's own server. The background service is never started or stopped.
"""

from __future__ import annotations

import os
from pathlib import Path

from axism.actions import (
    OPENCODE_DELETE_FAILED_PREFIX,
    STOP_ABORT_PREFIX,
    actions_failed,
    project_kept_line,
)
from axism.delete import DeletePlan, ProjectDeletePlan
from axism.details import SessionDetails
from axism.discover import ProjectInfo, SessionMeta
from axism.fragments import FragmentInventory
from axism.live import LiveSession
from axism.move import MoveResult, normalize_dest_cwd
from axism.providers.base import Capabilities, ProviderBase, UnsupportedOperation
from axism.providers.common import (
    binary_on_path,
    exec_with_cwd,
    file_exists,
    find_agent_sessions,
    terminate_pid,
)
from axism.providers.opencode_store import discover as oc_discover
from axism.providers.opencode_store import fragments as oc_fragments
from axism.providers.opencode_store import ops
from axism.providers.opencode_store.paths import (
    db_path,
    default_data_dir,
    is_default_root,
    is_session_id,
    short_id,
)

DELETE_FAILED_PREFIX = OPENCODE_DELETE_FAILED_PREFIX
_UNSUPPORTED_LAYOUT = "unsupported OpenCode database layout (aXism supports OpenCode 2)"


def _same_path(a: str | None, b: str) -> bool:
    if not a:
        return False
    if os.path.normpath(a) == os.path.normpath(b):
        return True
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return False


class OpenCodeProvider(ProviderBase):
    """Top-level chat sessions in ``$XDG_DATA_HOME/opencode/opencode.db``."""

    name = "opencode"
    label = "OpenCode"
    config_hint = "$XDG_DATA_HOME/opencode or ~/.local/share/opencode"
    capabilities = Capabilities(
        resume=True,
        slash_stop=True,
        stop=True,
        rename=True,
        move_sessions=True,
        move_projects=True,
        delete_sessions=True,
        delete_projects=True,
        fragments=False,
        memory_merge=False,
        purge_project=False,
    )

    def __init__(self, config_dir: Path | str | None = None) -> None:
        self._config_dir = (
            Path(config_dir).expanduser() if config_dir is not None else None
        )

    def config_root(self) -> Path:
        if self._config_dir is not None:
            return self._config_dir.resolve()
        return default_data_dir()

    def detect(self) -> bool:
        try:
            return file_exists(db_path(self.config_root())) or binary_on_path(
                ops.BINARY
            )
        except OSError:
            return False

    def status_note(self) -> str:
        root = self.config_root()
        status = oc_discover.layout_status(root)
        if status == "missing":
            return "database not found"
        if status == "unsupported":
            return _UNSUPPORTED_LAYOUT
        try:
            running = ops.service_info(root) is not None
        except OSError:
            running = False
        return "database ok · service " + ("running" if running else "not running")

    # -- discovery / live --------------------------------------------------

    def discover(self, root: Path | None = None) -> list[ProjectInfo]:
        return oc_discover.discover_all(root or self.config_root())

    def merge_live(
        self, root: Path | None = None, *, use_cli: bool = True
    ) -> dict[str, LiveSession]:
        """Sessions running in the background service or open in a client.

        Nothing is run when there is no readable database.
        """
        root = root or self.config_root()
        if oc_discover.layout_status(root) != "ok":
            return {}
        families = {
            sid: {sid, *kids}
            for sid, kids in oc_discover.family_map(root).items()
        }
        if not families:
            return {}
        owner = {m: top for top, members in families.items() for m in members}
        service = ops.service_info(root)
        service_pid = service.pid if service is not None else None
        active = ((ops.active_session_ids(root) or set()) & set(owner)) if use_cli else set()
        found = find_agent_sessions(set(owner), ops.PROCESS_NAMES)
        running = {owner[m] for m in active}
        clients: dict[str, int] = {}
        # A client on the top-level session wins; otherwise any family member's.
        for member, pid in sorted(found.items(), key=lambda kv: kv[0] != owner[kv[0]]):
            if pid != service_pid:
                clients.setdefault(owner[member], pid)
        live: dict[str, LiveSession] = {}
        for sid in sorted(running | set(clients)):
            live[sid] = LiveSession(
                session_id=sid,
                kind="interactive",
                state="running" if sid in running else "open",
                pid=clients.get(sid),
                source="opencode-service" if sid in running else "filesystem",
            )
        return live

    # -- open ----------------------------------------------------------------

    def open_command(
        self, session: SessionMeta, live: LiveSession | None = None
    ) -> tuple[list[str], str | None]:
        """The command that opens the session; a custom data folder is passed
        through ``env XDG_DATA_HOME=...`` so the printed command opens it too."""
        argv, env = ops.open_argv(self.config_root(), session.session_id, session.cwd)
        if not binary_on_path(ops.BINARY):
            raise FileNotFoundError(f"{ops.BINARY} not found on PATH")
        if env:
            argv = ["env", f"XDG_DATA_HOME={env['XDG_DATA_HOME']}", *argv]
        return argv, session.cwd or None

    def resume(self, session: SessionMeta, live: LiveSession | None = None) -> None:
        argv, workdir = self.open_command(session, live)
        exec_with_cwd(argv, workdir)

    # -- stop ----------------------------------------------------------------

    def can_stop(self, live: LiveSession | None) -> bool:
        return live is not None and (live.state == "running" or live.pid is not None)

    def slash_stop(
        self, session_id: str, live: LiveSession | None = None
    ) -> tuple[bool, str]:
        """OpenCode has no softer stop than :meth:`stop`."""
        return self.stop(session_id, live)

    def stop(
        self, session_id: str, live: LiveSession | None = None
    ) -> tuple[bool, str]:
        root = self.config_root()
        if live is None:
            live = self.merge_live(root).get(session_id)
        if live is None or not self.can_stop(live):
            return False, f"no running OpenCode session {short_id(session_id)}"
        family = [session_id, *oc_discover.family_map(root).get(session_id, [])]
        steps: list[tuple[bool, str]] = []
        service = ops.service_info(root)
        if live.state == "running":
            active = ops.active_session_ids(root)
            # Unknown (the service did not answer): interrupt the parent so the
            # failure is reported. Known and empty: it finished meanwhile.
            targets = [session_id] if active is None else sorted(active & set(family))
            if not targets:
                steps.append((True, f"{short_id(session_id)} no longer running"))
            for sid in targets:
                row = oc_discover.session_row(root, sid)
                if row is None:
                    steps.append((False, f"refused: session {short_id(sid)} not found"))
                else:
                    steps.append(ops.interrupt_session(root, row))
        pids: dict[int, str] = {}
        for member, pid in find_agent_sessions(set(family), ops.PROCESS_NAMES).items():
            pids.setdefault(pid, member)
        if live.pid is not None:
            pids.setdefault(live.pid, session_id)
        for pid, member in pids.items():
            if service is not None and service.pid == pid:
                steps.append((False, "refused: not signalling the OpenCode background service"))
            else:
                steps.append(terminate_pid(pid, expect=member))
        return all(ok for ok, _ in steps), "; ".join(msg for _, msg in steps)

    # -- inventory / rename ---------------------------------------------------

    def collect_fragments(
        self, session_id: str, *, project_slug: str | None = None
    ) -> FragmentInventory:
        return oc_fragments.collect_fragments(
            self.config_root(), session_id, project_slug=project_slug
        )

    def _row(self, session_id: str) -> oc_discover.SessionRow | None:
        root = self.config_root()
        if not is_session_id(session_id):
            return None
        row = oc_discover.session_row(root, session_id)
        if row is not None:
            return row
        found = self.find_session(session_id)
        return oc_discover.session_row(root, found[1].session_id) if found else None

    def session_details(self, session: SessionMeta) -> SessionDetails:
        details = super().session_details(session)
        usage = oc_discover.session_usage(self.config_root(), session.session_id)
        details.created = usage.get("created")  # type: ignore[assignment]
        details.model = usage.get("model")  # type: ignore[assignment]
        details.cost_usd = usage.get("cost_usd")  # type: ignore[assignment]
        details.tokens_in = usage.get("tokens_in")  # type: ignore[assignment]
        details.tokens_out = usage.get("tokens_out")  # type: ignore[assignment]
        return details

    def rename(self, session_id: str, title: str) -> tuple[bool, str]:
        row = self._row(session_id)
        if row is None:
            return False, f"refused: session {short_id(session_id)} not found"
        return ops.rename_session(self.config_root(), row, title)

    # -- move -------------------------------------------------------------------

    def move_sessions(
        self, sessions: list[SessionMeta], dest: str, *, dry_run: bool = False
    ) -> MoveResult:
        if not sessions:
            return MoveResult(False, "no sessions to move")
        dest_cwd = normalize_dest_cwd(dest)
        if not dest_cwd:
            return MoveResult(False, f"refused: invalid destination: {dest!r}")
        root = self.config_root()
        if not ops.can_move(root):
            refusal = (
                ops.MOVE_NEEDS_SERVICE
                if is_default_root(root)
                else ops.MOVE_NEEDS_DEFAULT_ROOT
            )
            return MoveResult(False, refusal, actions=[refusal], dest_cwd=dest_cwd)
        live_map = self.merge_live(root)
        actions: list[str] = []
        moved = unchanged = 0
        for session in sessions:
            sid = session.session_id
            short = short_id(sid)
            row = self._row(sid)
            if row is None:
                actions.append(f"refused: session {short} not found")
                continue
            if _same_path(row.directory, dest_cwd):
                actions.append(f"unchanged: {short} already in {dest_cwd}")
                unchanged += 1
                continue
            live = live_map.get(row.id)
            if live is not None and self.can_stop(live):
                if dry_run:
                    actions.append(f"would stop live session {short} first")
                else:
                    ok, msg = self.stop(row.id, live)
                    actions.append(f"{short}: stop: {msg}")
                    if not ok:
                        actions.append(f"{STOP_ABORT_PREFIX} ({short})")
                        continue
            if dry_run:
                actions.append(f"would move {short} → {dest_cwd}")
                moved += 1
                continue
            ok, msg = ops.move_session(root, row, dest_cwd)
            actions.append(msg)
            if ok:
                moved += 1
        failed = actions_failed(actions)
        if moved:
            verb = "would move" if dry_run else "moved"
            message = f"{verb} {moved} session(s) → {dest_cwd}"
            ok = True
        elif unchanged and not failed:
            message, ok = f"already in {dest_cwd}", True
        else:
            message, ok = "no sessions moved", False
        return MoveResult(ok, message, actions=actions, dest_cwd=dest_cwd)

    def move_projects(
        self,
        projects: list[ProjectInfo],
        dest: str,
        *,
        dry_run: bool = False,
        merge_memory: bool = True,
    ) -> MoveResult:
        sessions = [s for p in projects for s in p.sessions]
        return self.move_sessions(sessions, dest, dry_run=dry_run)

    # -- delete -------------------------------------------------------------------

    def _not_found(self, session_id: str, project_slug: str | None) -> DeletePlan:
        return DeletePlan(
            session_id=session_id,
            inventory=FragmentInventory(
                session_id=session_id, project_slug=project_slug
            ),
            blocked_reason="session not found",
            hard_blocked=True,
        )

    def _plan_known(
        self,
        session_id: str,
        project_slug: str | None,
        live: LiveSession | None,
        force: bool,
    ) -> DeletePlan:
        plan = DeletePlan(
            session_id=session_id,
            inventory=self.collect_fragments(session_id, project_slug=project_slug),
            live=live,
        )
        if live is not None and not force:
            where = f" (pid={live.pid})" if live.pid is not None else ""
            plan.blocked_reason = (
                f"an OpenCode process is using this session{where}; "
                "close it or use --force"
            )
        return plan

    def plan_delete(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        force: bool = False,
        use_cli: bool = True,
    ) -> DeletePlan:
        if not is_session_id(session_id):
            return self._not_found(session_id, project_slug)
        found = self.find_session(session_id)
        if not found:
            return self._not_found(session_id, project_slug)
        _proj, sess = found
        live = self.merge_live(use_cli=use_cli).get(sess.session_id)
        return self._plan_known(
            sess.session_id, project_slug or sess.project_slug, live, force
        )

    def execute_deletes(
        self,
        plans: list[DeletePlan],
        *,
        dry_run: bool = True,
        force: bool = False,
        stop_live: bool = True,
    ) -> list[str]:
        if not plans:
            return ["nothing to delete"]
        blocked = [p for p in plans if p.hard_blocked or (p.blocked_reason and not force)]
        if blocked:
            return [f"aborted {p.session_id}: {p.blocked_reason}" for p in blocked]

        root = self.config_root()
        actions: list[str] = []
        for plan in plans:
            sid = plan.session_id
            short = short_id(sid)
            live = plan.live
            if stop_live and live is not None and self.can_stop(live):
                if dry_run:
                    actions.append(f"{short}: would stop live session (state={live.state})")
                else:
                    ok, msg = self.stop(sid, live)
                    actions.append(f"{short}: stop: {msg}")
                    if not ok:
                        actions.append(f"{STOP_ABORT_PREFIX} ({short})")
                        continue
            if dry_run:
                actions.append(f"would delete OpenCode session {short} through OpenCode")
                continue
            row = oc_discover.session_row(root, sid)
            if row is None:
                actions.append(f"{DELETE_FAILED_PREFIX} {short} failed: session not found")
                continue
            ok, msg = ops.delete_session(root, row)
            if not ok:
                actions.append(f"{DELETE_FAILED_PREFIX} {short} failed: {msg}")
                continue
            actions.append(f"{short}: {msg}")
        return actions

    def plan_project_delete(
        self,
        slug_or_path: str,
        *,
        force: bool = False,
        use_cli: bool = True,
        keep_memory: bool = False,
    ) -> ProjectDeletePlan:
        root = self.config_root()
        projects = {p.slug: p for p in self.discover(root)}
        project = projects.get(slug_or_path)
        if project is None:
            resolved = str(Path(slug_or_path).expanduser())
            project = next(
                (p for p in projects.values() if p.cwd_guess and p.cwd_guess == resolved),
                None,
            )
        if project is None:
            return ProjectDeletePlan(
                slug=slug_or_path,
                project_path=db_path(root),
                blocked_reason=f"project not found: {slug_or_path}",
                hard_blocked=True,
            )
        live_map = self.merge_live(root, use_cli=use_cli)
        plan = ProjectDeletePlan(
            slug=project.slug,
            project_path=db_path(root),
            session_plans=[
                self._plan_known(s.session_id, project.slug, live_map.get(s.session_id), force)
                for s in project.sessions
            ],
            total_bytes=project.total_bytes,
        )
        live_blocked = [sp for sp in plan.session_plans if sp.blocked_reason and sp.live]
        if live_blocked and not force:
            plan.blocked_reason = (
                f"{len(live_blocked)} session(s) in use; close them or use --force"
            )
        return plan

    def execute_project_delete(
        self,
        plan: ProjectDeletePlan,
        *,
        dry_run: bool = True,
        force: bool = False,
        stop_live: bool = True,
    ) -> list[str]:
        if plan.hard_blocked or (plan.blocked_reason and not force):
            return [f"aborted: {plan.blocked_reason}"]
        if not plan.session_plans:
            return [f"project {plan.slug} has no sessions"]
        actions = self.execute_deletes(
            plan.session_plans, dry_run=dry_run, force=force, stop_live=stop_live
        )
        if any(a.startswith(STOP_ABORT_PREFIX) for a in actions):
            actions.append(
                project_kept_line(len(plan.session_plans), actions, has_project_dir=False)
            )
        return actions

    def purge_project(
        self, path: str | None = None, *, dry_run: bool = True, all_projects: bool = False
    ) -> tuple[int, str]:
        raise UnsupportedOperation(f"{self.label} has no project purge command")
