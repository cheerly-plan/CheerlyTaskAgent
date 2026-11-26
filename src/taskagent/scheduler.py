"""Scheduler for WorkItems: import -> map -> dispatch -> report.

Deterministic control flow (no LLM). Owns state machine, idempotency, bounded
concurrency, retry/backoff, cancellation, and event emission.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
from .plans import (
    ERR_PLAN_MISMATCH,
    ERR_PLAN_NOT_CONFIRMED,
    PlanCandidate,
    PlanError,
    is_complex,
    normalise_plan_layer,
)
from .projectmap import ProjectRegistry
from .report import parent_context_of, parent_title_of, write_report
from .sources import RawItem, parse_raw
from .spec import rule_spec
from .store import TaskStore
from .usage import TokenUsage, UsageLedger


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
    # 把 spec 验收标准编译为可执行断言并据此判定（默认关闭，保持旧门禁行为）。
    # 也可在 verify.json 的 repos.<name>.assertions 中开启/覆盖。
    assertions_enabled: bool = False
    assertions_config: dict[str, Any] | None = None
    # LLM 代码评审（仅自动链路）。provider 延迟调用，UI 里配置的模型改完即生效；
    # None = 未启用评审。评审结论并入上面的验证门禁。
    review_provider: Callable[[], Any] | None = None
    review_timeout_s: int = 120
    review_max_diff_lines: int = 2_000
    # 任务规格（TaskSpec）：自动链路执行前用后端 Agent 模型把工单变成可验证目标。
    # provider 与评审共用同一份「模型配置」；None = 只用规则版规格。
    spec_provider: Callable[[], Any] | None = None
    spec_timeout_s: int = 60
    # 执行-反馈循环：自动任务门禁/评审未通过时，带着问题再修几轮。
    # 1 = 关闭循环；人工 apply 固定 1 轮。
    max_rounds: int = 2
    # Token 预算（0 = 不限）。任务预算是「本次执行」累计，日预算跨任务累计。
    task_token_budget: int = 0
    daily_token_budget: int = 0
    usage_file: Path | None = None
    # 交付（默认关闭；仓库映射的 delivery 字段可单独覆盖）。
    delivery_mode: str = "off"
    delivery_remote: str = "origin"
    delivery_base: str = ""
    delivery_provider: str = ""
    delivery_api_base: str = ""
    delivery_merge: str = "manual"
    ci_timeout_s: int = 1800
    ci_poll_interval_s: int = 15
    # 动态读取「系统设置 → 交付」的全局默认；None = 用上面的静态字段（测试/嵌入）。
    delivery_globals: Callable[[], dict[str, Any]] | None = None
    # P0-3 workspace/backup GC: merged/abandoned worktrees are deleted and
    # sync-backups are capped by age + count.
    gc_enabled: bool = True
    backup_retention_days: int = 7
    backup_max_per_task: int = 5
    # 自动执行准入/熔断/重排配置（系统设置 → 自动执行）。None = 用默认值。
    automation_policy: Callable[[], dict[str, Any]] | None = None
    # 方案层：开启后复杂需求必须存在 confirmed 方案才能执行。
    # None / 默认配置 = 关闭，沿用既有单方案执行路径。
    plan_store: Any | None = None
    plan_policy: Callable[[], dict[str, Any]] | None = None
    # 执行租约 TTL（秒）：心跳每 ttl/3 续期；进程崩溃后到期即可被重排。
    lease_ttl_s: float = 180.0
    # 跨任务记忆召回：执行前调用，返回 {similar, failures, stats} 注入提示词。
    memory_provider: Callable[[WorkItem], dict[str, Any]] | None = None


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
        on_item_done: Callable[[WorkItem], None] | None = None,
        on_children_done: Callable[[WorkItem, list[WorkItem], bool], None] | None = None,
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
        # 任何终态任务都会回调一次（跨任务记忆记账）；由 api 接到 TaskMemory。
        self.on_item_done = on_item_done
        # 拆解出的子任务全部结束后回调一次（父需求汇总回填）。
        self.on_children_done = on_children_done
        # 自动执行收尾钩子（熔断记账）。由 api 接到 admission.CircuitBreaker。
        self.auto_outcome_hook: Callable[[WorkItem], None] | None = None
        # 本调度进程实例 id：区分「自己还在跑」与「上一个进程留下的租约」。
        self.lease_owner = f"pid{os.getpid()}-{secrets.token_hex(4)}"
        # 启动恢复时被自动重排的任务 id；由 api 在自动化引擎就绪后派发。
        self.recovered_items: list[str] = []
        # 本进程内仍持有 wrapper 的任务：租约巡检不得回收它们，避免执行中途
        # 的旧 item 快照把 lease 写成过期值后误判。
        self._live_runs: set[str] = set()
        self._sem = asyncio.Semaphore(self.settings.max_concurrent)
        # One lock per repository path: same-repo auto tasks run one at a time
        # so they never branch from the same stale base and race to merge.
        # Token 运行账本：按天累计，供预算与报告使用。
        self.ledger = UsageLedger(
            self.settings.usage_file
            or (self.settings.reports_dir.parent / "usage.json")
        )
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
        item.spec = rule_spec(item).to_dict()
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
                if parent.stage != CONTAINER_STAGE and not parent.aggregated:
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
        item.spec = rule_spec(item).to_dict()
        item.model = model or None
        if executor:
            item.executor = executor
            item.executor_locked = True
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
    def _automation_policy(self) -> dict[str, Any]:
        from .admission import normalise_automation

        try:
            raw = self.settings.automation_policy() if self.settings.automation_policy else {}
        except Exception:
            logger.warning("读取自动执行配置失败，使用默认值", exc_info=True)
            raw = {}
        return normalise_automation(raw)

    def _requeue(self, item: WorkItem, *, reason: str) -> None:
        """Put an interrupted auto task back in the queue for one more try.

        The worktree is intentionally kept: the next run continues from whatever
        partial edits exist instead of paying for the same work twice.
        """
        item.recovered += 1
        item.status = ItemStatus.READY
        item.stage = f"{reason}（第 {item.recovered} 次）"
        item.error = reason
        item.result = None
        item.report_path = ""
        item.finished_at = None
        item.started_at = None
        item.hold_merge = False
        item.auto_dispatched_at = ""
        item.lease_owner = ""
        item.lease_until = ""
        item.lease_heartbeat_at = ""
        self.store.save(item)
        self.events.publish(
            Event("item_requeued", item.id, {"reason": reason, "recovered": item.recovered})
        )

    def recover_interrupted(self) -> dict[str, Any]:
        """Reap queued/running items left behind by a previous process.

        P1 semantics:

        - **auto tasks** are re-queued automatically (bounded by
          ``automation.max_recoveries``) instead of being failed; the caller
          dispatches them once the automation engine is ready.
        - **manual tasks** stay failed with a clear reason, because a human
          click was the approval and silently re-running it is surprising.
        - **CI waiters** keep ``ci_pending``; the caller resumes watching them.

        Returns ``{requeued, failed, ci_pending, total}`` so the app can act on
        each group instead of only logging a number.
        """
        policy = self._automation_policy()
        max_recoveries = int(policy.get("max_recoveries", 1))
        requeued: list[str] = []
        ci_pending: list[str] = []
        failed = 0
        for item in self.store.list(limit=self.store.count()):
            if item.status in (ItemStatus.QUEUED, ItemStatus.RUNNING):
                self._cancel_flags[item.id] = True
                if item.auto and item.recovered < max_recoveries:
                    self._requeue(item, reason="服务重启导致执行中断，已自动重新排队")
                    requeued.append(item.id)
                    continue
                item.status = ItemStatus.FAILED
                item.stage = "已中断：服务重启，执行进程已退出"
                item.error = "服务重启导致执行中断，请重新执行（非代码失败）"
                item.lease_owner = ""
                item.lease_until = ""
                if not item.finished_at:
                    item.finished_at = _utcnow()
                self.store.save(item)
                self.events.publish(
                    Event("item_failed", item.id, {"error": item.error, "recovered": True})
                )
                failed += 1
            delivery = item.delivery if isinstance(item.delivery, dict) else {}
            if delivery.get("ci_pending"):
                # 不丢等待：启动后由 resume_ci_watches() 重新接上。
                ci_pending.append(item.id)
        self.recovered_items = list(requeued)
        return {
            "requeued": requeued,
            "failed": failed,
            "ci_pending": ci_pending,
            "total": len(requeued) + failed + len(ci_pending),
        }

    # ── long-run reliability: leases ──────────────────────────────────────────
    def _dependency_block(self, item: WorkItem) -> str:
        """Why this child cannot start yet ("" = dependencies satisfied)."""
        refs = [ref for ref in getattr(item, "depends_on", []) if str(ref).strip()]
        if not refs:
            return ""
        by_ref = {
            row.source_ref: row
            for row in self.store.list(limit=self.store.count())
            if row.source_ref
        }
        for ref in refs:
            dep = by_ref.get(ref)
            if dep is None:
                return f"依赖的子任务不存在：{ref}"
            if not dep.is_terminal():
                return f"依赖尚未完成：{dep.title}"
            if dep.status != ItemStatus.SUCCEEDED or dep.merge_state == "blocked":
                return f"依赖未成功：{dep.title}"
            if (
                item.exec_mode == "apply"
                and dep.result is not None
                and dep.result.dry_run
            ):
                return f"前置子任务只是 dry-run，未真正交付：{dep.title}"
        return ""

    def _touch_lease(self, item: WorkItem) -> None:
        now = datetime.now(UTC)
        item.lease_owner = self.lease_owner
        item.lease_heartbeat_at = now.isoformat(timespec="seconds")
        item.lease_until = (
            now + timedelta(seconds=max(30.0, float(self.settings.lease_ttl_s)))
        ).isoformat(timespec="seconds")

    async def _lease_heartbeat(self, item_id: str) -> None:
        """Renew a running item's lease until it finishes or disappears.

        Queued items are *not* leased yet (they may wait behind the semaphore
        for a long time). Keep polling rather than exiting: the task may start
        minutes after this coroutine was created, and it must be renewed then.
        """
        interval = max(1.0, float(self.settings.lease_ttl_s) / 3)
        while True:
            await asyncio.sleep(interval)
            item = self.store.get(item_id)
            if item is None:
                return
            if item.status == ItemStatus.RUNNING:
                try:
                    self._touch_lease(item)
                    self.store.save(item)
                except OSError:
                    # 磁盘抖动不应让心跳协程永久退出、把活任务误判成过期。
                    logger.warning("续租失败：%s", item_id, exc_info=True)
            elif item.is_terminal():
                return
            # QUEUED / READY 继续等待：重试路径会先把任务置 READY 再
            # spawn 新 wrapper，若在这里退出，真正 RUNNING 后就没人续租了。

    def sweep_expired_leases(self) -> list[str]:
        """Re-queue auto tasks whose heartbeat stopped (process died mid-run).

        Safe against live runs: the heartbeat renews the lease every TTL/3, so
        only a genuinely stalled task exceeds its deadline.
        """
        policy = self._automation_policy()
        max_recoveries = int(policy.get("max_recoveries", 1))
        now = datetime.now(UTC)
        requeued: list[str] = []
        for item in self.store.list(limit=self.store.count()):
            if item.status != ItemStatus.RUNNING or not item.lease_until:
                continue
            if item.id in self._live_runs:
                # 本进程还有 wrapper 在跑：即使磁盘上的 lease 被某次旧快照
                # 覆盖成过期值，也不能把它从脚下抽走。
                continue
            try:
                deadline = datetime.fromisoformat(item.lease_until)
            except ValueError:
                continue
            if deadline > now:
                continue
            if item.auto and item.recovered < max_recoveries:
                self._cancel_flags[item.id] = True
                self._requeue(item, reason="执行租约过期（进程已退出），已自动重新排队")
                requeued.append(item.id)
            else:
                item.status = ItemStatus.FAILED
                item.stage = "已中断：执行租约过期"
                item.error = "执行租约过期（进程可能已退出），请重新执行"
                item.lease_owner = ""
                item.lease_until = ""
                item.finished_at = _utcnow()
                self.store.save(item)
                self.events.publish(
                    Event("item_failed", item.id, {"error": item.error, "recovered": True})
                )
                if item.auto and self.auto_outcome_hook is not None:
                    try:
                        self.auto_outcome_hook(item)
                    except Exception:
                        logger.warning("租约回收后记账失败：%s", item.id, exc_info=True)
        if requeued:
            logger.warning("租约回收：%d 个自动任务重新排队", len(requeued))
        return requeued

    def resume_ci_watches(self) -> list[str]:
        """Re-attach background CI waits that a restart left pending."""
        resumed: list[str] = []
        for item in self.store.list(limit=self.store.count()):
            delivery = item.delivery if isinstance(item.delivery, dict) else {}
            if not delivery.get("ci_pending"):
                continue
            if item.repo is None:
                delivery["ci_pending"] = False
                delivery["error"] = "CI 等待无法恢复：任务没有本地仓库"
                item.delivery = delivery
                item.stage = "完成（CI 等待无法恢复）"
                self.store.save(item)
                self.events.publish(Event("item_delivery_updated", item.id, {
                    "delivery": dict(delivery), "stage": item.stage,
                }))
                continue
            policy = self._delivery_policy(item)
            if not policy.enabled:
                delivery["ci_pending"] = False
                delivery["error"] = "交付已关闭，CI 等待结束"
                item.delivery = delivery
                item.stage = "完成（交付已关闭，CI 等待结束）"
                self.store.save(item)
                self.events.publish(Event("item_delivery_updated", item.id, {
                    "delivery": dict(delivery), "stage": item.stage,
                }))
                continue
            self._spawn(self._watch_ci(item.id, policy), name=f"ci-{item.id}")
            resumed.append(item.id)
        if resumed:
            logger.info("启动恢复：接上 %d 个待等待的 CI", len(resumed))
        return resumed

    # ── 方案层硬门禁 ───────────────────────────────────────────────────────────
    def _plan_layer_config(self) -> dict[str, Any]:
        raw: dict[str, Any] = {}
        if self.settings.plan_policy is not None:
            try:
                raw = self.settings.plan_policy() or {}
            except Exception as error:  # 配置读取失败必须 fail closed
                raise PlanError(
                    ERR_PLAN_NOT_CONFIRMED, f"读取方案层配置失败，禁止执行：{error}"
                ) from error
        return normalise_plan_layer(raw)

    def _plan_gate(self, item: WorkItem) -> PlanCandidate | None:
        """执行前硬门禁；只读检查，不写存储、不入队、不调用执行器。

        返回 ``None`` 表示方案层关闭或该需求不复杂，继续走原有单方案路径。
        复杂需求没有 ``confirmed`` 方案时抛 ``PLAN_NOT_CONFIRMED``；确认后
        把唯一 confirmed 的 plan_id 与步骤绑到 item，保证只执行那一个方案。
        """
        cfg = self._plan_layer_config()
        store = self.settings.plan_store
        # 「需要确认」在生成方案时固化到 item.plan_required，并以磁盘上是否
        # 已有方案文件兜底。绝不在这里按当前 spec/仓库映射重算复杂度：否则
        # 执行前删掉验收项、补上映射或关掉开关就能绕过门禁。
        has_plans = bool(store.list_by_requirement(item.id)) if store is not None else False
        needs_plan = (
            item.plan_required
            or has_plans
            or (cfg["enabled"] and is_complex(item, cfg))
        )
        if not needs_plan:
            return None
        if store is None:
            # 开启了方案层却没给存储：宁可拒绝，也不能静默放行执行。
            raise PlanError(ERR_PLAN_NOT_CONFIRMED, "方案层已开启但未配置方案存储")
        confirmed = store.confirmed_plan(item.id)
        if confirmed is None:
            raise PlanError(
                ERR_PLAN_NOT_CONFIRMED,
                f"复杂需求 {item.id} 尚无 confirmed 方案，禁止执行",
            )
        if item.selected_plan_id and item.selected_plan_id != confirmed.plan_id:
            raise PlanError(
                ERR_PLAN_MISMATCH,
                f"已锁定方案 {item.selected_plan_id} 与 confirmed 方案 "
                f"{confirmed.plan_id} 不一致",
            )
        item.selected_plan_id = confirmed.plan_id
        item.plan = confirmed.steps_text()
        return confirmed

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
        # 硬门禁必须位于任何状态变更 / 落盘 / 入队 / 工具调用之前。
        self._plan_gate(item)
        if item.is_container:
            raise ValueError("父需求容器只用于分组，不进入执行队列")
        if item.parent_ref:
            # 子任务重跑后，父需求需要重新汇总一次。
            for parent in self.store.list(limit=self.store.count()):
                if parent.source_ref == item.parent_ref and parent.aggregated:
                    parent.aggregated = False
                    self.store.save(parent)
                    break
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
            item.feedback = ""
            item.usage = {}
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
        if executor:
            # 人工选了后端：自动路由不得再改。
            item.executor_locked = True
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
    def run_auto(
        self,
        item_id: str,
        *,
        plan: str = "",
        exec_mode: str = "apply",
        manual: bool = False,
    ) -> WorkItem:
        """Dispatch an item from the automation path (or a human-authorized bulk run).

        ``manual=True`` 表示人工在任务中心显式勾选：仍然走自动链路（规划、
        门禁、原子合并），但 ``approved_by="user"``，不参与仓库熔断记账。

        ``exec_mode`` 必须与触发源的安全语义一致：数据源配置成 ``dry-run``
        时，定时拉取只自动生成提示词与命令，**不改任何仓库**；只有 ``apply``
        才会进隔离 worktree，并在门禁通过后做原子合并。
        """
        item = self.store.get(item_id)
        if item is None:
            raise ValueError(f"unknown item {item_id!r}")
        # 无人值守/人工勾选的自动链路同样受方案层门禁约束（无副作用拒绝）。
        self._plan_gate(item)
        if item.repo is None:
            raise ValueError("no local repo mapped; map the item first")
        if exec_mode not in {"dry-run", "apply"}:
            raise ValueError("exec_mode must be 'dry-run' or 'apply'")
        item.auto = True
        # 人工勾选与无人值守派发都走 auto 链路，但只有后者参与熔断记账。
        item.approved_by = "user" if manual else "auto"
        item.merge_state = ""
        item.exec_mode = exec_mode
        # dry-run 永远不进入合并路径；apply 才需要原子合并。
        item.hold_merge = exec_mode == "apply"
        if plan and not item.selected_plan_id:
            # 方案层已绑定 confirmed 计划时，不允许外部 plan 覆盖它。
            item.plan = plan
        # dry-run 不算“已派发”：它没改代码，评估后切到 apply 还应能补跑一次。
        item.auto_dispatched_at = _utcnow() if exec_mode == "apply" else ""
        self.store.save(item)
        self.events.publish(
            Event("auto_queued", item_id, {
                "repo": item.repo.name,
                "planner": bool(plan),
                "exec_mode": exec_mode,
            })
        )
        if exec_mode == "dry-run":
            return self.run(item_id, exec_mode="dry-run", approved=False, _model_supplied=False)
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
        policy = self._delivery_policy(item)
        ok = self._auto_merge(item, backend, item.result)
        files = len(item.result.synced_files) + len(item.result.sync_deleted)
        if ok:
            # 合并成功后再提交：worktree HEAD 是三方合并的基线。
            self._prepare_delivery(item, backend, item.result, policy)
            if verify_failed:
                item.status = ItemStatus.SUCCEEDED
                item.error = None
            base_stage = (
                f"完成（人工强制合并 {files} 个文件，已跳过验证）"
                if verify_failed else f"完成（已合并 {files} 个文件到工作区）"
            )
            item.stage = base_stage + self._finish_delivery(item, policy)
            # Changes are in the main repo — the worktree has served its purpose.
            self._gc_after_success(item, backend)
        else:
            self._prepare_delivery(item, backend, item.result, policy)
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
        policy = self._delivery_policy(item)
        if merge and item.hold_merge and backend is not None:
            merged = self._auto_merge(item, backend, item.result)
            if merged:
                # 先合并再提交：worktree 的 HEAD 是同步基线，提交会移动它。
                self._prepare_delivery(item, backend, item.result, policy)
                files = len(item.result.synced_files) + len(item.result.sync_deleted)
                suffix = await asyncio.to_thread(self._finish_delivery, item, policy)
                item.stage = f"完成（验证通过，已合并 {files} 个文件到工作区）{suffix}"
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
        keep_branch = bool((item.delivery or {}).get("commit"))
        try:
            cleanup(
                worktree,
                str(item.repo.path) if item.repo else None,
                keep_branch=keep_branch,
            )
        except TypeError:
            # 老的自定义执行器没有 keep_branch 参数：退回旧签名。
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

    # ── task spec ──────────────────────────────────────────────────────────────
    def _spec_llm(self) -> Any | None:
        """Backend-agent LLM used for TaskSpec generation (may be unconfigured)."""
        provider = self.settings.spec_provider or self.settings.review_provider
        if provider is None:
            return None
        try:
            return provider()
        except Exception as error:  # noqa: BLE001 - spec generation is best effort
            logger.warning("读取规格模型失败：%s", error)
            return None

    # ── token usage / budget ───────────────────────────────────────────────────
    def record_usage(self, item: WorkItem, kind: str, usage: TokenUsage | dict) -> int:
        """Accumulate usage onto the item and into the daily ledger.

        ``kind`` is ``cli`` (execution backend) or ``llm`` (spec/review/planner).
        Returns today's cumulative total.
        """
        parsed = usage if isinstance(usage, TokenUsage) else TokenUsage.from_dict(usage)
        if not parsed:
            return self.ledger.total_today
        book = dict(item.usage or {})
        bucket = dict(book.get(kind) or {})
        bucket["input_tokens"] = int(bucket.get("input_tokens") or 0) + parsed.input_tokens
        bucket["output_tokens"] = int(bucket.get("output_tokens") or 0) + parsed.output_tokens
        bucket["calls"] = int(bucket.get("calls") or 0) + parsed.calls
        bucket["total_tokens"] = int(bucket["input_tokens"]) + int(bucket["output_tokens"])
        book[kind] = bucket
        book["total_tokens"] = sum(
            int((value or {}).get("total_tokens") or 0)
            for key, value in book.items()
            if key in {"cli", "llm"} and isinstance(value, dict)
        )
        book["calls"] = sum(
            int((value or {}).get("calls") or 0)
            for key, value in book.items()
            if key in {"cli", "llm"} and isinstance(value, dict)
        )
        item.usage = book
        return self.ledger.record(item.id, parsed)

    def _budget_reason(self, item: WorkItem) -> str:
        """Why this task must not start another round/run (empty = within budget)."""
        used = int((item.usage or {}).get("total_tokens") or 0)
        task_limit = max(0, int(self.settings.task_token_budget or 0))
        if task_limit and used >= task_limit:
            return f"任务 token 预算已用完（{used}/{task_limit}）"
        daily_limit = max(0, int(self.settings.daily_token_budget or 0))
        today = self.ledger.total_today
        if daily_limit and today >= daily_limit:
            return f"今日 token 预算已用完（{today}/{daily_limit}）"
        return ""

    # ── delivery（过滤提交 / push / PR） ───────────────────────────────────────
    def _mapping_for(self, item: WorkItem):
        if self.projects is None or item.repo is None:
            return None
        try:
            repo_path = str(Path(item.repo.path).expanduser().resolve())
        except OSError:
            repo_path = item.repo.path
        for mapping in self.projects.list():
            if mapping.name == item.repo.name:
                return mapping
            try:
                if str(Path(mapping.path).expanduser().resolve()) == repo_path:
                    return mapping
            except OSError:
                continue
        return None

    def _delivery_policy(self, item: WorkItem):
        from .delivery import policy_for

        globals_cfg: dict[str, Any] = {}
        if self.settings.delivery_globals is not None:
            try:
                globals_cfg = self.settings.delivery_globals() or {}
            except Exception:
                logger.warning("读取交付全局配置失败", exc_info=True)
        return policy_for(
            item,
            settings=self.settings,
            mapping=self._mapping_for(item),
            globals_cfg=globals_cfg,
        )

    def _deliver_commit(self, item: WorkItem, backend, result, policy) -> dict[str, Any]:
        """Commit the verified worktree changes onto the task branch."""
        from .delivery import commit_changes

        outcome = commit_changes(
            item,
            backend=backend,
            worktree=result.worktree or "",
            filter_obj=self._commit_filter(),
        )
        record: dict[str, Any] = {
            "mode": policy.mode,
            "branch": str(outcome.get("branch") or ""),
            "commit": str(outcome.get("commit") or ""),
            "kept": list(outcome.get("kept") or []),
            "skipped": list(outcome.get("skipped") or []),
            "pushed": False,
            "pr_url": "",
            "error": str(outcome.get("error") or ""),
        }
        if record["commit"]:
            item.commit = CommitResult(
                ok=True,
                commit=record["commit"],
                branch=record["branch"],
                message=f"taskagent: {item.title}",
                kept=record["kept"],
                skipped=record["skipped"],
            )
        return record

    def _deliver_remote(self, item: WorkItem, policy) -> dict[str, Any]:
        """Push the committed branch and optionally open a PR/MR."""
        from .delivery import create_pr, push_branch, remote_url

        record = dict(item.delivery or {})
        record["mode"] = policy.mode
        if not record.get("commit") or item.repo is None:
            record["error"] = record.get("error") or "没有可推送的提交"
            item.delivery = record
            return record
        if policy.mode not in {"push", "pr"}:
            item.delivery = record
            return record
        push = push_branch(item.repo.path, record["branch"], policy.remote)
        record["pushed"] = bool(push.get("ok"))
        record["remote"] = policy.remote
        if not push.get("ok"):
            record["error"] = str(push.get("error") or "push 失败")
            item.delivery = record
            return record
        if policy.mode == "pr":
            body = (
                f"由 TaskAgent 自动生成。\n\n- 任务：{item.title}\n"
                f"- 任务 ID：{item.id}\n"
                f"- 改动文件：{len((item.result.changed_files if item.result else []) or [])} 个\n"
            )
            pr = create_pr(
                policy=policy,
                remote_url=remote_url(item.repo.path, policy.remote),
                branch=record["branch"],
                title=f"{item.title} ({item.id})",
                body=body,
            )
            record["pr_url"] = str(pr.get("url") or "")
            record["pr_number"] = int(pr.get("number") or 0)
            record["provider"] = str(pr.get("provider") or "")
            if not pr.get("ok"):
                record["error"] = str(pr.get("error") or "创建 PR/MR 失败")
        item.delivery = record
        return record

    def _prepare_delivery(self, item: WorkItem, backend, result, policy) -> None:
        """Commit verified changes before merge (worktree is gone afterwards)."""
        if not policy.enabled:
            return
        if (item.delivery or {}).get("commit"):
            return
        if result is None or not result.worktree:
            item.delivery = {"mode": policy.mode, "error": "没有可提交的 worktree"}
            return
        item.delivery = self._deliver_commit(item, backend, result, policy)

    def _finish_delivery(self, item: WorkItem, policy, *, prefix: str = "，") -> str:
        """Push the committed branch and create a PR/MR (fast, no CI wait).

        CI waiting is a separate background task (:meth:`_watch_ci`) so a slow
        pipeline never holds the scheduler slot.
        """
        if not policy.enabled or not (item.delivery or {}).get("commit"):
            return ""
        self._deliver_remote(item, policy)
        record = item.delivery or {}
        if record.get("pr_url"):
            return f"{prefix}PR/MR：{record['pr_url']}"
        if record.get("pushed"):
            return f"{prefix}已推送任务分支 {record.get('branch')}"
        if record.get("error"):
            return f"{prefix}交付失败：{str(record['error'])[:120]}"
        if record.get("commit"):
            return f"{prefix}已提交 {record['commit']}（任务分支）"
        return ""

    async def _watch_ci(self, item_id: str, policy) -> None:
        """Background CI watch + optional auto-merge; never holds a scheduler slot."""
        from .delivery import ci_status, merge_pr, remote_url

        item = self.store.get(item_id)
        if item is None or item.repo is None:
            return
        record = dict(item.delivery or {})
        ref = str(record.get("branch") or "")
        number = int(record.get("pr_number") or 0)
        url = remote_url(item.repo.path, policy.remote)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(1, policy.ci_timeout_s)
        interval = max(1, policy.ci_poll_interval_s)
        ci: dict[str, Any] = {"state": "pending", "detail": "等待 CI"}
        while True:
            ci = await asyncio.to_thread(ci_status, policy, url, ref)
            current = self.store.get(item_id)
            if current is None:
                return
            if current.status in (ItemStatus.CANCELLED, ItemStatus.SKIPPED):
                return
            current.delivery["ci_state"] = str(ci.get("state") or "")
            current.delivery["ci_detail"] = str(ci.get("detail") or "")
            if ci.get("state") in {"success", "failure", "none", "error", "timeout"}:
                break
            remaining = deadline - loop.time()
            if remaining <= 0:
                ci = {"state": "timeout", "detail": "等待 CI 超时"}
                break
            current.stage = f"等待 CI…（{ci.get('state')}）"
            self.store.save(current)
            self.events.publish(Event("item_progress", item_id, {
                "progress": 0.99, "stage": current.stage,
            }))
            await asyncio.sleep(min(interval, remaining))

        item = self.store.get(item_id)
        if item is None:
            return
        item.delivery["ci_state"] = str(ci.get("state") or "")
        item.delivery["ci_detail"] = str(ci.get("detail") or "")
        merged = False
        if ci.get("state") == "success" and policy.auto_merge and number:
            outcome = await asyncio.to_thread(merge_pr, policy, url, number)
            merged = bool(outcome.get("ok"))
            item.delivery["merged"] = merged
            if not merged:
                item.delivery["merge_error"] = str(outcome.get("error") or "")
        item.delivery["ci_pending"] = False
        if merged:
            item.stage = "完成（CI 通过，已自动合并 PR）"
        elif ci.get("state") == "success" and policy.auto_merge:
            item.stage = "完成（CI 通过，但自动合并失败，请人工处理 PR）"
        elif ci.get("state") == "success":
            item.stage = "完成（PR 已创建，CI 通过，待人工合并）"
        elif ci.get("state") == "timeout":
            item.stage = "完成（等待 CI 超时，PR 未合并）"
        elif ci.get("state") == "none":
            item.stage = "完成（未发现 CI，PR 未自动合并）"
        elif ci.get("state") == "error":
            item.stage = "完成（CI 查询失败，PR 未自动合并）"
        else:
            item.stage = f"完成（CI {ci.get('state')}，PR 未合并）"
        item.report_path = write_report(
            item, self.settings.reports_dir, parent_title_of(item, self.store)
        )
        self.store.save(item)
        self.events.publish(Event("item_delivery_updated", item_id, {
            "delivery": dict(item.delivery), "stage": item.stage,
        }))

    def deliver_item(self, item_id: str) -> WorkItem:
        """Retry delivery (push/PR) for a task whose local commit already exists."""
        item = self.store.get(item_id)
        if item is None:
            raise ValueError(f"unknown item {item_id!r}")
        if item.result is None or item.result.dry_run:
            raise ValueError("任务还没有可交付的 apply 结果")
        policy = self._delivery_policy(item)
        if not policy.enabled:
            raise ValueError("该仓库未开启交付（delivery=off）")
        if not (item.delivery or {}).get("commit"):
            worktree = item.result.worktree
            if not worktree or not Path(worktree).expanduser().is_dir():
                raise ValueError("worktree 已回收，找不到可提交的改动")
            backend = self.executors.resolve(item.executor)
            if backend is None:
                raise ValueError("没有可用的执行后端")
            item.delivery = self._deliver_commit(item, backend, item.result, policy)
        self._finish_delivery(item, policy)
        self.store.save(item)
        self.events.publish(Event("item_delivered", item.id, {"delivery": dict(item.delivery)}))
        return item

    async def _ensure_spec(self, item: WorkItem) -> None:
        """Make sure the item has a TaskSpec; auto tasks get an LLM upgrade.

        Rule specs are generated at import time and are always available. Auto
        tasks try the backend-agent model once per execution; any failure keeps
        the rule spec so planning can never block the run.
        """
        from .spec import plan_spec, rule_spec, spec_from_dict

        existing = spec_from_dict(item.spec)
        if item.auto and (existing is None or existing.source != "llm"):
            llm = self._spec_llm()
            if llm is not None:
                generated = await plan_spec(
                    item, llm=llm, timeout_s=self.settings.spec_timeout_s
                )
                self.record_usage(item, "llm", getattr(llm, "last_usage", None) or {})
                if generated is not None:
                    item.spec = generated.to_dict()
                    return
        if existing is None:
            item.spec = rule_spec(item).to_dict()

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
            review = await review_diff(
                item,
                list(result.changed_files),
                result.worktree,
                llm=llm,
                timeout_s=self.settings.review_timeout_s,
                max_diff_lines=self.settings.review_max_diff_lines,
            )
        except Exception as error:  # noqa: BLE001 - best effort
            logger.warning("LLM 代码评审异常 %s：%s", item.id, error)
            review = None
        self.record_usage(item, "llm", getattr(llm, "last_usage", None) or {})
        return review

    async def _verify(self, item: WorkItem, result) -> dict[str, Any]:
        """Run the repository's checks inside the task worktree.

        Synchronous subprocess work is pushed to a thread so a slow test suite
        cannot freeze the event loop (SSE / UI / other tasks).
        """
        from .reviewer import apply_review, review_enabled
        from .verification import gc_verification_logs, load_config, repo_config, run_gate

        config_file = self.settings.verify_config_file or (
            self.settings.reports_dir.parent / "verify.json"
        )
        repo = item.repo
        repo_name = repo.name if repo else ""
        repo_path = repo.path if repo else ""
        configured = repo_config(load_config(config_file), repo_name, repo_path).get("assertions")
        assertions = dict(self.settings.assertions_config or {})
        assertions_enabled = (
            self.settings.assertions_enabled
            or bool(isinstance(configured, dict) and configured.get("enabled"))
            or bool(assertions.get("enabled"))
        )
        if assertions_enabled:
            assertions["enabled"] = True
        else:
            # 未显式开启时不传 spec/配置，避免仅设 timeout_s 就意外改变默认门禁。
            assertions = {}
        try:
            outcome = await asyncio.to_thread(
                run_gate,
                repo_name=repo_name,
                repo_path=repo_path,
                worktree=result.worktree,
                changed=list(result.changed_files),
                item_id=item.id,
                config_path=config_file,
                data_dir=self.settings.reports_dir.parent,
                default_timeout_s=self.settings.verify_default_timeout_s,
                total_timeout_s=self.settings.verify_total_timeout_s,
                spec=item.spec if assertions_enabled else None,
                assertions=assertions or None,
            )
            # 断言门禁开启时，verdict 只由用例结果决定；LLM 评审降级为参考信息。
            assertion_mode = bool(
                (outcome.get("assertions") or {}).get("enabled")
                if isinstance(outcome.get("assertions"), dict) else False
            )
            if review_enabled():
                review = await self._review(item, result)
                outcome = apply_review(outcome, review, deciding=not assertion_mode)
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
    async def _execute(self, item_id: str, *, retry_feedback: str = "") -> None:
        """Run one work item under a lease, then report the outcome.

        The wrapper owns two cross-cutting concerns so the (long) execution
        body stays untouched: lease heartbeat/release and the automatic-run
        outcome hook used by the circuit breaker.

        ``retry_feedback`` carries the previous CLI failure into a scheduling
        retry (see ``_execute_inner``): normal starts leave it empty.
        """
        heartbeat = asyncio.create_task(
            self._lease_heartbeat(item_id), name=f"lease-{item_id}"
        )
        self._live_runs.add(item_id)
        try:
            await self._execute_inner(item_id, retry_feedback=retry_feedback)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # 未捕获异常不再让任务永远停在 RUNNING：立即落 failed（自动任务
            # 会触发熔断计数），而不是等 3 分钟租约到期。
            logger.exception("执行任务 %s 时发生未捕获异常", item_id)
            self._mark_crashed(item_id, exc)
        finally:
            self._live_runs.discard(item_id)
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            except Exception:
                # 心跳自身的意外错误不能阻断收尾（释放租约 / 熔断记账）。
                logger.warning("租约心跳异常：%s", item_id, exc_info=True)
            self._release_lease(item_id)
            self._record_attempt_trace(item_id)
            self._notify_auto_outcome(item_id)
            self._record_memory(item_id)
            await self._finalize_parent(item_id)

    def _record_attempt_trace(self, item_id: str) -> None:
        """Append one structured record for the attempt that just finished.

        Append-only across re-arms so the board/report can answer "which round
        failed and why". Bounded to the most recent 12 records to avoid an
        unbounded JSON file on long-lived items.
        """
        from .failure import FailureKind, classify_exec_error

        item = self.store.get(item_id)
        if item is None or item.attempt <= 0:
            return
        result = item.result
        status = getattr(item.status, "value", str(item.status))

        # outcome: terminal state, or a retry scheduled for the next attempt.
        if status == "succeeded":
            outcome = "succeeded"
        elif status == "cancelled":
            outcome = "cancelled"
        elif status == "ready":
            outcome = "retry_scheduled"  # 调度级重试：本轮失败，已排下一轮
        else:
            outcome = "failed"

        error = str(
            item.error
            or (getattr(result, "error", None) if result is not None else None)
            or ""
        ).strip()
        gate = getattr(result, "verification", None) if result is not None else None
        gate = gate if isinstance(gate, dict) else {}
        kind = FailureKind.REPO.value
        if error:
            kind = classify_exec_error(error).value
        elif gate and not gate.get("ok", True):
            kind = FailureKind.REPO.value
        if (getattr(result, "failure_scope", "") or "") in {"environment", "budget"}:
            kind = str(result.failure_scope)

        record = {
            "seq": len(item.attempts) + 1,
            "attempt": item.attempt,
            "at": item.finished_at or item.started_at or _utcnow(),
            "outcome": outcome,
            "error": error[:300],
            "kind": kind,
            "rounds": int(getattr(result, "rounds", 0) or 0) if result else 0,
            "gate_state": str(gate.get("assertion_state") or gate.get("state") or ""),
            "feedback": (item.feedback or "")[:500],
        }
        item.attempts.append(record)
        del item.attempts[:-12]  # 只保留最近 12 条
        self.store.save(item)

    def _mark_crashed(self, item_id: str, exc: BaseException) -> None:
        item = self.store.get(item_id)
        if item is None or item.status not in (ItemStatus.QUEUED, ItemStatus.RUNNING):
            return
        item.status = ItemStatus.FAILED
        item.stage = "执行异常中断"
        item.error = f"执行器异常：{exc}"
        item.finished_at = _utcnow()
        item.lease_owner = ""
        item.lease_until = ""
        self.store.save(item)
        self.events.publish(Event("item_failed", item_id, {"error": item.error}))

    def _release_lease(self, item_id: str) -> None:
        item = self.store.get(item_id)
        if item is None or item.status in (ItemStatus.QUEUED, ItemStatus.RUNNING):
            return
        if not (item.lease_owner or item.lease_until):
            return
        item.lease_owner = ""
        item.lease_until = ""
        item.lease_heartbeat_at = ""
        self.store.save(item)

    def _record_memory(self, item_id: str) -> None:
        if self.on_item_done is None:
            return
        item = self.store.get(item_id)
        if item is None or item.status not in (ItemStatus.SUCCEEDED, ItemStatus.FAILED):
            return
        try:
            self.on_item_done(item)
        except Exception:
            logger.warning("跨任务记忆记账失败：%s", item_id, exc_info=True)

    async def _finalize_parent(self, item_id: str) -> None:
        """Aggregate a decomposed parent once all its children are terminal."""
        item = self.store.get(item_id)
        if item is None or not item.parent_ref or not item.is_terminal():
            return
        items = self.store.list(limit=self.store.count())
        parent = next((row for row in items if row.source_ref == item.parent_ref), None)
        if parent is None or parent.aggregated or not parent.children_ids:
            return
        children = [
            row for row in items
            if row.parent_ref == parent.source_ref and row.source_ref != parent.source_ref
        ]
        if not children or any(not child.is_terminal() for child in children):
            return
        if not any(
            child.result is not None and not child.result.dry_run for child in children
        ):
            # 全部是 dry-run：没有真正交付，不冒充“拆解完成”，也不回填状态。
            parent.aggregated = True
            parent.stage = "拆解完成（全部 dry-run，未写入主仓）"
            self.store.save(parent)
            self.events.publish(Event("parent_aggregated", parent.id, {
                "ok": None, "dry_run": True, "children": len(children),
            }))
            return
        succeeded = sum(
            1 for child in children
            if child.status == ItemStatus.SUCCEEDED
            and child.merge_state != "blocked"
            and child.result is not None
            and not child.result.dry_run
        )
        ok = succeeded == len(children)
        parent.aggregated = True
        parent.stage = (
            f"拆解完成：{succeeded}/{len(children)} 子任务成功"
            + ("" if ok else "（有失败）")
        )
        self.store.save(parent)
        self.events.publish(Event("parent_aggregated", parent.id, {
            "ok": ok, "children": len(children), "succeeded": succeeded,
        }))
        if self.on_children_done is None:
            return
        try:
            # 回填是同步网络调用（TAPD），不能占住调度事件循环。
            await asyncio.to_thread(self.on_children_done, parent, children, ok)
        except Exception:  # aggregation is best effort
            logger.warning("父需求 %s 汇总回填失败", parent.id, exc_info=True)

    def _notify_auto_outcome(self, item_id: str) -> None:
        if self.auto_outcome_hook is None:
            return
        item = self.store.get(item_id)
        if item is None or not item.auto:
            return
        if item.status not in (ItemStatus.SUCCEEDED, ItemStatus.FAILED):
            return
        try:
            self.auto_outcome_hook(item)
        except Exception:
            logger.warning("自动执行结果记账失败：%s", item_id, exc_info=True)

    async def _execute_inner(self, item_id: str, *, retry_feedback: str = "") -> None:
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
            blocked = self._dependency_block(item)
            if blocked:
                # 用 failed（可重跑）而不是 skipped（终态不可重跑）：前置任务
                # 修好后，人工可以重新执行这一条。
                item.status = ItemStatus.FAILED
                item.stage = blocked
                item.error = blocked
                item.finished_at = _utcnow()
                self.store.save(item)
                self.events.publish(Event("item_failed", item_id, {"error": blocked}))
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
            # 调度级重试（CLI 自身失败）把上一轮的失败原因放进 feedback 注入提示词；
            # 正常执行 / 人工重新执行仍清空，保持原有「不带历史反馈」语义。
            item.feedback = (retry_feedback or "").strip()[:4000]
            item.memory = {}
            self._touch_lease(item)
            self.store.save(item)

            # 日预算在进入任何模型调用之前检查，避免"先花钱再报超支"。
            budget_early = self._budget_reason(item)
            if budget_early:
                item.status = ItemStatus.FAILED
                item.stage = budget_early
                item.error = budget_early
                item.finished_at = _utcnow()
                item.report_path = write_report(
                    item, self.settings.reports_dir, parent_title_of(item, self.store)
                )
                self.store.save(item)
                self.events.publish(Event("item_failed", item_id, {"error": item.error}))
                return

            # 任务规格：规则版兜底；自动任务升级为 LLM 版（失败保留规则版）。
            if item.auto and (item.spec or {}).get("source") != "llm":
                item.stage = "生成任务规格…"
                self.store.save(item)
                self.events.publish(Event("item_progress", item_id, {
                    "progress": 0.05, "stage": item.stage,
                }))
            await self._ensure_spec(item)
            # 规格调用本身也占预算：生成完再检查一次，超了就不启动 agent。
            budget_early = self._budget_reason(item)
            if budget_early:
                item.status = ItemStatus.FAILED
                item.stage = budget_early
                item.error = budget_early
                item.finished_at = _utcnow()
                item.report_path = write_report(
                    item, self.settings.reports_dir, parent_title_of(item, self.store)
                )
                self.store.save(item)
                self.events.publish(Event("item_failed", item_id, {"error": item.error}))
                return
            if self.settings.memory_provider is not None:
                try:
                    recalled = self.settings.memory_provider(item) or {}
                except Exception:
                    logger.warning("跨任务记忆召回失败：%s", item_id, exc_info=True)
                    recalled = {}
                if recalled:
                    item.memory = recalled
                    self.store.save(item)
            item.stage = f"开始执行（{backend.label}）"
            self.store.save(item)
            self.events.publish(Event("item_started", item_id, {
                "attempt": item.attempt, "executor": backend.backend_id,
            }))

            def set_stage(text: str) -> None:
                """Persist a round-stage update without clobbering cancel state."""
                current = self.store.get(item_id)
                if current is None or current.status != ItemStatus.RUNNING:
                    return
                current.stage = text
                current.feedback = item.feedback
                current.spec = dict(item.spec)
                self.store.save(current)
                self.events.publish(Event("item_progress", item_id, {
                    "progress": 0.5, "stage": text,
                }))

            budget_stop = ""
            if item.hold_merge and item.exec_mode == "apply":
                # 执行-反馈循环：门禁/评审未通过时带着具体问题再修几轮。
                # 人工 apply 固定 1 轮，保持「点一次跑一次」的直觉。
                from .loop import run_rounds

                outcome = await run_rounds(
                    item=item,
                    backend=backend,
                    gate=lambda target, res: self._verify(target, res),
                    max_rounds=self.settings.max_rounds if item.auto else 1,
                    progress=progress,
                    should_cancel=should_cancel,
                    stage=set_stage,
                    record_usage=lambda kind, usage: self.record_usage(item, kind, usage),
                    budget_reason=lambda: self._budget_reason(item),
                )
                result = outcome.result
                budget_stop = outcome.budget_reason
                if budget_stop:
                    # 预算是全局配置问题，不该把仓库熔断打开。
                    result.failure_scope = "budget"
            else:
                result = await backend.run(item, progress=progress, should_cancel=should_cancel)
            result.rounds = result.rounds or 1

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
                    gate = result.verification if isinstance(result.verification, dict) else {}
                    if not gate:
                        gate = await self._verify(item, result)
                    result.verification = gate
                    if not gate.get("ok"):
                        verify_failure = str(gate.get("reason") or "未知原因")
                        # Surface a blocking review too: otherwise a single
                        # "ruff failed" line hides the fact that the diff was
                        # also rejected for missing core functions.
                        review_note = ""
                        review = gate.get("review") if isinstance(gate.get("review"), dict) else {}
                        if review.get("state") == "done" and review.get("verdict") == "block":
                            review_reason = str(review.get("reason") or "").strip()
                            review_note = "；评审 block" + (f"：{review_reason[:80]}" if review_reason else "")
                        round_note = f"（第 {result.rounds} 轮）" if result.rounds > 1 else ""
                        item.stage = f"验证未通过{round_note}：{verify_failure}{review_note}"
                    else:
                        # 门禁通过：先原子合并，再在 worktree 的任务分支上提交交付物。
                        # 顺序不能反：提交会移动 worktree HEAD（三方合并的基线）。
                        policy = self._delivery_policy(item)
                        merged = self._auto_merge(item, backend, result)
                        if merged:
                            files = len(result.synced_files) + len(result.sync_deleted)
                            note = ""
                            if gate.get("state") == "disabled":
                                note = "，验证已禁用"
                            elif gate.get("state") == "passed_with_warnings":
                                note = "，验证有警告"
                            await asyncio.to_thread(
                                self._prepare_delivery, item, backend, result, policy
                            )
                            suffix = await asyncio.to_thread(
                                self._finish_delivery, item, policy
                            )
                            item.stage = (
                                f"完成（已自动合并 {files} 个文件到工作区{note}）{suffix}"
                            )
                            # Merge landed: reclaim the isolated worktree now.
                            self._gc_after_success(item, backend)
                            if item.auto and policy.mode == "pr" and (item.delivery or {}).get("pr_url"):
                                item.delivery["ci_pending"] = True
                                item.stage = "PR 已创建，等待 CI…（后台）"
                        else:
                            conflicts = len(result.sync_conflicts)
                            await asyncio.to_thread(
                                self._prepare_delivery, item, backend, result, policy
                            )
                            blocked_stage = (
                                f"完成（合并冲突 {conflicts} 个文件，未写入主仓）"
                                if conflicts else "完成（合并失败，未写入主仓）"
                            )
                            commit_note = ""
                            if (item.delivery or {}).get("commit"):
                                commit_note = f"；已在任务分支提交 {item.delivery['commit']}（未推送）"
                            item.stage = blocked_stage + commit_note
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
                    # 验证没过 = 这单没有交付。自动任务在次数/预算允许、且门禁状态
                    # 确实可修时，带着结构化诊断再跑一轮；否则置 failed 留在任务
                    # 中心供人工处理，而不是归档进历史。
                    from .failure import gate_repairable

                    gate = gate if isinstance(gate, dict) else {}
                    can_verify_retry = (
                        item.auto
                        and not budget_stop
                        and item.attempt < item.max_attempts
                        and gate_repairable(gate)
                    )
                    if can_verify_retry:
                        delay = self.settings.retry_delays[
                            min(item.attempt - 1, len(self.settings.retry_delays) - 1)
                        ]
                        retry_feedback = _build_verify_retry_feedback(
                            gate, attempt=item.attempt, max_attempts=item.max_attempts
                        )
                        item.status = ItemStatus.READY
                        item.stage = (
                            f"验证未通过，{delay:g}s 后重试"
                            f"（{item.attempt}/{item.max_attempts}）：{verify_failure}"
                        )
                        item.error = f"验证未通过：{verify_failure}"
                        item.feedback = retry_feedback
                        self.store.save(item)
                        self.events.publish(Event("item_retry", item_id, {
                            "delay": delay, "reason": "verification",
                        }))
                        await asyncio.sleep(delay)
                        self._spawn(
                            self._execute(item_id, retry_feedback=retry_feedback),
                            name=f"work-{item_id}-verify-retry",
                        )
                        return
                    item.status = ItemStatus.FAILED
                    if budget_stop:
                        item.stage = f"{budget_stop}，已停止重试"
                        item.error = f"{budget_stop}（最后一轮未通过：{verify_failure}）"
                    else:
                        item.error = f"验证未通过：{verify_failure}"
                else:
                    item.status = ItemStatus.SUCCEEDED
                    item.error = None
            else:
                if item.attempt < item.max_attempts and _is_retryable(result.error):
                    delay = self.settings.retry_delays[
                        min(item.attempt - 1, len(self.settings.retry_delays) - 1)
                    ]
                    retry_feedback = _build_retry_feedback(
                        result.error, attempt=item.attempt, max_attempts=item.max_attempts
                    )
                    item.status = ItemStatus.READY
                    item.stage = f"重试前等待 {delay:g}s"
                    item.error = result.error
                    item.feedback = retry_feedback
                    self.store.save(item)
                    self.events.publish(Event("item_retry", item_id, {"delay": delay}))
                    await asyncio.sleep(delay)
                    self._spawn(
                        self._execute(item_id, retry_feedback=retry_feedback),
                        name=f"work-{item_id}-retry",
                    )
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
            # 最后再起后台 CI 等待：避免 _execute 末尾的 save 覆盖后台写入的 ci_state。
            if item.delivery.get("ci_pending"):
                self._spawn(
                    self._watch_ci(item_id, self._delivery_policy(item)),
                    name=f"ci-{item_id}",
                )


def _is_retryable(error: str | None) -> bool:
    """兼容入口；规则已统一到 failure.is_retryable（判定等价）。"""
    from .failure import is_retryable

    return is_retryable(error)


def _build_retry_feedback(
    error: str | None, *, attempt: int, max_attempts: int
) -> str:
    """把 CLI 执行失败转成下一轮可操作的反馈，注入提示词。

    与 ``loop.build_feedback``（门禁/评审失败）互补：这里处理的是 agent 进程
    根本没跑完的情况——超时、限流、服务暂时不可用。这类错误不是代码写错，
    而是执行环境抖动，所以反馈重点不是「改哪行」，而是告诉 agent 上次做到
    哪一步、本轮从哪里续上，避免重复劳动或推倒重来。
    """
    lowered = (error or "").lower()
    if "timeout" in lowered or "timed out" in lowered:
        cause = (
            "上一轮 CLI 执行超时：任务可能在跑测试/构建时耗时过长。"
            "本轮优先缩小改动范围、聚焦最小修改，避免触发长时间的全量检查。"
        )
    elif any(word in lowered for word in ("rate", "429")):
        cause = (
            "上一轮 CLI 遇到调用限流（rate limit）：本轮减少不必要的重复探测，"
            "直接针对需求改动，避免大量试探性命令。"
        )
    else:  # 502 / 503 等服务暂时不可用
        cause = (
            "上一轮 CLI 因服务端暂时不可用而中断（502/503）：这不是代码问题。"
            "本轮继续完成原任务即可；如 worktree 已有部分改动，先检查再续做，不要推倒重来。"
        )
    parts = [
        f"这是第 {attempt}/{max_attempts} 次执行，上一轮 agent 进程未能正常完成（执行失败，非验证不通过）。",
        cause,
    ]
    if error:
        parts.append(f"原始错误：{error.strip()[:500]}")
    parts.append("请继续完成原任务；worktree 中的历史改动会保留，先确认现状再动手。")
    return "\n".join(parts)


def _build_verify_retry_feedback(
    gate: dict, *, attempt: int, max_attempts: int
) -> str:
    """把验证/评审未通过的门禁结论转成下一轮可操作的反馈。

    复用 ``loop.build_feedback`` 的结构化诊断（review issues / check diagnostics），
    而不是把门禁原始输出整段倒给模型。外层加一句"在现有改动上续做"，让调度级
    重试与 L2 轮次共享同一 worktree 的语义保持一致。
    """
    from .loop import build_feedback

    detail = build_feedback(gate, round_no=attempt, max_rounds=max_attempts)
    return (
        "上一轮代码已产出，但验证/评审未通过；工作区保留了上一轮的改动，"
        "请在现有基础上修复，不要推倒重来。\n" + detail
    )


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
