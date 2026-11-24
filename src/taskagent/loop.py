"""有界执行-反馈循环：让 agent 自己修到验证/评审通过。

之前 agent 只跑一次：门禁失败就把任务标 failed，失败信息留在 worktree 里没人
看。这个模块把「验证/评审的结论」翻译成下一轮的修复指令，在**同一个 worktree**
里继续修，最多 ``max_rounds`` 轮：

    执行 → 门禁（含 LLM 评审）→ 通过就收工
                              ↘ 未通过且可修 → 反馈注入提示词 → 再执行

停止条件（任一成立）：
- 门禁 + 评审通过；
- 跑满轮次（结果保留最后一轮，交给 scheduler 判 failed）；
- 门禁状态不是「agent 能修」的（例如没配置检查、需要人工审批）；
- 人工取消。

循环本身不碰主仓库、不合并；合并仍由 scheduler 在循环结束后做一次原子合并。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .models import RunResult, WorkItem
from .usage import TokenUsage

logger = logging.getLogger(__name__)

MAX_FEEDBACK_CHARS = 4_000
MAX_REVIEW_ISSUES = 8
MAX_CHECK_TAIL = 600

ProgressFn = Callable[[float, str], None]
ShouldCancelFn = Callable[[], bool]
GateFn = Callable[[WorkItem, RunResult], Awaitable[dict[str, Any]]]
StageFn = Callable[[str], None]

# 这些门禁状态说明「agent 再改一改就可能过」，值得再来一轮。
REPAIRABLE_STATES = frozenset({
    "failed",              # 检查失败
    "review_blocked",      # LLM 评审 block
    "test_files_changed",  # 动了测试/快照
    "protected_files_changed",
    "too_many_files",
    "diff_too_large",
    "no_changes",          # 声称做完了但没有改动
})

_STATE_HINTS = {
    "test_files_changed": "不要修改或删除已有测试/快照文件；需要补测试请新增测试文件。",
    "protected_files_changed": "不要修改测试配置、CI、依赖清单、锁文件、.env 等受保护文件。",
    "too_many_files": "缩小改动范围，只改与需求直接相关的文件。",
    "diff_too_large": "缩小改动范围，去掉与需求无关的重构。",
    "no_changes": "上一轮没有产生任何文件改动；请实际修改代码，不要只输出说明。",
    "review_needs_human": "评审判定需要人工审批；这不是继续改代码能解决的。",
}


@dataclass
class LoopOutcome:
    """一次执行循环的最终结果（合并前的状态）。"""

    result: RunResult
    gate: dict[str, Any] = field(default_factory=dict)
    rounds: int = 0
    cancelled: bool = False
    # 非空 = 因为 token 预算耗尽而提前停止（不会合并，任务置 failed）。
    budget_reason: str = ""


def repairable(gate: dict[str, Any]) -> bool:
    """Whether another agent round could plausibly fix this gate verdict."""
    return str(gate.get("state") or "") in REPAIRABLE_STATES


def _clip(text: str, limit: int) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _review_lines(gate: dict[str, Any]) -> list[str]:
    review = gate.get("review")
    if not isinstance(review, dict) or review.get("state") != "done":
        return []
    verdict = str(review.get("verdict") or "")
    if verdict not in {"comment", "block"}:
        return []
    lines = [f"评审结论：{verdict}" + (f"（{review.get('reason')}）" if review.get("reason") else "")]
    for issue in (review.get("issues") or [])[:MAX_REVIEW_ISSUES]:
        if not isinstance(issue, dict):
            continue
        where = f"[{issue.get('file')}] " if issue.get("file") else ""
        lines.append(
            f"- {issue.get('severity') or 'medium'}：{where}{_clip(issue.get('message', ''), 300)}"
        )
    if review.get("risks"):
        lines.append("- 风险标签：" + "、".join(str(tag) for tag in review["risks"][:6]))
    return lines


def _check_lines(gate: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for check in gate.get("checks") or []:
        if not isinstance(check, dict) or check.get("ok"):
            continue
        label = check.get("label") or check.get("id") or "检查"
        if check.get("timed_out"):
            reason = f"{label} 超时"
        elif check.get("error"):
            reason = f"{label} 无法执行：{check.get('error')}"
        else:
            reason = f"{label} 失败（退出码 {check.get('exit_code')}）"
        lines.append(f"- {reason}")
        tail = _clip(check.get("tail") or check.get("output") or "", MAX_CHECK_TAIL)
        if tail:
            lines.append("  输出节选：")
            lines.extend(f"  {line}" for line in tail.splitlines()[:20])
    return lines


def build_feedback(
    gate: dict[str, Any], *, round_no: int, max_rounds: int
) -> str:
    """Turn a failing gate verdict into the next round's repair instructions."""
    state = str(gate.get("state") or "")
    parts = [
        f"这是第 {round_no}/{max_rounds} 轮执行，上一轮没有通过验证/评审。",
        (
            "请只针对下面的问题修改。已经正确的部分不要重做；不要删除或削弱测试；"
            "修改后重新运行相关检查。"
        ),
    ]
    if gate.get("reason"):
        parts.append(f"失败原因：{_clip(gate['reason'], 500)}")
    hint = _STATE_HINTS.get(state)
    if hint:
        parts.append(f"要求：{hint}")
    review_lines = _review_lines(gate)
    if review_lines:
        parts.append("评审意见：")
        parts.extend(review_lines)
    check_lines = _check_lines(gate)
    if check_lines:
        parts.append("未通过的检查：")
        parts.extend(check_lines)
    if len(parts) == 2 and state:  # 没有具体原因时至少把状态写清楚
        parts.append(f"门禁状态：{state}")
    return _clip("\n".join(parts), MAX_FEEDBACK_CHARS)


