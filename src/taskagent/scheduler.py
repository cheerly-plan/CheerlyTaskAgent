"""Scheduler for WorkItems: import -> map -> dispatch -> report.

Deterministic control flow (no LLM). Owns state machine, idempotency, bounded
concurrency, retry/backoff, cancellation, and event emission.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

from .commitfilter import CommitFileFilter, load_extra_patterns
from .events import Event, EventBus
from .executor import Executor, ExecutorRegistry
from .gitmap import GitProjectRegistry
from .models import (
    ARCHIVED_SOURCE_STAGE,
    CommitResult,
    ItemKind,
    ItemStatus,
    SourceType,
    WorkItem,
)
from .projectmap import ProjectRegistry
from .report import parent_title_of, write_report
from .review import build_review, diff_stat, review_fields
from .sources import RawItem, parse_raw
from .store import TaskStore
from .todos import (
    TODO_BLOCKED,
    TODO_DISCARDED,
    TODO_MERGED,
    TODO_PENDING,
    TodoEntry,
    TodoStore,
)


@dataclass
class SchedulerSettings:
    max_concurrent: int = 1          # codebuddy is heavy; run one at a time by default
    retry_delays: tuple[float, ...] = (2.0, 8.0)
    reports_dir: Path = Path("data/reports")


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
        todos: TodoStore | None = None,
        on_auto_done: Callable[[str], None] | None = None,
    ) -> None:
        self.store = store
        self.gitmap = gitmap
        self.projects = projects
        # 「我的待办」存储：自动执行成功且 hold_merge 的结果落到这里等人合并。
        self.todos = todos
        # 自动任务结束后通知引擎收口批次（由 API 注入，测试可不传）。
        self.on_auto_done = on_auto_done
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
        self._cancel_flags: dict[str, bool] = {}
        self._seen_keys: dict[str, str] = {}
        self._tasks: set[asyncio.Task] = set()
        for item in self.store.list(limit=2000):
            if item.dedupe_key:
                self._seen_keys[item.dedupe_key] = item.id

    def _spawn(self, coro, *, name: str) -> asyncio.Task:
        """Track a background task so it can be cancelled on server shutdown."""
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def shutdown(self) -> None:
        """Cancel in-flight execution tasks and let them settle (best effort).

        Cancelled tasks raise CancelledError inside ``_execute``; the work item
        remains persisted as queued/running and ``recover_interrupted()`` reaps
        it on the next start.
        """
        pending = [t for t in self._tasks if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

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
        item.status = ItemStatus.QUEUED
        item.error = None
        self.store.save(item)
        self._cancel_flags[item_id] = False
        self._spawn(self._execute(item_id), name=f"work-{item_id}")
        self.events.publish(Event("item_queued", item_id, {"exec_mode": item.exec_mode}))
        return item

    # ── 自动执行 / 我的待办 ───────────────────────────────────────────────────
    def run_auto(self, item_id: str, *, batch_id: str = "", plan: str = "") -> WorkItem:
        """Dispatch a READY item from the automation engine (no human click).

        The approval is granted *inside* the gate path only: callers outside the
        automation engine still have to pass ``approved=true`` themselves. The
        result is held for review (``hold_merge``), so nothing is written into
        the main working tree until a human clicks 合并.
        """
        item = self.store.get(item_id)
        if item is None:
            raise ValueError(f"unknown item {item_id!r}")
        if item.repo is None:
            raise ValueError("no local repo mapped; map the item first")
        item.auto = True
        item.approved_by = "auto"
        item.hold_merge = True
        item.exec_mode = "apply"
        if batch_id:
            item.auto_batch_id = batch_id
        if plan:
            item.plan = plan
        item.auto_dispatched_at = _utcnow()
        self.store.save(item)
        self.events.publish(
            Event("auto_queued", item_id, {
                "batch_id": item.auto_batch_id, "repo": item.repo.name,
                "planner": bool(plan),
            })
        )
        return self.run(item_id, exec_mode="apply", approved=True, _model_supplied=False)

    def _create_todo(self, item: WorkItem) -> TodoEntry | None:
        """Turn a successful held-for-review run into a 我的待办 entry."""
        if self.todos is None or item.result is None:
            return None
        result = item.result
        if not (item.hold_merge and result.ok and not result.dry_run):
            return None
        existing = (
            self.todos.get(item.todo_id) if item.todo_id else self.todos.find_by_item(item.id)
        )
        if existing is not None:
            if item.todo_id != existing.id:
                item.todo_id = existing.id
                self.store.save(item)
            return existing

        changed = list(result.changed_files)
        review_data = build_review(result.stdout or "", changed)
        fields = review_fields(item, review_data)
        blockers = list(fields.pop("blockers", []))
        can_merge = bool(fields.pop("can_merge", True))

        # 确定性否决优先于模型建议：没有改动 / worktree 丢了 / 没有仓库，
        # 这些都不该显示成「可以合并」。
        if not changed:
            can_merge = False
            blockers.append("没有产生任何改动")
        worktree = result.worktree or ""
        if not worktree or not Path(worktree).expanduser().is_dir():
            can_merge = False
            blockers.append("隔离 worktree 不存在，无法合并")
        if item.repo is None:
            can_merge = False
            blockers.append("没有映射到本地仓库")
        blockers = list(dict.fromkeys(blockers))

        todo = TodoEntry(
            id=TodoStore.new_todo_id(),
            item_id=item.id,
            batch_id=item.auto_batch_id,
            title=item.title,
            repo=item.repo.name if item.repo else "",
            repo_path=item.repo.path if item.repo else "",
            status=TODO_PENDING,
            can_merge=can_merge and not blockers,
            blockers=blockers,
            diff_stat=diff_stat(worktree, changed),
            **fields,
        )
        self.todos.save(todo)
        item.todo_id = todo.id
        self.store.save(item)
        self.events.publish(Event("todo_created", item.id, {"todo": todo.summary_row()}))
        return todo

    def backfill_todos(self) -> int:
        """启动补偿：自动跑成功但（进程中断等）没生成待办的任务补一条。"""
        if self.todos is None:
            return 0
        created = 0
        for item in self.store.list(limit=5000):
            if not (item.hold_merge and item.result and item.result.ok and not item.result.dry_run):
                continue
            if item.todo_id and self.todos.get(item.todo_id):
                continue
            if self.todos.find_by_item(item.id):
                continue
            if self._create_todo(item):
                created += 1
        return created

    def merge_todo(self, todo_id: str) -> TodoEntry:
        """Merge a todo's worktree changes into the main repo *working tree*.

        Reuses the executor's three-way safe sync (never commits/pushes). A
        conflict or path problem leaves the todo blocked instead of pretending
        it merged; already-synced files are backed up before overwrite.
        """
        if self.todos is None:
            raise ValueError("todo store not configured")
        todo = self.todos.get(todo_id)
        if todo is None:
            raise ValueError(f"unknown todo {todo_id!r}")
        if todo.status == TODO_DISCARDED:
            raise ValueError("待办已放弃，不能合并")
        if todo.status == TODO_MERGED:
            raise ValueError("待办已合并")
        item = self.store.get(todo.item_id)
        if item is None or item.result is None or not item.result.ok:
            raise ValueError("任务结果不存在或未成功，无法合并")
        if item.result.dry_run:
            raise ValueError("dry-run 没有可合并的代码")
        if item.repo is None:
            raise ValueError("任务没有映射仓库")
        worktree = Path(item.result.worktree or "").expanduser()
        if not worktree.is_dir():
            raise ValueError("隔离 worktree 不存在（可能已被清理），无法合并")
        backend = self.executors.resolve(item.executor)
        if backend is None:
            raise ValueError("没有可用的执行后端")

        sync = backend.sync_to_repo(
            worktree, item.repo.path, item_id=item.id,
            changed=list(item.result.changed_files),
        )
        result = item.result
        result.synced_files = list(sync.get("synced_files") or [])
        result.sync_deleted = list(sync.get("sync_deleted") or [])
        result.sync_skipped = list(sync.get("sync_skipped") or [])
        result.sync_conflicts = list(sync.get("sync_conflicts") or [])
        backup = sync.get("sync_backup_dir")
        result.sync_backup_dir = backup if isinstance(backup, str) else None
        error = sync.get("sync_error")
        result.sync_error = error if isinstance(error, str) else None
        item.result = result
        self.store.save(item)

        ok = bool(sync.get("ok")) and not result.sync_error and not result.sync_conflicts
        todo.merge = {
            "synced_files": list(result.synced_files),
            "deleted": list(result.sync_deleted),
            "skipped": list(result.sync_skipped),
            "conflicts": list(result.sync_conflicts),
            "backup_dir": result.sync_backup_dir,
            "error": result.sync_error,
        }
        todo.status = TODO_MERGED if ok else TODO_BLOCKED
        todo.merged_at = _utcnow() if ok else ""
        if ok:
            # 冲突解决后重试成功：旧的阻塞项已经不再成立，清掉避免误导。
            todo.blockers = []
            todo.can_merge = True
        else:
            reason = result.sync_error or (
                f"{len(result.sync_conflicts)} 个文件冲突未写入" if result.sync_conflicts
                else "同步失败"
            )
            todo.blockers = list(dict.fromkeys([*todo.blockers, reason]))
            todo.can_merge = False
        self.todos.save(todo)
        self.events.publish(
            Event("todo_merged" if ok else "todo_blocked", item.id, {"todo": todo.summary_row()})
        )
        return todo

    def discard_todo(self, todo_id: str) -> TodoEntry:
        """Drop a todo and best-effort delete its isolated worktree."""
        if self.todos is None:
            raise ValueError("todo store not configured")
        todo = self.todos.get(todo_id)
        if todo is None:
            raise ValueError(f"unknown todo {todo_id!r}")
        if todo.status == TODO_MERGED:
            raise ValueError("已合并的待办不能放弃")
        item = self.store.get(todo.item_id)
        worktree = None
        if item is not None and item.result and item.result.worktree:
            worktree = Path(item.result.worktree).expanduser()
            if worktree.is_dir():
                removed = False
                if item.repo is not None:
                    proc = subprocess.run(
                        ["git", "-C", item.repo.path, "worktree", "remove", "--force", str(worktree)],
                        capture_output=True, check=False, timeout=30,
                    )
                    removed = proc.returncode == 0
                if not removed:
                    shutil.rmtree(worktree, ignore_errors=True)
        todo.status = TODO_DISCARDED
        todo.merged_at = ""
        self.todos.save(todo)
        self.events.publish(Event("todo_discarded", todo.item_id, {"todo": todo.summary_row()}))
        return todo

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

    # ── execution ──────────────────────────────────────────────────────────────
    async def _execute(self, item_id: str) -> None:
        # 无论成功、失败、取消还是重试，结束后都通知引擎检查批次是否收口。
        try:
            await self._execute_inner(item_id)
        finally:
            self._notify_auto_done(item_id)

    def _notify_auto_done(self, item_id: str) -> None:
        if self.on_auto_done is None:
            return
        item = self.store.get(item_id)
        if item is None or not item.auto or not item.auto_batch_id:
            return
        try:
            self.on_auto_done(item_id)
        except Exception as error:  # noqa: BLE001 - 批次收口失败不该影响执行结果
            logger.warning("批次收口失败 %s：%s", item_id, error)

    async def _execute_inner(self, item_id: str) -> None:
        async with self._sem:
            item = self.store.get(item_id)
            if item is None or item.is_terminal():
                return

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
                item.status = ItemStatus.CANCELLED
                item.stage = "已取消"
                item.finished_at = _utcnow()
                self.store.save(item)
                self.events.publish(Event("item_cancelled", item_id, {}))
                return

            item.result = result
            if result.ok:
                item.status = ItemStatus.SUCCEEDED
                if result.dry_run:
                    item.stage = "完成（dry-run）"
                elif item.hold_merge:
                    item.stage = "完成（已进入我的待办，待合并）"
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
            # 自动执行的结果先落待办（解析 LLM 的内嵌评审），再写报告，
            # 这样报告里能带上「已进入我的待办」与合并建议。
            if result.ok and not result.dry_run and item.hold_merge:
                try:
                    self._create_todo(item)
                except Exception as error:  # noqa: BLE001 - 待办失败不该吞掉执行结果
                    logger.warning("生成待办失败 %s：%s", item_id, error)
                    item.stage = "完成（生成待办失败，请查看日志）"
            item.report_path = write_report(
                item, self.settings.reports_dir, parent_title_of(item, self.store)
            )
            self.store.save(item)
            event = "item_succeeded" if result.ok else "item_failed"
            self.events.publish(
                Event(event, item_id, {"report_path": item.report_path, "error": item.error})
            )
            # Optional write-back (e.g. TAPD status). Runs in a thread so a slow
            # external API can't stall the scheduler; failures never mask the
            # local result.
            if self.on_finished and item.source == SourceType.TAPD:
                try:
                    await asyncio.to_thread(self.on_finished, item, bool(result.ok))
                except Exception as exc:  # noqa: BLE001 - write-back is best effort
                    logger.warning("工作项 %s 状态回填失败：%s", item_id, exc)


def _is_retryable(error: str | None) -> bool:
    if not error:
        return False
    lowered = error.lower()
    return any(word in lowered for word in ("timeout", "timed out", "rate", "429", "503", "502"))


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
