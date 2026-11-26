"""统一的失败分类：重试、熔断、记忆记账的单一决策来源。

历史上三套彼此独立的判定并存，规则散落且互不相通：
- ``_is_retryable`` 关键词表（决定 CLI 执行失败是否重试）；
- ``failure_scope`` 字符串（决定环境/预算失败是否计入熔断与记忆）；
- ``loop.REPAIRABLE_STATES`` 门禁状态集合（决定验证失败是否再修一轮）。

本模块把这些判定收拢到一处，给出明确的 ``FailureKind`` 枚举与正交判定函数。
各消费点委托到这里，但**保持既有判定结果逐字不变**（严格兼容），以便后续
演进只需改这一处规则，而不用在多文件间同步关键词表。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class FailureKind(StrEnum):
    TRANSIENT = "transient"      # 超时/限流/服务暂时不可用，可调度级重试
    AUTH = "auth"                # 凭据失效，需人工登录，不可自动重试
    ENVIRONMENT = "environment"  # CLI 缺失 / worktree 失败，非仓库健康信号
    BUDGET = "budget"            # token 预算耗尽
    REPO = "repo"                # 代码/门禁本身的问题
    NONE = ""                    # 无失败


# 瞬时错误关键词：与原 Scheduler._is_retryable 完全一致，保证行为不变。
TRANSIENT_MARKERS = ("timeout", "timed out", "rate", "429", "503", "502")
# 认证失效关键词：与 dialect.StrictVerdict.auth_markers 一致。
AUTH_MARKERS = (
    "authentication required",
    "please use /login",
    "not logged in",
    "invalid api key",
    "401 unauthorized",
)
# 门禁状态：agent 再改一轮可能修好，与原 loop.REPAIRABLE_STATES 完全一致。
REPAIRABLE_STATES = frozenset({
    "failed",
    "review_blocked",
    "test_files_changed",
    "protected_files_changed",
    "too_many_files",
    "diff_too_large",
    "no_changes",
    "assertion_failed",
})
# 计数范围：failure_scope 为空或 repo 才算仓库失败（memory/policy/admission 一致）。
COUNTABLE_SCOPES = frozenset({"", "repo"})


def classify_exec_error(error: str | None) -> FailureKind:
    """对 CLI 执行的自由文本错误分类，供 trace 与报告展示使用。"""
    if not error:
        return FailureKind.NONE
    lowered = error.lower()
    if any(marker in lowered for marker in AUTH_MARKERS):
        return FailureKind.AUTH
    if any(marker in lowered for marker in TRANSIENT_MARKERS):
        return FailureKind.TRANSIENT
    return FailureKind.REPO


def is_retryable(error: str | None) -> bool:
    """CLI 执行失败是否值得调度级重试。

    与原 ``_is_retryable`` 逐字等价（只查瞬时关键词，不引入认证等新分支），
    这样「统一分类」不会悄悄改变任何既有重试决策。
    """
    if not error:
        return False
    lowered = error.lower()
    return any(word in lowered for word in TRANSIENT_MARKERS)


def is_countable_scope(scope: str | None) -> bool:
    """failure_scope 是否计入仓库熔断/跨任务记忆（等价 ``scope in {"", "repo"}``）。"""
    return str(scope or "") in COUNTABLE_SCOPES


def scope_for(kind: FailureKind) -> str:
    """分类 → ``RunResult.failure_scope`` 字符串（保持旧字段取值集合不变）。"""
    if kind in (FailureKind.ENVIRONMENT, FailureKind.BUDGET):
        return str(kind.value)
    return ""


def gate_repairable(gate: dict[str, Any] | None) -> bool:
    """门禁/评审状态是否可由 agent 再修一轮（等价原 ``loop.repairable``）。"""
    if not isinstance(gate, dict):
        return False
    states = {
        str(gate.get("state") or ""),
        str(gate.get("assertion_state") or ""),
    }
    return bool(states & REPAIRABLE_STATES)
