"""自动执行引擎：由**触发点**决定跑哪些任务，而不是靠全局扫描 + 一堆开关。

触发点：

1. **TAPD 定时拉取**：`tapd_poll_loop` 拉到新工单后，把本次新建的任务交给
   :meth:`AutomationEngine.dispatch` 自动执行；数据源配成 `dry-run` 时只出方案。
2. **任务中心手动触发**：用户勾选若干任务点「自动执行」，只跑选中的这些。
3. **重启 / 租约回收**：中断的自动任务重新排队后由 `dispatch(trigger="recovery")`
   接回，仍要过准入。

链路：准入（类型/仓库白名单 + 熔断）→ 可选的后端路由（LLM 按用户规则选
backend/model，开关关闭或已指定则跳过）→ 规划（仅多候选仓库时）→ 隔离 worktree
内 apply → 任务结束后由 scheduler 做**原子合并**：无冲突才写回主仓库工作区
（不 commit）；有冲突则一个文件都不写，任务留在任务中心，等人工处理后点
「合并」重试。

幂等：`auto_dispatched_at` 一旦写入就不再自动派发；失败重试仍由 scheduler 内部处理。
准入策略在 `admission.py`，熔断状态跨重启持久化。
"""

from __future__ import annotations

import logging
from typing import Any

from .admission import (
    AutomationPolicy,
    CircuitBreaker,
    admit,
    normalise_automation,
    outcome_of,
)
from .decompose import create_children, multi_repo_evidence
from .decompose import plan as plan_decomposition
from .events import Event
from .llm import AgentLLM, AgentModel, resolve_agent_model
from .models import ItemKind, ItemStatus, WorkItem
from .planner import Plan, build_short_plan, plan_repo
from .plans import PlanError

logger = logging.getLogger(__name__)

# 规划只在「TAPD 项目绑定了多个仓库」时发生，参数固定，不暴露成设置项。
PLANNER_TIMEOUT_S = 120
PLANNER_MAX_CANDIDATES = 4