async def run_rounds(
    *,
    item: WorkItem,
    backend: Any,
    gate: GateFn,
    max_rounds: int = 1,
    progress: ProgressFn | None = None,
    should_cancel: ShouldCancelFn | None = None,
    stage: StageFn | None = None,
    record_usage: Callable[[str, TokenUsage], None] | None = None,
    budget_reason: Callable[[], str] | None = None,
) -> LoopOutcome:
    """Run the agent up to ``max_rounds`` times until gate + review pass.

    The worktree is reused across rounds on purpose: the next round edits the
    previous diff instead of starting over, so feedback can converge.
    """
    rounds = max(1, int(max_rounds))
    cancel = should_cancel or (lambda: False)
    emit = progress or (lambda _value, _stage: None)
    announce = stage or (lambda _text: None)

    last_result: RunResult | None = None
    last_gate: dict[str, Any] = {}
    for number in range(1, rounds + 1):
        if cancel():
            break
        if number > 1:
            announce(f"第 {number}/{rounds} 轮：根据上一轮问题修复中…")
        result = await backend.run(
            item, progress=lambda value, text: emit(value, text), should_cancel=cancel
        )
        last_result = result
        result.rounds = number
        if record_usage is not None and result.usage:
            record_usage("cli", TokenUsage.from_dict(result.usage))
        if cancel():
            return LoopOutcome(result=result, rounds=number, cancelled=True)
        if result.dry_run or not result.ok:
            return LoopOutcome(result=result, rounds=number)
        announce(f"第 {number}/{rounds} 轮：验证 + 评审中…")
        verdict = await gate(item, result)
        result.verification = verdict
        last_gate = verdict
        if verdict.get("ok"):
            return LoopOutcome(result=result, gate=verdict, rounds=number)
        if number >= rounds:
            break
        if not repairable(verdict):
            logger.info(
                "任务 %s 第 %d 轮门禁状态 %s 不可由 agent 修复，停止循环",
                item.id, number, verdict.get("state"),
            )
            break
        reason = budget_reason() if budget_reason is not None else ""
        if reason:
            logger.info("任务 %s 第 %d 轮后停止：%s", item.id, number, reason)
            return LoopOutcome(
                result=last_result, gate=verdict, rounds=number, budget_reason=reason
            )
        item.feedback = build_feedback(verdict, round_no=number, max_rounds=rounds)
        logger.info("任务 %s 第 %d 轮未通过（%s），带着反馈重试", item.id, number, verdict.get("state"))

    if last_result is None:  # 还没跑就被取消
        last_result = RunResult(ok=False, dry_run=False, command="", error="cancelled")
    return LoopOutcome(result=last_result, gate=last_gate, rounds=max(1, last_result.rounds))