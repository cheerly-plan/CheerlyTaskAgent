"""自动执行引擎：由**触发点**决定跑哪些任务，而不是靠全局扫描 + 一堆开关。

两个触发点：

1. **TAPD 定时拉取**：`tapd_poll_loop` 拉到新工单后，把本次新建的任务交给
   :meth:`AutomationEngine.dispatch`，自动跑完进「我的待办」。
2. **任务中心手动触发**：用户勾选若干任务点「自动执行」，只跑选中的这些。

链路：规划（仅多候选仓库时）→ 隔离 worktree 内 apply → 成功后生成待办（不合并、
不提交）→ 一轮全部结束后收口成批次。人工在待办页点「合并」才写回主仓库工作区。

幂等：`auto_dispatched_at` 一旦写入就不再自动派发；失败重试仍由 scheduler 内部处理。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from .events import Event
from .models import ItemStatus, WorkItem
from .planner import Plan, plan_repo
from .todos import BatchSummary, TodoStore

logger = logging.getLogger(__name__)

# 规划只在「TAPD 项目绑定了多个仓库」时发生，参数固定，不再暴露成一堆设置项。
PLANNER_TIMEOUT_S = 120
PLANNER_MAX_CANDIDATES = 4


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class AutomationEngine:
    def __init__(self, *, scheduler, store, todos: TodoStore, events) -> None:
        self.scheduler = scheduler
        self.store = store
        self.todos = todos
        self.events = events
        # dispatch 期间不回收批次：避免「第一条跑完时后面的任务还没入批」被提前收口。
        self._dispatching = 0

    # ── 触发入口 ──────────────────────────────────────────────────────────────
    async def dispatch(self, item_ids: list[str], *, trigger: str = "manual") -> dict[str, Any]:
        """自动执行指定的任务；返回实际启动与跳过的清单。

        只处理两类：已映射仓库的 READY，以及多候选仓库的 DISCOVERED（先规划）。
        其余（未映射、父容器、已自动派发过）如实跳过，不猜。
        """
        started: list[str] = []
        skipped: list[dict[str, str]] = []
        batch_id = ""
        self._dispatching += 1
        try:
            seen: set[str] = set()
            for item_id in item_ids:
                if item_id in seen:
                    continue
                seen.add(item_id)
                item = self.store.get(item_id)
                if item is None:
                    skipped.append({"id": item_id, "reason": "任务不存在"})
                    continue
                reason = self._skip_reason(item)
                if reason:
                    skipped.append({"id": item_id, "reason": reason})
                    continue
                plan_text = ""
                if item.status == ItemStatus.DISCOVERED:
                    candidates = self._candidates(item)
                    if not candidates:
                        skipped.append({"id": item_id, "reason": "未映射仓库，且没有可用的候选仓库"})
                        continue
                    plan = await self._plan(item, candidates)
                    if plan is None:
                        skipped.append({"id": item_id, "reason": "自动规划未能确定仓库，请手动映射"})
                        continue
                    try:
                        self._apply_plan(item, plan)
                    except ValueError as error:
                        skipped.append({"id": item_id, "reason": f"规划结果无法应用：{error}"})
                        continue
                    plan_text = plan.to_prompt()
                    item = self.store.get(item_id)
                    if item is None:
                        skipped.append({"id": item_id, "reason": "任务已不存在"})
                        continue

                batch_id = batch_id or TodoStore.new_batch_id()
                try:
                    self.scheduler.run_auto(item.id, batch_id=batch_id, plan=plan_text)
                    started.append(item.id)
                except ValueError as error:
                    skipped.append({"id": item.id, "reason": str(error)})
        finally:
            self._dispatching -= 1
        if batch_id:
            # 全部立即失败（例如执行器拒绝）时也要能收口；必须在 _dispatching
            # 归零之后判断，否则会被“派发中”保护拦住。
            self.maybe_close_batch(batch_id)
        if started:
            logger.info(
                "自动执行[%s]：启动 %d 个，跳过 %d 个（batch=%s）",
                trigger, len(started), len(skipped), batch_id,
            )
        return {"batch_id": batch_id, "started": started, "skipped": skipped}

    # ── 资格判定 ──────────────────────────────────────────────────────────────
    @staticmethod
    def _skip_reason(item: WorkItem) -> str:
        if item.auto_dispatched_at:
            return "已经自动执行过"
        if item.is_container or item.children_ids:
            return "父需求容器不进执行队列"
        if item.status == ItemStatus.READY:
            if item.repo is None:
                return "未映射仓库"
            return ""
        if item.status == ItemStatus.DISCOVERED:
            return ""
        if item.status in (ItemStatus.QUEUED, ItemStatus.RUNNING):
            return "已在执行队列中"
        if item.is_terminal():
            return f"任务已结束（{item.status.value}）"
        return f"当前状态不可执行（{item.status.value}）"

    def _candidates(self, item: WorkItem):
        if not item.tapd_workspace_id or self.scheduler.projects is None:
            return []
        return [
            m for m in self.scheduler.projects.find_all_by_workspace(item.tapd_workspace_id)
            if m.enabled
        ]

    async def _plan(self, item: WorkItem, candidates) -> Plan | None:
        backend = self.scheduler.executors.resolve(item.executor)
        return await plan_repo(
            item, candidates, backend=backend, enabled=True,
            timeout_s=PLANNER_TIMEOUT_S, max_candidates=PLANNER_MAX_CANDIDATES,
        )

    def _apply_plan(self, item: WorkItem, plan: Plan) -> None:
        self.scheduler.map_manually(item.id, plan.repo)
        fresh = self.store.get(item.id)
        if fresh is None:
            raise ValueError("任务已不存在")
        fresh.plan = plan.to_prompt()
        self.store.save(fresh)
        self.events.publish(
            Event("item_planned", fresh.id, {
                "repo": plan.repo, "reason": plan.reason, "source": plan.source,
            })
        )

    # ── 批次收口 ──────────────────────────────────────────────────────────────
    def on_item_done(self, item_id: str) -> None:
        """执行线程在任务结束后回调；最后一个完成时收口批次。"""
        item = self.store.get(item_id)
        if item is None or not item.auto or not item.auto_batch_id:
            return
        self.maybe_close_batch(item.auto_batch_id)

    def maybe_close_batch(self, batch_id: str) -> str:
        if not batch_id or self._dispatching:
            return ""
        if self.todos.get_batch(batch_id) is not None:
            return ""
        members = [i for i in self.store.list(limit=5000) if i.auto_batch_id == batch_id]
        if not members:
            return ""
        pending = [
            i for i in members
            if i.status in (ItemStatus.QUEUED, ItemStatus.RUNNING)
            # 失败重试会短暂回到 READY（attempt>0）；仍属本批次在途任务。
            or (i.status == ItemStatus.READY and i.auto_dispatched_at and i.attempt > 0)
            or (i.status == ItemStatus.READY and not i.auto_dispatched_at)
        ]
        if pending:
            return ""
        return self._close_batch(batch_id, members)

    def recover_batches(self) -> int:
        """启动补偿：进程中断导致没收口的批次，条件满足时补收口。"""
        closed = self.todos.closed_batch_ids()
        batch_ids = {
            item.auto_batch_id for item in self.store.list(limit=5000)
            if item.auto_batch_id and item.auto_batch_id not in closed
        }
        count = 0
        for batch_id in batch_ids:
            if self.maybe_close_batch(batch_id):
                count += 1
        return count

    def _close_batch(self, batch_id: str, members: list[WorkItem]) -> str:
        todos = [t for t in self.todos.list(limit=100000) if t.batch_id == batch_id]
        succeeded = sum(1 for i in members if i.status == ItemStatus.SUCCEEDED)
        failed = sum(1 for i in members if i.status == ItemStatus.FAILED)
        mergeable = sum(1 for t in todos if t.status == "pending" and t.can_merge)
        high = sum(1 for t in todos if t.impact_level == "high")
        repos = sorted({i.repo.name for i in members if i.repo is not None})
        if not repos:
            repos = sorted({t.repo for t in todos if t.repo})
        summary = (
            f"本轮自动执行 {len(members)} 个任务：成功 {succeeded}、失败 {failed}；"
            f"生成待办 {len(todos)} 条，其中可合并 {mergeable} 条、高影响 {high} 条。"
        )
        if failed:
            summary += "失败任务没有产生待办，请到任务中心查看原因。"
        batch = BatchSummary(
            id=batch_id,
            started_at=min((i.created_at for i in members), default=_now()),
            finished_at=_now(),
            item_ids=[i.id for i in members],
            total=len(members),
            succeeded=succeeded,
            failed=failed,
            todo_count=len(todos),
            mergeable=mergeable,
            high_impact=high,
            repos=repos,
            summary=summary,
        )
        self.todos.save_batch(batch)
        self.events.publish(Event("batch_ready", batch_id, {"batch": batch.to_dict()}))
        logger.info("自动执行批次收口 %s：%s", batch_id, summary)
        return batch_id