"""自动执行引擎：由**触发点**决定跑哪些任务，而不是靠全局扫描 + 一堆开关。

两个触发点：

1. **TAPD 定时拉取**：`tapd_poll_loop` 拉到新工单后，把本次新建的任务交给
   :meth:`AutomationEngine.dispatch` 自动执行。
2. **任务中心手动触发**：用户勾选若干任务点「自动执行」，只跑选中的这些。

链路：规划（仅多候选仓库时）→ 隔离 worktree 内 apply → 任务结束后由 scheduler
做**原子合并**：无冲突才写回主仓库工作区（不 commit）；有冲突则一个文件都不写，
任务留在任务中心，等人工处理后点「合并」重试。

幂等：`auto_dispatched_at` 一旦写入就不再自动派发；失败重试仍由 scheduler 内部处理。
"""

from __future__ import annotations

import logging
from typing import Any

from .events import Event
from .llm import AgentLLM, AgentModel, resolve_agent_model
from .models import ItemStatus, WorkItem
from .planner import Plan, build_short_plan, plan_repo

logger = logging.getLogger(__name__)

# 规划只在「TAPD 项目绑定了多个仓库」时发生，参数固定，不暴露成设置项。
PLANNER_TIMEOUT_S = 120
PLANNER_MAX_CANDIDATES = 4


class AutomationEngine:
    def __init__(self, *, scheduler, store, events, settings=None) -> None:
        self.scheduler = scheduler
        self.store = store
        self.events = events
        # App settings are read lazily at plan time so a model configured in the
        # UI takes effect without restarting the server.
        self.settings = settings

    # ── 触发入口 ──────────────────────────────────────────────────────────────
    async def dispatch(self, item_ids: list[str], *, trigger: str = "manual") -> dict[str, Any]:
        """自动执行指定的任务；返回实际启动与跳过的清单。

        只处理两类：已映射仓库的 READY，以及多候选仓库的 DISCOVERED（先规划）。
        其余（未映射、父容器、已自动派发过）如实跳过，不猜。
        """
        started: list[str] = []
        skipped: list[dict[str, str]] = []
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
            # 所有自动任务都有一份短计划：单仓/已映射任务不需要 LLM，但计划
            # 仍要生成并落库（由 scheduler.run_auto 写入 item.plan）。
            plan_text = build_short_plan(item).to_prompt()
            if item.status == ItemStatus.DISCOVERED:
                candidates = self._candidates(item)
                if not candidates:
                    skipped.append({"id": item_id, "reason": "未映射仓库，且没有可用的候选仓库"})
                    continue
                agent = self._agent_model()
                if len(candidates) > 1 and (
                    agent is None or not agent.base_url or not agent.api_key
                ):
                    skipped.append({
                        "id": item_id,
                        "reason": "未配置可用的后端 Agent 模型（需 Base URL 与 API Key）",
                    })
                    continue
                plan = await self._plan(item, candidates, agent)
                if plan is None:
                    skipped.append({"id": item_id, "reason": "自动规划未能确定仓库，请手动映射"})
                    continue
                try:
                    self._apply_plan(item, plan)
                except ValueError as error:
                    skipped.append({"id": item_id, "reason": f"规划结果无法应用：{error}"})
                    continue
                plan_text = plan.to_prompt() or build_short_plan(item).to_prompt()
                item = self.store.get(item_id)
                if item is None:
                    skipped.append({"id": item_id, "reason": "任务已不存在"})
                    continue
                # 规划结果统一落库，避免 LLM 返回空步骤时计划为空。
                item.plan = plan_text
                self.store.save(item)
            try:
                self.scheduler.run_auto(item.id, plan=plan_text)
                started.append(item.id)
            except ValueError as error:
                skipped.append({"id": item.id, "reason": str(error)})

        logger.info(
            "自动执行[%s]：启动 %d 个，跳过 %d 个",
            trigger, len(started), len(skipped),
        )
        return {"started": started, "skipped": skipped}

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

    def _agent_model(self) -> AgentModel | None:
        """The enabled backend-agent model, read fresh from saved settings."""
        if self.settings is None:
            return None
        ui = self.settings.load_ui_settings()
        return resolve_agent_model(
            ui.get("models") or [], str(ui.get("agent_model") or "")
        )

    async def _plan(
        self, item: WorkItem, candidates, agent: AgentModel | None = None
    ) -> Plan | None:
        agent = agent or self._agent_model()
        # 单候选仓库走规则版短计划，不需要 LLM；只有多候选才依赖模型。
        if agent is None and len(candidates) > 1:
            return None
        llm = AgentLLM(agent) if agent is not None else None
        return await plan_repo(
            item, candidates, llm=llm, enabled=agent is not None,
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