class AutomationEngine:
    def __init__(
        self, *, scheduler, store, events, settings=None, circuit=None, router=None,
    ) -> None:
        self.scheduler = scheduler
        self.store = store
        self.events = events
        # App settings are read lazily at plan time so a model configured in the
        # UI takes effect without restarting the server.
        self.settings = settings
        # 熔断状态跨重启持久化；由 api 注入（测试可传内存实现）。
        self.circuit: CircuitBreaker | None = circuit
        # 执行后端路由（与手动执行共用同一实例）。None = 不路由。
        self.router = router
        # 任务结束后由 scheduler 调用，用于熔断记账。
        if circuit is not None:
            scheduler.auto_outcome_hook = self.record_outcome

    # ── 触发入口 ──────────────────────────────────────────────────────────────
    async def dispatch(
        self,
        item_ids: list[str],
        *,
        trigger: str = "manual",
        exec_mode: str | None = None,
        force: bool | None = None,
    ) -> dict[str, Any]:
        """自动执行指定的任务；返回实际启动与跳过的清单。

        只处理两类：已映射仓库的 READY，以及多候选仓库的 DISCOVERED（先规划）。
        其余（未映射、父容器、已自动派发过、准入拒绝）如实跳过，不猜。

        ``trigger`` 决定安全语义：``manual``（任务中心勾选）是显式授权，默认
        绕过准入/熔断并执行 apply；``tapd-poll`` / ``recovery`` 是无人值守
        链路，必须过准入，并遵守 ``exec_mode``（数据源配置成 dry-run 时绝不
        落盘改代码）。
        """
        autonomous = trigger != "manual"
        if force is None:
            force = not autonomous
        policy = self._policy()
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
            if not force:
                decision = admit(item, policy, self.circuit)
                if not decision.ok:
                    skipped.append({
                        "id": item_id,
                        "reason": decision.reason,
                        "code": decision.code,
                    })
                    continue
            # 执行后端路由：在规划/拆解之前定下来，拆出的子任务会继承这个决策。
            # 用户显式指定或已锁定的任务直接跳过（路由内部判定）。
            if self.router is not None:
                await self.router.maybe_route(item)
            # 所有自动任务都有一份短计划：单仓/已映射任务不需要 LLM，但计划
            # 仍要生成并落库（由 scheduler.run_auto 写入 item.plan）。
            plan_text = build_short_plan(item).to_prompt()
            if item.status == ItemStatus.DISCOVERED:
                candidates = self._candidates(item)
                # 多仓需求：开启自动拆解时先拆成子任务（只对顶层需求；
                # 子任务不再递归拆解，避免无限套娃）。
                # TAPD 绑定多个仓库只说明「可能跨仓」，还要求需求文本本身
                # 有跨仓或前后端信号，否则退回正常单仓规划，避免误拆。
                evidence = multi_repo_evidence(item, candidates)
                if (
                    policy.decompose
                    and not item.parent_ref
                    and item.kind == ItemKind.FEATURE
                    and evidence
                ):
                    children = await self._decompose(
                        item, candidates, policy, exec_mode, evidence=evidence
                    )
                    if children:
                        child_result = await self.dispatch(
                            [child.id for child in children],
                            trigger=trigger,
                            exec_mode=exec_mode,
                        )
                        started.extend(child_result.get("started") or [])
                        skipped.extend(child_result.get("skipped") or [])
                        continue
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
                plan, planner_llm = await self._plan(item, candidates, agent)
                planner_usage = planner_llm.last_usage if planner_llm is not None else None
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
                # 规划（选仓）的 token 也记进任务账本：它同样花的是模型钱。
                if planner_usage:
                    self.scheduler.record_usage(item, "llm", planner_usage)
                # 规划结果统一落库，避免 LLM 返回空步骤时计划为空。
                item.plan = plan_text
                self.store.save(item)
            fresh = self.store.get(item_id) or item
            # 规划可能刚写入 repo；再查一次熔断/仓库白名单，避免刚熔断的仓库
            # 因为“先规划后执行”的窗口被漏掉。
            if not force:
                decision = admit(fresh, policy, self.circuit)
                if not decision.ok:
                    skipped.append({
                        "id": fresh.id,
                        "reason": decision.reason,
                        "code": decision.code,
                    })
                    continue
            mode = exec_mode or fresh.exec_mode or "dry-run"
            try:
                self.scheduler.run_auto(
                    fresh.id, plan=plan_text, exec_mode=mode, manual=bool(force)
                )
                started.append(fresh.id)
            except PlanError as error:
                # 方案门禁拒绝：跳过并记录事件，不让自动调度循环因异常中断。
                code = getattr(error, "code", "")
                self.scheduler.events.publish(Event("plan_gate_skipped", fresh.id, {
                    "code": code, "reason": error.message,
                }))
                skipped.append({
                    "id": fresh.id, "reason": error.message, "code": code,
                })
            except ValueError as error:
                skipped.append({"id": fresh.id, "reason": str(error)})

        logger.info(
            "自动执行[%s]：启动 %d 个，跳过 %d 个",
            trigger, len(started), len(skipped),
        )
        return {"started": started, "skipped": skipped}

    # ── 资格判定 ──────────────────────────────────────────────────────────────
    @staticmethod
    def _skip_reason(item: WorkItem) -> str:
        if item.is_dry_run_done():
            # dry-run 成功只出了方案；允许之后用 apply 再派发一次。
            return ""
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

    async def _decompose(
        self,
        item: WorkItem,
        candidates,
        policy: AutomationPolicy,
        exec_mode: str | None,
        *,
        evidence: str = "",
    ) -> list[WorkItem]:
        agent = self._agent_model()
        if agent is None or not agent.base_url or not agent.api_key:
            return []
        llm = AgentLLM(agent)
        subtasks = await plan_decomposition(
            item,
            candidates,
            llm=llm,
            timeout_s=PLANNER_TIMEOUT_S,
            max_subtasks=policy.max_subtasks,
        )
        if not subtasks:
            return []
        if llm.last_usage:
            self.scheduler.record_usage(item, "llm", llm.last_usage)
        children = create_children(self.scheduler, item, subtasks)
        self.events.publish(Event("item_decomposed", item.id, {
            "children": [child.id for child in children],
            "trigger": "auto",
            "evidence": evidence,
        }))
        logger.info(
            "自动拆解 %s：%d 个子任务（模式 %s；依据 %s）",
            item.id, len(children), exec_mode or "inherit", evidence or "未记录",
        )
        return children

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
    ) -> tuple[Plan | None, AgentLLM | None]:
        agent = agent or self._agent_model()
        # 单候选仓库走规则版短计划，不需要 LLM；只有多候选才依赖模型。
        if agent is None and len(candidates) > 1:
            return None, None
        llm = AgentLLM(agent) if agent is not None else None
        plan = await plan_repo(
            item, candidates, llm=llm, enabled=agent is not None,
            timeout_s=PLANNER_TIMEOUT_S, max_candidates=PLANNER_MAX_CANDIDATES,
        )
        return plan, llm

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

    # ── 准入 / 熔断 ───────────────────────────────────────────────────────────
    def _policy(self) -> AutomationPolicy:
        """Read the latest saved automation policy (UI changes apply instantly)."""
        if self.settings is None:
            return AutomationPolicy()
        try:
            ui = self.settings.load_ui_settings()
        except Exception:
            logger.warning("读取自动执行配置失败，使用默认值", exc_info=True)
            return AutomationPolicy()
        return AutomationPolicy.from_settings(
            ui.get("automation") or normalise_automation(None)
        )

    def record_outcome(self, item: WorkItem) -> None:
        """Circuit bookkeeping for one finished auto task.

        Dry-runs prove nothing about repo health and are skipped. A success
        clears the repo's failure streak; a failure counts toward the threshold.
        """
        if self.circuit is None:
            return
        # 人工在任务中心显式勾选的「自动执行」不参与熔断：它是授权，不是
        # 无人值守链路的健康信号。只记录 approved_by="auto" 的派发。
        if str(getattr(item, "approved_by", "") or "") != "auto":
            return
        decision = outcome_of(item)
        if decision.dry_run or not decision.repo:
            return
        policy = self._policy()
        if decision.ok:
            self.circuit.record_success(decision.repo)
            return
        # 非仓库原因（CLI 缺失、超时、预算耗尽、进程崩溃）不算仓库的锅。
        if not decision.countable:
            return
        state = self.circuit.record_failure(
            decision.repo,
            error=decision.error,
            threshold=policy.failure_threshold,
            cooldown_minutes=policy.cooldown_minutes,
        )
        if state.get("open"):
            self.events.publish(Event("automation_circuit_open", item.id, state))

    def circuit_snapshot(self) -> dict[str, Any]:
        return {
            "policy": self._policy().__dict__,
            "repos": self.circuit.snapshot() if self.circuit is not None else {},
        }

    def reset_circuit(self, repo: str | None = None) -> int:
        return self.circuit.reset(repo) if self.circuit is not None else 0