"""Scheduler for WorkItems: import -> map -> dispatch -> report.

Deterministic control flow (no LLM). Owns state machine, idempotency, bounded
concurrency, retry/backoff, cancellation, and event emission.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from .commitfilter import CommitFileFilter, load_extra_patterns
from .events import Event, EventBus
from .executor import Executor, ExecutorRegistry
from .gitmap import GitProjectRegistry
from .models import (
    ARCHIVED_SOURCE_STAGE,
    CONTAINER_STAGE,
    CommitResult,
    ItemKind,
    ItemStatus,
    SourceType,
    WorkItem,
)
from .projectmap import ProjectRegistry
from .report import parent_context_of, parent_title_of, write_report
from .sources import RawItem, parse_raw
from .store import TaskStore


@dataclass
class SchedulerSettings:
    max_concurrent: int = 1          # codebuddy is heavy; run one at a time by default
    retry_delays: tuple[float, ...] = (2.0, 8.0)
    reports_dir: Path = Path("data/reports")
    # Verification gate (all apply runs). Defaults to <data_dir>/verify.json,
    # i.e. the sibling of reports/. None means "derive from reports_dir".
    verify_config_file: Path | None = None
    verify_default_timeout_s: int = 300
    verify_total_timeout_s: int = 900
    # LLM 代码评审（仅自动链路）。provider 延迟调用，UI 里配置的模型改完即生效；
    # None = 未启用评审。评审结论并入上面的验证门禁。
    review_provider: Callable[[], Any] | None = None
    review_timeout_s: int = 120
    review_max_diff_lines: int = 2_000
    # P0-3 workspace/backup GC: merged/abandoned worktrees are deleted and
    # sync-backups are capped by age + count.
    gc_enabled: bool = True
    backup_retention_days: int = 7
    backup_max_per_task: int = 5


class Scheduler:
    def __init__(
        self,
        *,
        store: TaskStore,
        gitmap: GitProjectRegistry,
        executor: Executor | None = None,
        events: EventBus,
        settings: SchedulerSettings | None = None,
        projects: ProjectRegistry | None = None,
        commit_rules_file: Path | None = None,
        on_finished: Callable[[WorkItem, bool], None] | None = None,
        executors: ExecutorRegistry | None = None,
    ) -> None:
        self.store = store
        self.gitmap = gitmap
        self.projects = projects
        # Accept either a full registry or a single executor (legacy/tests).
        if executors is not None:
            self.executors = executors
        elif executor is not None:
            self.executors = ExecutorRegistry({executor.backend_id: executor})
        else:
            self.executors = ExecutorRegistry()
        # ``self.executor`` remains the default backend for compatibility with
        # callers that used it directly; dispatch uses ``self.executors``. It is
        # the registry's default backend (or None if no backend is configured).
        self.executor = self.executors.resolve(self.executors.default_backend_id) if self.executors.ids() else executor
        self.events = events
        self.settings = settings or SchedulerSettings()
        self.commit_rules_file = commit_rules_file
        self.on_finished = on_finished
        self._sem = asyncio.Semaphore(self.settings.max_concurrent)
        # One lock per repository path: same-repo auto tasks run one at a time
        # so they never branch from the same stale base and race to merge.
        self._repo_locks: dict[str, asyncio.Lock] = {}
        # 同父任务的串行锁：同一 parent_ref 的自动任务一次只跑一个，即便它们
        # 映射到了不同仓库（父子编排硬约束）。
        self._parent_locks: dict[str, asyncio.Lock] = {}
        self._cancel_flags: dict[str, bool] = {}
        self._seen_keys: dict[str, str] = {}
        self._tasks: set[asyncio.Task] = set()
        # Idempotency keys must cover the full history: capping at 2000 made
        # old dedupe_keys vanish once the task archive grew past that, so a
        # re-synced TAPD ticket could be imported a second time. Load every
        # persisted item at startup (store.count() is a cheap glob count).
        for item in self.store.list(limit=self.store.count()):
            if item.dedupe_key:
                self._seen_keys[item.dedupe_key] = item.id

    def _spawn(self, coro, *, name: str) -> asyncio.Task:
        """Track a background task so it can be cancelled on server shutdown."""
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def shutdown(self) -> None:
        """Stop live CLI processes, then cancel tasks and let them settle.

        Cancelling the coroutine alone used to orphan the CLI subprocess: it
        kept writing to the worktree after the server exited. Signal every
        backend's live processes first (best effort), then cancel the tasks.
        Work items stay persisted as queued/running and
        ``recover_interrupted()`` reaps them on the next start.
        """
        backends = [
            (bid, backend)
            for bid in list(self.executors.ids())
            if (backend := self.executors.get(bid)) is not None
        ]
        # Snapshot BEFORE cancelling tasks: _run_apply's finally unbinds the
        # process from _live_procs as soon as the task is cancelled, so a
        # post-gather lookup there would find nothing to kill.
        snapshots = []
        for backend_id, backend in backends:
            try:
                snap = backend.snapshot_live_processes()
                snapshots.append((backend_id, backend, snap))
            except Exception:  # shutdown must never stall
                logger.warning("快照后端 %s 的进程失败", backend_id, exc_info=True)
                continue
        for backend_id, backend, _snap in snapshots:
            try:
                backend.cancel_all_processes()  # SIGTERM + scheduled escalation
            except Exception:  # shutdown must never stall
                logger.warning("停止后端 %s 的进程失败", backend_id, exc_info=True)
        pending = [t for t in self._tasks if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        # A SIGTERM-ignoring child can outlive its coroutine and the deferred
        # call_later escalation may not fire before the loop closes. Kill any
        # survivor from the captured handles while the loop is still running.
        for backend_id, backend, snap in snapshots:
            try:
                backend.hard_kill_snapshot(snap)
            except Exception:  # shutdown must never stall
                logger.warning("强制停止后端 %s 的进程失败", backend_id, exc_info=True)

    # ── import ─────────────────────────────────────────────────────────────────
    def import_raw(
        self, raw: RawItem, *, exec_mode: str = "dry-run",
        auto_map: bool = True, model: str | None = None,
        executor: str | None = None, repo_name: str = "",
        stage_hint: str = "", archived: bool = False,
    ) -> tuple[WorkItem, bool]:
        """Create a WorkItem from a RawItem. Returns (item, created).

        Duplicate dedupe_keys are skipped (idempotent import): returns the
        existing item with created=False. When ``repo_name`` is given (e.g. the
        local project bound to this TAPD workspace), it is resolved first.
        ``archived`` marks an already-closed source item: it is kept for history
        but lands in a terminal state so it never enters the execution queue.
        """
        probe = parse_raw(raw, exec_mode=exec_mode)
        probe.model = model or None
        if executor:
            probe.executor = executor
        if archived:
            probe.status = ItemStatus.SKIPPED
            probe.exec_mode = "apply"
            probe.stage = stage_hint or ARCHIVED_SOURCE_STAGE
        existing_id = self._seen_keys.get(probe.dedupe_key)
        if existing_id:
            existing = self.store.get(existing_id)
            if existing:
                # 重新拉取时回填父子字段：旧工单没有 parent_ref，或 TAPD 侧关系变了。
                # 只写有值的 parent_ref，不主动清空（避免拉取窗口不含父时误清空）。
                parent_changed = bool(probe.parent_ref) and probe.parent_ref != existing.parent_ref
                if parent_changed:
                    existing.parent_ref = probe.parent_ref
                    self.store.save(existing)
                    self.events.publish(Event("item_updated", existing.id, {"item": existing.summary()}))
                # 来源明确带子需求：旧记录可能已按普通任务映射失败，这里纠正为容器。
                if probe.is_container and not archived and existing.status != ItemStatus.SKIPPED:
                    fixed = False
                    if not existing.is_container:
                        existing.is_container = True
                        fixed = True
                    if existing.repo is not None:
                        existing.repo = None
                        fixed = True
                    if existing.status != ItemStatus.DISCOVERED:
                        existing.status = ItemStatus.DISCOVERED
                        fixed = True
                    if existing.stage != CONTAINER_STAGE:
                        existing.stage = CONTAINER_STAGE
                        fixed = True
                    if fixed:
                        self.store.save(existing)
                # 来源从已关闭回到活跃（如 reopened）：复活到任务中心，重新映射。
                if (
                    not archived
                    and existing.status == ItemStatus.SKIPPED
                    and existing.stage == ARCHIVED_SOURCE_STAGE
                ):
                    existing.status = ItemStatus.DISCOVERED
                    existing.exec_mode = exec_mode
                    existing.stage = ""
                    item = existing
                    item.tapd_workspace_id = raw.tapd_workspace_id
                    item.parent_title = raw.parent_title
                    if repo_name:
                        repo = self.projects.resolve_name(repo_name) if self.projects else None
                        repo = repo or self.gitmap.resolve(name=repo_name)
                        if repo:
                            item.repo = repo
                            item.status = ItemStatus.READY
                            item.stage = f"已按 TAPD 项目绑定映射：{repo.name}"
                    elif auto_map:
                        self._map(item)
                    item.touch()
                    self.store.save(item)
                    self.events.publish(Event("item_updated", item.id, {"item": item.summary()}))
                return existing, False
        item = probe
        item.tapd_workspace_id = raw.tapd_workspace_id
        item.parent_title = raw.parent_title
        if stage_hint and not archived:
            item.stage = stage_hint
        if archived:
            pass  # 已归档条目不映射、不进执行队列
        elif item.is_container:
            # 父需求只做分组头：不映射、不进入执行队列。
            item.status = ItemStatus.DISCOVERED
            item.repo = None
            item.stage = CONTAINER_STAGE
        elif repo_name:
            # 显式指定（来自 TAPD workspace 绑定的本地项目），优先于文本匹配
            repo = self.projects.resolve_name(repo_name) if self.projects else None
            repo = repo or self.gitmap.resolve(name=repo_name)
            if repo:
                item.repo = repo
                item.status = ItemStatus.READY
                item.stage = f"已按 TAPD 项目绑定映射：{repo.name}"
            elif auto_map:
                self._map(item)
        elif auto_map:
            self._map(item)
        self.store.save(item)
        self._seen_keys[item.dedupe_key] = item.id
        self.events.publish(Event("item_imported", item.id, {"item": item.summary()}))
        return item, True

    def link_parent_relations(self) -> dict[str, int]:
        """根据各工单的 ``parent_ref`` 维护父子关系。

        - 把"被子需求引用为父"的工单标记为 container（不进执行队列）。
        - 回填每个父工单的 ``children_ids``（子工单的 source_ref 列表）。
        - 对只写了 ``children_ids``（来自父的 TAPD children_id）但子工单尚未建单/
          未写 parent_ref 的情况不强行建连；父子判定以子的 parent_ref 为准。

        返回 ``{"containers": 标记数, "linked": 回填子列表数}`` 供调用方观测。
        """
        items = self.store.list(limit=100000)
        by_ref = {it.source_ref: it for it in items if it.source_ref}
        children_by_parent: dict[str, list[str]] = {}
        for it in items:
            if it.parent_ref and it.parent_ref != it.source_ref:
                children_by_parent.setdefault(it.parent_ref, []).append(it.source_ref)

        containers = linked = 0
        for parent_ref, child_refs in children_by_parent.items():
            parent = by_ref.get(parent_ref)
            if parent is None:
                continue  # 孤儿：父未建单，保持子的 parent_ref，待后续单独补建
            child_refs = sorted(set(child_refs))
            changed = False
            if not parent.is_container:
                parent.is_container = True
                changed = True
                containers += 1
            # 容器只做分组：即便它之前被当成普通任务映射过，也纠正回不可执行态。
            if parent.status != ItemStatus.SKIPPED:
                if parent.status != ItemStatus.DISCOVERED:
                    parent.status = ItemStatus.DISCOVERED
                    changed = True
                if parent.repo is not None:
                    parent.repo = None
                    changed = True
                if parent.stage != CONTAINER_STAGE:
                    parent.stage = CONTAINER_STAGE
                    changed = True
            if parent.children_ids != child_refs:
                parent.children_ids = child_refs
                changed = True
                linked += 1
            if changed:
                self.store.save(parent)
        return {"containers": containers, "linked": linked}

    def create_manual(
        self,
        *,
        title: str,
        description: str = "",
        kind: str = ItemKind.TASK.value,
        repo_name: str = "",
        source_ref: str = "",
        git_url_hint: str = "",
        exec_mode: str = "dry-run",
        model: str | None = None,
        auto_map: bool = True,
        executor: str | None = None,
    ) -> WorkItem:
        """Create a WorkItem directly from user input (no email/TAPD needed).

        ``repo_name`` is resolved against the user-maintained project mappings
        first, then the git scan fallback. When given, the item is ready to run
        immediately; otherwise normal auto-mapping applies. Manual items get a
        unique dedupe key so re-running the same request creates a new item.
        """
        if not title.strip():
            raise ValueError("title is required")
        if exec_mode not in {"dry-run", "apply"}:
            raise ValueError("exec_mode must be 'dry-run' or 'apply'")

        raw = RawItem(
            source=SourceType.MANUAL,
            title=title.strip(),
            body=description,
            source_ref=source_ref.strip(),
            git_url_hint=git_url_hint.strip(),
        )
        item = parse_raw(raw, exec_mode=exec_mode)
        # Manual input is authoritative: keep the title and kind exactly as given.
        item.title = title.strip()
        item.kind = ItemKind(kind)
        item.model = model or None
        if executor:
            item.executor = executor
        item.dedupe_key = source_ref.strip() or f"manual:{secrets.token_hex(8)}"

        if repo_name:
            repo = self.projects.resolve_name(repo_name) if self.projects else None
            repo = repo or self.gitmap.resolve(name=repo_name)
            if repo is None:
                raise ValueError(f"unknown repo {repo_name!r}")
            item.repo = repo
            item.status = ItemStatus.READY
            item.stage = f"手动指定仓库：{repo.name}"
        elif auto_map:
            self._map(item)

        self.store.save(item)
        self._seen_keys[item.dedupe_key] = item.id
        self.events.publish(Event("item_imported", item.id, {"item": item.summary()}))
        return item

    def _map(self, item: WorkItem) -> None:
        # 1) explicit project-name/alias mapping first (user-maintained, wins)
        if self.projects:
            text = f"{item.title} {item.description} {item.git_url} {item.source_ref}"
            repo = self.projects.resolve_text(text)
            if repo:
                item.repo = repo
                item.status = ItemStatus.READY
                item.stage = f"已按项目映射：{repo.name}"
                return
        # 2) fall back to git-origin scan
        repo = self.gitmap.resolve(git_url=item.git_url, name=item.title)
        if repo:
            item.repo = repo
            item.status = ItemStatus.READY
            item.stage = f"已映射仓库：{repo.name}"
        else:
            item.status = ItemStatus.DISCOVERED
            item.stage = "未找到本地仓库（可在界面手动指定或新增项目映射）"

    def map_manually(self, item_id: str, repo_name: str) -> WorkItem:
        item = self.store.get(item_id)
        if item is None:
            raise ValueError(f"unknown item {item_id!r}")
        if item.is_container:
            raise ValueError("父需求容器只用于分组，不需要映射仓库")
        # project mapping first, then git scan
        repo = self.projects.resolve_name(repo_name) if self.projects else None
        repo = repo or self.gitmap.resolve(name=repo_name)
        if repo is None:
            raise ValueError(f"unknown repo {repo_name!r}")
        item.repo = repo
        item.status = ItemStatus.READY
        item.stage = f"已手动映射：{repo.name}"
        self.store.save(item)
        self.events.publish(Event("item_mapped", item.id, {"repo": repo.to_dict()}))
        return item

    # ── startup recovery ──────────────────────────────────────────────────────
    def recover_interrupted(self) -> int:
        """Reap items left in queued/running by a previous process.

        In-flight asyncio tasks die together with the old server process, so an
        item persisted as queued/running would otherwise show "执行中" forever
        with no worker behind it. Mark it failed with a clear reason so the user
        can simply re-run it. Returns the number of items recovered.
        """
        recovered = 0
        for item in self.store.list(limit=2000):
            if item.status not in (ItemStatus.QUEUED, ItemStatus.RUNNING):
                continue
            item.status = ItemStatus.FAILED
            item.stage = "已中断：服务重启，执行进程已退出"
            item.error = "服务重启导致执行中断，请重新执行（非代码失败）"
            if not item.finished_at:
                item.finished_at = _utcnow()
            self.store.save(item)
            self._cancel_flags[item.id] = True
            self.events.publish(
                Event("item_failed", item.id, {"error": item.error, "recovered": True})
            )
            recovered += 1
        return recovered

    # ── run / cancel ───────────────────────────────────────────────────────────
    def run(
        self,
        item_id: str,
        *,
        exec_mode: str | None = None,
        approved: bool = False,
        model: str | None = None,
        executor: str | None = None,
        _model_supplied: bool = True,
    ) -> WorkItem:
        item = self.store.get(item_id)
        if item is None:
            raise ValueError(f"unknown item {item_id!r}")
        if item.is_container:
            raise ValueError("父需求容器只用于分组，不进入执行队列")
        rearmable = item.is_dry_run_done() or item.is_retryable()
        if item.is_terminal() and not rearmable:
            raise ValueError(f"item {item_id} is already {item.status.value}")
        if rearmable:
            # Re-arm a dry-run / failed / cancelled item for a (repeated) run.
            # Clear stale result and report so the board never shows old output
            # while the new run is queued/running.
            previous = "dry-run" if item.is_dry_run_done() else item.status.value
            item.status = ItemStatus.READY
            item.result = None
            item.error = None
            item.report_path = ""
            item.started_at = None
            item.finished_at = None
            item.attempt = 0
            item.stage = f"已从 {previous} 转入重新执行"
        if item.repo is None:
            raise ValueError("no local repo mapped; map the item first")
        if exec_mode:
            if exec_mode not in {"dry-run", "apply"}:
                raise ValueError("exec_mode must be 'dry-run' or 'apply'")
            item.exec_mode = exec_mode
        if executor:
            item.executor = executor
        if _model_supplied:
            # The caller explicitly set the model for this run (None clears it
            # back to the global default); when omitted, keep the stored choice.
            item.model = (model or "").strip() or None
        if item.exec_mode == "apply" and not approved:
            raise ValueError("apply mode requires explicit approval (approved=true)")
        if item.exec_mode == "apply":
            # 人工 apply 同样要过验证门禁：执行器只写隔离 worktree，由 scheduler
            # 在门禁通过后做原子合并。否则代码会先落主仓，门禁就形同虚设。
            item.hold_merge = True
        item.status = ItemStatus.QUEUED
        item.error = None
        self.store.save(item)
        self._cancel_flags[item_id] = False
        self._spawn(self._execute(item_id), name=f"work-{item_id}")
        self.events.publish(Event("item_queued", item_id, {"exec_mode": item.exec_mode}))
        return item

    # ── 自动执行 / 自动合并 ───────────────────────────────────────────────────
    def run_auto(self, item_id: str, *, plan: str = "") -> WorkItem:
        """Dispatch an item from the automation path (no human click).

        结果先留在隔离 worktree；任务结束后由 scheduler 做**原子合并**：无冲突
        才写回主仓库工作区，有冲突则一个文件都不写、留在任务中心待处理。
        """
        item = self.store.get(item_id)
        if item is None:
            raise ValueError(f"unknown item {item_id!r}")
        if item.repo is None:
            raise ValueError("no local repo mapped; map the item first")
        item.auto = True
        item.approved_by = "auto"
        item.hold_merge = True
        item.merge_state = ""
        item.exec_mode = "apply"
        if plan:
            item.plan = plan
        item.auto_dispatched_at = _utcnow()
        self.store.save(item)
        self.events.publish(
            Event("auto_queued", item_id, {
                "repo": item.repo.name, "planner": bool(plan),
            })
        )
        return self.run(item_id, exec_mode="apply", approved=True, _model_supplied=False)

    def _record_merge(self, item: WorkItem, result, merge: dict[str, object]) -> bool:
        """Persist one atomic-merge attempt onto the item's result."""
        result.synced_files = list(merge.get("synced_files") or [])
        result.sync_deleted = list(merge.get("sync_deleted") or [])
        result.sync_skipped = list(merge.get("sync_skipped") or [])
        result.sync_conflicts = list(merge.get("sync_conflicts") or [])
        backup = merge.get("sync_backup_dir")
        result.sync_backup_dir = backup if isinstance(backup, str) else None
        error = merge.get("sync_error")
        result.sync_error = error if isinstance(error, str) else None
        ok = bool(merge.get("ok")) and not result.sync_conflicts and not result.sync_error
        item.merge_state = "merged" if ok else "blocked"
        return ok

    def _auto_merge(self, item: WorkItem, backend, result) -> bool:
        """Atomic merge of the worktree into the main repo working tree (no commit)."""
        if item.repo is None or not result.worktree:
            result.sync_error = "没有可用的 worktree 或主仓库，无法合并"
            item.merge_state = "blocked"
            return False
        worktree = Path(result.worktree).expanduser()
        if not worktree.is_dir():
            result.sync_error = "隔离 worktree 不存在，无法合并"
            item.merge_state = "blocked"
            return False
        merge = backend.sync_to_repo(
            worktree, item.repo.path, item_id=item.id,
            changed=list(result.changed_files), atomic=True,
        )
        return self._record_merge(item, result, merge)

    def merge_item(self, item_id: str, *, force: bool = False) -> WorkItem:
        """Retry the atomic merge for a task whose automatic merge was blocked.

        ``force=True`` is the explicit human override for a task that failed the
        verification gate; it is audited on the result and never used by the
        automatic path.
        """
        item = self.store.get(item_id)
        if item is None:
            raise ValueError(f"unknown item {item_id!r}")
        if item.result is None or not item.result.ok:
            raise ValueError("任务尚未成功执行，没有可合并的改动")
        if item.result.dry_run:
            raise ValueError("dry-run 没有可合并的代码")
        verification = item.result.verification
        verify_failed = bool(
            isinstance(verification, dict) and verification
            and not verification.get("ok", True)
        )
        if verify_failed and not force:
            raise ValueError("该任务验证未通过；如需跳过门禁请使用人工强制合并")
        if item.repo is None:
            raise ValueError("任务没有映射仓库")
        backend = self.executors.resolve(item.executor)
        if backend is None:
            raise ValueError("没有可用的执行后端")
        if verify_failed:
            item.result.verification = {
                **verification,
                "override": True,
                "override_by": "user",
                "override_at": _utcnow(),
            }
            item.approved_by = "user"
        ok = self._auto_merge(item, backend, item.result)
        files = len(item.result.synced_files) + len(item.result.sync_deleted)
        if ok:
            if verify_failed:
                item.status = ItemStatus.SUCCEEDED
                item.error = None
            item.stage = (
                f"完成（人工强制合并 {files} 个文件，已跳过验证）"
                if verify_failed else f"完成（已合并 {files} 个文件到工作区）"
            )
            # Changes are in the main repo — the worktree has served its purpose.
            self._gc_after_success(item, backend)
        else:
            conflicts = len(item.result.sync_conflicts)
            item.stage = (
                f"完成（合并冲突 {conflicts} 个文件，未写入主仓）"
                if conflicts else "完成（合并失败，未写入主仓）"
            )
        self.store.save(item)
        self.events.publish(
            Event("item_merged" if ok else "item_merge_blocked", item.id, {
                "conflicts": list(item.result.sync_conflicts),
                "error": item.result.sync_error,
            })
        )
        # 冲突后人工点「合并」成功时，补一次来源回填（例如把 TAPD 工单置为完成）。
        if ok and self.on_finished and item.source == SourceType.TAPD:
            try:
                self.on_finished(item, True)
            except Exception as error:  # noqa: BLE001 - 回填失败不影响合并结果
                logger.warning("合并成功后状态回填失败 %s：%s", item.id, error)
        return item

    async def verify_item(self, item_id: str, *, merge: bool = True) -> WorkItem:
        """Re-run the gate on an existing worktree, without re-running the agent.

        Useful after a flaky failure, an environment fix, or an N/A/config
        change. When the gate passes and the run holds its merge (all apply
        runs), it also performs the normal atomic merge.
        """
        item = self.store.get(item_id)
        if item is None:
            raise ValueError(f"unknown item {item_id!r}")
        if item.result is None or not item.result.ok:
            raise ValueError("任务尚未成功执行，没有可验证的 worktree")
        if item.result.dry_run:
            raise ValueError("dry-run 没有可验证的代码")
        worktree = item.result.worktree
        if not worktree or not Path(worktree).expanduser().is_dir():
            raise ValueError("worktree 不存在，请重新执行")
        if item.repo is None:
            raise ValueError("任务没有映射仓库")
        item.stage = "重新验证中…"
        self.store.save(item)
        self.events.publish(Event("item_verifying", item_id, {"rerun": True}))
        gate = await self._verify(item, item.result)
        item.result.verification = gate
        if not gate.get("ok"):
            item.status = ItemStatus.FAILED
            item.error = f"验证未通过：{gate.get('reason') or '未知原因'}"
            item.stage = item.error
            item.finished_at = _utcnow()
            item.report_path = write_report(
                item, self.settings.reports_dir, parent_title_of(item, self.store)
            )
            self.store.save(item)
            self.events.publish(
                Event("item_verify_failed", item_id, {"reason": item.error})
            )
            return item

        item.status = ItemStatus.SUCCEEDED
        item.error = None
        backend = self.executors.resolve(item.executor)
        if merge and item.hold_merge and backend is not None:
            merged = self._auto_merge(item, backend, item.result)
            if merged:
                files = len(item.result.synced_files) + len(item.result.sync_deleted)
                item.stage = f"完成（验证通过，已合并 {files} 个文件到工作区）"
                self._gc_after_success(item, backend)
            else:
                conflicts = len(item.result.sync_conflicts)
                item.stage = (
                    f"完成（验证通过，但合并冲突 {conflicts} 个文件，未写入主仓）"
                    if conflicts else "完成（验证通过，但合并失败，未写入主仓）"
                )
        else:
            item.stage = "验证通过（未合并）"
        item.finished_at = _utcnow()
        item.report_path = write_report(
            item, self.settings.reports_dir, parent_title_of(item, self.store)
        )
        self.store.save(item)
        self.events.publish(
            Event("item_succeeded", item_id, {
                "report_path": item.report_path, "verification": gate,
            })
        )
        if self.on_finished and item.source == SourceType.TAPD:
            try:
                done = bool(merge) and item.merge_state != "blocked"
                await asyncio.to_thread(self.on_finished, item, done)
            except Exception as exc:  # noqa: BLE001 - best effort
                logger.warning("工作项 %s 状态回填失败：%s", item_id, exc)
        return item

    # ── workspace / backup GC ─────────────────────────────────────────────────
    def _gc_backups(self, backend) -> int:
        """Prune one backend's sync-backups; GC is best effort, never fatal."""
        if not self.settings.gc_enabled:
            return 0
        collect = getattr(backend, "gc_backups", None)
        if collect is None:
            return 0
        try:
            out = collect(
                retention_days=self.settings.backup_retention_days,
                max_per_task=self.settings.backup_max_per_task,
            )
            return int(out.get("removed") or 0) if isinstance(out, dict) else 0
        except (OSError, ValueError, TypeError):
            logger.warning("清理同步备份失败", exc_info=True)
            return 0

    def _drop_worktree(self, item: WorkItem, backend, *, result=None) -> None:
        """Delete a worktree that is no longer needed (merged / abandoned)."""
        if not self.settings.gc_enabled:
            return
        cleanup = getattr(backend, "cleanup_worktree", None)
        if cleanup is None:
            return
        outcome = result if result is not None else item.result
        worktree = outcome.worktree if outcome is not None else None
        if not worktree:
            return
        try:
            cleanup(worktree, str(item.repo.path) if item.repo else None)
        except OSError:
            logger.warning("清理 worktree 失败：%s", worktree, exc_info=True)
            return
        if not Path(worktree).expanduser().exists():
            outcome.worktree = None

    def _gc_after_success(self, item: WorkItem, backend) -> None:
        """After a successful merge: drop the worktree and prune backups."""
        self._drop_worktree(item, backend)
        self._gc_backups(backend)

    def gc_artifacts(self) -> dict[str, int]:
        """Sweep worktrees/backups left behind by earlier runs (startup-safe).

        Only worktrees of items already merged or abandoned are removed; a
        blocked merge keeps its worktree so a human can still retry
        :meth:`merge_item`. Returns counts for logging/observability.
        """
        stats = {"worktrees": 0, "backups": 0}
        if not self.settings.gc_enabled:
            return stats
        for item in self.store.list(limit=100000):
            if item.merge_state != "merged" and item.status != ItemStatus.CANCELLED:
                continue
            if not item.result or not item.result.worktree:
                continue
            backend = self.executors.resolve(item.executor)
            if backend is None:
                continue
            before = item.result.worktree
            self._drop_worktree(item, backend)
            if item.result.worktree is None and before:
                stats["worktrees"] += 1
                self.store.save(item)
        for backend_id in self.executors.ids():
            backend = self.executors.get(backend_id)
            if backend is not None:
                stats["backups"] += self._gc_backups(backend)
        return stats

    def cancel(self, item_id: str) -> int:
        item = self.store.get(item_id)
        if item is None:
            raise ValueError(f"unknown item {item_id!r}")
        if item.is_terminal():
            return 0
        self._cancel_flags[item_id] = True
        item.status = ItemStatus.CANCELLED
        item.stage = "已取消"
        item.finished_at = _utcnow()
        backend = self.executors.resolve(item.executor)
        if backend is not None:
            # 关键：先让正在运行的子进程停下来。旧实现直接删 worktree，而
            # CLI 子进程可能仍在往里写，造成竞态与孤儿进程。signal 之后由
            # _execute 的取消分支在进程退出时清理 worktree；这里只处理
            # 「没有活进程」的情况（排队中 / 尚未启动），保持即时回收。
            live = bool(getattr(backend, "cancel_process", lambda _id: False)(item_id))
            if not live:
                self._drop_worktree(item, backend)
                self._gc_backups(backend)
        self.store.save(item)
        self.events.publish(Event("item_cancelled", item.id, {}))
        return 1

    # ── filtered commit ───────────────────────────────────────────────────────
    def _commit_filter(self) -> CommitFileFilter:
        extra = load_extra_patterns(self.commit_rules_file)
        return CommitFileFilter(extra_patterns=extra)

    def commit_preview(self, item_id: str) -> dict[str, object]:
        """Return the filtered commit plan for an item's worktree, no writes."""
        item = self.store.get(item_id)
        if item is None:
            raise ValueError(f"unknown item {item_id!r}")
        worktree = item.result.worktree if item.result else None
        if not worktree:
            raise ValueError("工作项还没有 apply 过的 worktree，无法提交")
        backend = self.executors.resolve(item.executor)
        if backend is None:
            raise ValueError("没有可用的执行后端，无法提交")
        changed = backend.changed_files(Path(worktree))
        plan = self._commit_filter().classify(changed)
        return {
            "item_id": item_id,
            "worktree": worktree,
            "total": len(changed),
            **plan.to_dict(),
        }

    def commit(self, item_id: str, *, message: str = "") -> CommitResult:
        """Commit the filtered changes of an item's worktree into its branch."""
        item = self.store.get(item_id)
        if item is None:
            raise ValueError(f"unknown item {item_id!r}")
        worktree = item.result.worktree if item.result else None
        if not worktree:
            raise ValueError("工作项还没有 apply 过的 worktree，无法提交")
        if not Path(worktree).is_dir():
            raise ValueError(f"worktree 不存在：{worktree}")

        backend = self.executors.resolve(item.executor)
        if backend is None:
            raise ValueError("没有可用的执行后端，无法提交")
        changed = backend.changed_files(Path(worktree))
        plan = self._commit_filter().classify(changed)
        commit_message = message.strip() or f"taskagent: {item.title}"

        outcome = backend.commit_files(
            Path(worktree), kept=plan.kept, message=commit_message
        )
        result = CommitResult(
            ok=bool(outcome.get("ok")),
            commit=str(outcome.get("commit") or ""),
            branch=str(outcome.get("branch") or ""),
            message=commit_message,
            kept=list(plan.kept),
            skipped=list(plan.skipped),
            skipped_reasons=dict(plan.skipped_reasons),
            error=outcome.get("error") if isinstance(outcome.get("error"), str) else None,
        )
        item.commit = result
        item.stage = f"已提交 {result.commit}" if result.ok else "提交失败"
        self.store.save(item)
        self.events.publish(
            Event("item_committed" if result.ok else "item_commit_failed", item_id, {
                "commit": result.commit,
                "error": result.error,
            })
        )
        return result

    def reconfigure(
        self,
        *,
        executors: ExecutorRegistry | None = None,
        max_concurrent: int | None = None,
    ) -> None:
        """Apply settings that used to be fixed at construction time.

        Called when the UI saves settings: the registry carries each backend's
        command / model / timeout / auto_sync, and ``max_concurrent`` sizes the
        concurrency gate. Runs already inside the gate keep the semaphore they
        entered with, so swapping it cannot corrupt in-flight accounting.
        """
        if executors is not None:
            self.executors = executors
            self.executor = (
                executors.resolve(executors.default_backend_id) if executors.ids() else None
            )
        if max_concurrent is not None:
            value = max(1, int(max_concurrent))
            if value != self.settings.max_concurrent:
                self.settings.max_concurrent = value
                self._sem = asyncio.Semaphore(value)

    def _repo_lock(self, item: WorkItem | None) -> asyncio.Lock | None:
        """Lock that serializes auto tasks targeting one local repository.

        Auto tasks branch from the repo's current state and the scheduler
        merges their result back atomically. Two of them on the same repo at
        once means the first merge moves the base out from under the second,
        which only discovers the conflict after burning a full agent run.
        Cross-repo tasks get different locks and stay parallel. Manual runs
        are left alone: they don't drive the scheduler's atomic merge.
        """
        if item is None or not item.auto or item.repo is None or not item.repo.path:
            return None
        key = str(Path(item.repo.path).expanduser().resolve())
        lock = self._repo_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._repo_locks[key] = lock
        return lock

    def _parent_lock(self, item: WorkItem | None) -> asyncio.Lock | None:
        """锁住同一父需求下的所有自动任务，避免兄弟子任务并行落同一仓库。"""
        if item is None or not item.auto or not item.parent_ref:
            return None
        key = item.parent_ref
        lock = self._parent_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._parent_locks[key] = lock
        return lock

    # ── verification gate ─────────────────────────────────────────────────────
    async def _review(self, item: WorkItem, result) -> Any | None:
        """LLM 评审 worktree 改动；未配置模型或任何异常都降级为「未评审」。"""
        from .reviewer import review_diff

        provider = self.settings.review_provider
        if provider is None:
            return None
        try:
            llm = provider()
        except Exception as error:  # noqa: BLE001 - 评审不得影响合并链路
            logger.warning("读取评审模型失败 %s：%s", item.id, error)
            return None
        if llm is None:
            return None
        try:
            return await review_diff(
                item,
                list(result.changed_files),
                result.worktree,
                llm=llm,
                timeout_s=self.settings.review_timeout_s,
                max_diff_lines=self.settings.review_max_diff_lines,
            )
        except Exception as error:  # noqa: BLE001 - best effort
            logger.warning("LLM 代码评审异常 %s：%s", item.id, error)
            return None

    async def _verify(self, item: WorkItem, result) -> dict[str, Any]:
        """Run the repository's checks inside the task worktree.

        Synchronous subprocess work is pushed to a thread so a slow test suite
        cannot freeze the event loop (SSE / UI / other tasks).
        """
        from .reviewer import apply_review, review_enabled
        from .verification import gc_verification_logs, run_gate

        config_file = self.settings.verify_config_file or (
            self.settings.reports_dir.parent / "verify.json"
        )
        repo = item.repo
        try:
            outcome = await asyncio.to_thread(
                run_gate,
                repo_name=repo.name if repo else "",
                repo_path=repo.path if repo else "",
                worktree=result.worktree,
                changed=list(result.changed_files),
                item_id=item.id,
                config_path=config_file,
                data_dir=self.settings.reports_dir.parent,
                default_timeout_s=self.settings.verify_default_timeout_s,
                total_timeout_s=self.settings.verify_total_timeout_s,
            )
            # LLM 评审结论并入门禁：block / 大 diff / 高风险转人工，不自动合并。
            if review_enabled():
                review = await self._review(item, result)
                outcome = apply_review(outcome, review)
            # Keep the verification log directory bounded; best effort.
            await asyncio.to_thread(
                gc_verification_logs, self.settings.reports_dir.parent
            )
            return outcome
        except Exception as error:
            logger.exception("验证执行异常 %s", item.id)
            return {
                "ok": False,
                "state": "error",
                "reason": f"验证执行异常：{type(error).__name__}: {error}",
                "checks": [],
                "duration_s": 0,
            }

    # ── execution ──────────────────────────────────────────────────────────────
    async def _execute(self, item_id: str) -> None:
        probe = self.store.get(item_id)
        repo_lock = self._repo_lock(probe)
        parent_lock = self._parent_lock(probe)
        async with AsyncExitStack() as stack:
            # 固定顺序：父锁 → 仓库锁 → 全局槽位。同父/同仓任务串行，跨父跨仓
            # 仍可并行；先拿局部锁再拿全局槽位，等待时不占并发额度。
            if parent_lock is not None:
                await stack.enter_async_context(parent_lock)
            if repo_lock is not None:
                await stack.enter_async_context(repo_lock)
            await stack.enter_async_context(self._sem)
            item = self.store.get(item_id)
            if item is None or item.is_terminal():
                return
            # 子任务执行前补齐父需求上下文，build_prompt 会把它注入提示词。
            item.parent_context = parent_context_of(item, self.store)

            def progress(value: float, stage: str) -> None:
                current = self.store.get(item_id)
                if current is None or current.status != ItemStatus.RUNNING:
                    return
                current.stage = stage
                self.store.save(current)
                self.events.publish(Event("item_progress", item_id, {"progress": value, "stage": stage}))

            def should_cancel() -> bool:
                return bool(self._cancel_flags.get(item_id))

            backend = self.executors.resolve(item.executor)
            if backend is None:
                item.status = ItemStatus.FAILED
                item.stage = "失败"
                item.error = "没有可用的执行后端：请先在系统设置中配置并启用至少一个后端"
                item.finished_at = _utcnow()
                self.store.save(item)
                self.events.publish(Event("item_failed", item_id, {"error": item.error}))
                return
            item.status = ItemStatus.RUNNING
            item.attempt += 1
            item.started_at = item.started_at or _utcnow()
            item.stage = f"开始执行（{backend.label}）"
            self.store.save(item)
            self.events.publish(Event("item_started", item_id, {
                "attempt": item.attempt, "executor": backend.backend_id,
            }))

            result = await backend.run(item, progress=progress, should_cancel=should_cancel)

            if should_cancel():
                # cancel() 已经 signal 了活进程；此刻 backend.run() 已返回、
                # 子进程确已退出，worktree 回收是安全的（cancel() 自身在进程
                # 仍存活时跳过删除，正是为了避开写入竞态）。
                item.result = result
                item.status = ItemStatus.CANCELLED
                item.stage = "已取消"
                item.finished_at = _utcnow()
                self._drop_worktree(item, backend)
                self.store.save(item)
                self.events.publish(Event("item_cancelled", item_id, {}))
                return

            item.result = result
            verify_failure = ""
            if result.ok:
                # Keep RUNNING until the gate/merge decision is made: callers
                # poll on status, so flipping to SUCCEEDED before verification
                # would let them read a half-finished task ("验证中…").
                if result.dry_run:
                    item.stage = "完成（dry-run）"
                elif item.hold_merge:
                    # 验证门禁（人工 apply 与自动链路都走）：先在隔离 worktree 里
                    # 跑仓库声明的检查。验证不过（含未配置、跑不起来、超时、测试
                    # 文件被改）就一个文件都不写回主仓，worktree 保留供排查。
                    item.stage = "验证中…"
                    self.store.save(item)
                    self.events.publish(Event("item_verifying", item_id, {}))
                    gate = await self._verify(item, result)
                    result.verification = gate
                    if not gate.get("ok"):
                        verify_failure = str(gate.get("reason") or "未知原因")
                        item.stage = f"验证未通过：{verify_failure}"
                    else:
                        # 门禁通过：scheduler 负责原子合并，无冲突才写入主仓工作区。
                        merged = self._auto_merge(item, backend, result)
                        if merged:
                            files = len(result.synced_files) + len(result.sync_deleted)
                            note = ""
                            if gate.get("state") == "disabled":
                                note = "，验证已禁用"
                            elif gate.get("state") == "passed_with_warnings":
                                note = "，验证有警告"
                            item.stage = (
                                f"完成（已自动合并 {files} 个文件到工作区{note}）"
                            )
                            # Merge landed: reclaim the isolated worktree now.
                            self._gc_after_success(item, backend)
                        else:
                            conflicts = len(result.sync_conflicts)
                            item.stage = (
                                f"完成（合并冲突 {conflicts} 个文件，未写入主仓）"
                                if conflicts else "完成（合并失败，未写入主仓）"
                            )
                elif result.sync_error:
                    # Apply itself worked, but the mirror into the main repo
                    # failed — surface it so the user doesn't assume the code
                    # is in their checkout.
                    item.stage = "完成（同步回主仓库失败，请查看报告）"
                elif result.sync_conflicts:
                    # Diverged files were left untouched to protect later
                    # main-repo edits — make the partial sync visible.
                    n = len(result.sync_conflicts)
                    item.stage = f"完成（{n} 个文件冲突未覆盖，请查看报告）"
                elif result.synced_files or result.sync_deleted:
                    n = len(result.synced_files) + len(result.sync_deleted)
                    item.stage = f"完成（已同步 {n} 个文件到主仓库）"
                else:
                    item.stage = "完成"
                if verify_failure:
                    # 验证没过 = 这单没有交付；置 failed 以便留在任务中心可重试，
                    # 而不是归档进历史。
                    item.status = ItemStatus.FAILED
                    item.error = f"验证未通过：{verify_failure}"
                else:
                    item.status = ItemStatus.SUCCEEDED
                    item.error = None
            else:
                if item.attempt < item.max_attempts and _is_retryable(result.error):
                    delay = self.settings.retry_delays[
                        min(item.attempt - 1, len(self.settings.retry_delays) - 1)
                    ]
                    item.status = ItemStatus.READY
                    item.stage = f"重试前等待 {delay:g}s"
                    item.error = result.error
                    self.store.save(item)
                    self.events.publish(Event("item_retry", item_id, {"delay": delay}))
                    await asyncio.sleep(delay)
                    self._spawn(self._execute(item_id), name=f"work-{item_id}-retry")
                    return
                item.status = ItemStatus.FAILED
                item.stage = "失败"
                item.error = result.error
            item.finished_at = _utcnow()
            item.report_path = write_report(
                item, self.settings.reports_dir, parent_title_of(item, self.store)
            )
            self.store.save(item)
            event = "item_succeeded" if result.ok else "item_failed"
            if result.ok and result.verification and not result.verification.get("ok", True):
                event = "item_verify_failed"
            self.events.publish(
                Event(event, item_id, {
                    "report_path": item.report_path,
                    "error": item.error,
                    "verification": result.verification or {},
                })
            )
            # Optional write-back (e.g. TAPD status). Runs in a thread so a slow
            # external API can't stall the scheduler; failures never mask the
            # local result.
            if self.on_finished and item.source == SourceType.TAPD:
                try:
                    # 合并未写入主仓时不要把 TAPD 工单标记为完成，避免“归档”了
                    # 但代码根本没进工作区。
                    done = (
                        bool(result.ok)
                        and item.merge_state != "blocked"
                        and bool(result.verification.get("ok", True))
                    )
                    await asyncio.to_thread(self.on_finished, item, done)
                except Exception as exc:  # noqa: BLE001 - write-back is best effort
                    logger.warning("工作项 %s 状态回填失败：%s", item_id, exc)


def _is_retryable(error: str | None) -> bool:
    if not error:
        return False
    lowered = error.lower()
    return any(word in lowered for word in ("timeout", "timed out", "rate", "429", "503", "502"))


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
