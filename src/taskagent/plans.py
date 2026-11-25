"""方案层：复杂需求的多候选方案、确定性评分与人工确认状态机。

设计要点（与 ``docs/auto-execution-and-plan-selection-design.md`` 对齐，但按
「恢复方案层」需求重新收紧语义）：

- 只有「人工」能把方案置为 ``confirmed``。本模块之外的任何代码路径都不写
  ``confirmed``；生成只产生 ``pending_confirmation``，确认/驳回走
  :func:`confirm_plan` / :func:`reject_plan`，其余递进统一由
  :func:`_transition` 的白名单校验。
- 评分是纯确定性代码（规则 + 加权和 0..100），不调用模型、不依赖付费服务。
  模型即便不可用也照常出方案，但**没有 confirmed 方案就绝不执行**（门禁在
  ``scheduler.run`` 里，失败是硬拒绝而非静默放行）。
- 判定是否复杂、候选数、评分权重全部由配置驱动；开关默认关闭，简单需求
  继续走原有单方案执行路径。
- 落盘为 ``<data_dir>/plans/<requirement_id>.json``，只新增文件，不改
  ``WorkItem`` 既有字段语义。
"""

from __future__ import annotations

import fcntl
import json
import math
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .models import ItemKind, WorkItem

PLAN_SCHEMA = "taskagent.plan_layer.v1"
SCORE_MIN = 0.0
SCORE_MAX = 100.0

# 可断言的错误标识（API 以 HTTP 409 + detail.code 返回）。
ERR_PLAN_NOT_CONFIRMED = "PLAN_NOT_CONFIRMED"
ERR_PLAN_MISMATCH = "PLAN_MISMATCH"
ERR_INVALID_TRANSITION = "INVALID_PLAN_TRANSITION"
ERR_PLAN_NOT_FOUND = "PLAN_NOT_FOUND"
ERR_PLAN_CONFLICT = "PLAN_CONFLICT"

_SCORE_DIMENSIONS = ("clarity", "risk", "scope", "trace")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


class PlanStatus(str, Enum):
    PENDING_CONFIRMATION = "pending_confirmation"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


# 状态机：唯一的出边在 pending_confirmation；其余状态均为终态。
ALLOWED_TRANSITIONS: dict[PlanStatus, frozenset[PlanStatus]] = {
    PlanStatus.PENDING_CONFIRMATION: frozenset({
        PlanStatus.CONFIRMED,
        PlanStatus.REJECTED,
        PlanStatus.SUPERSEDED,
    }),
    PlanStatus.CONFIRMED: frozenset(),
    PlanStatus.REJECTED: frozenset(),
    PlanStatus.SUPERSEDED: frozenset(),
}


class PlanError(Exception):
    """方案层错误；``code`` 面向调用方断言，不依赖自然语言文案。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class PlanCandidate:
    """一条候选方案。字段集即验收要求的最小持久化集合（外加审计字段）。"""

    plan_id: str
    requirement_id: str
    title: str
    steps: list[str] = field(default_factory=list)
    score: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)
    rationale: str = ""
    status: PlanStatus = PlanStatus.PENDING_CONFIRMATION
    created_at: str = field(default_factory=_now)
    source: str = "rule"
    # 人工决策审计：只有 confirm_plan / reject_plan 会写这些字段。
    operator: str = ""
    decided_at: str = ""
    schema: str = PLAN_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "requirement_id": self.requirement_id,
            "title": self.title,
            "steps": list(self.steps),
            "score": round(float(self.score), 2),
            "score_breakdown": {
                key: round(float(value), 2) for key, value in self.score_breakdown.items()
            },
            "rationale": self.rationale,
            "status": self.status.value,
            "created_at": self.created_at,
            "source": self.source,
            "operator": self.operator,
            "decided_at": self.decided_at,
            "schema": self.schema,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanCandidate:
        try:
            status = PlanStatus(str(data.get("status", PlanStatus.PENDING_CONFIRMATION.value)))
        except ValueError:
            status = PlanStatus.PENDING_CONFIRMATION
        breakdown = data.get("score_breakdown") or {}
        if not isinstance(breakdown, dict):
            breakdown = {}
        steps = data.get("steps") or []
        if isinstance(steps, str):
            steps = [steps]
        return cls(
            plan_id=str(data.get("plan_id") or ""),
            requirement_id=str(data.get("requirement_id") or ""),
            title=str(data.get("title") or ""),
            steps=[str(step) for step in steps if str(step).strip()],
            score=_clamp_score(data.get("score", 0.0)),
            score_breakdown={
                str(key): _clamp_score(value) for key, value in breakdown.items()
            },
            rationale=str(data.get("rationale") or ""),
            status=status,
            created_at=str(data.get("created_at") or _now()),
            source=str(data.get("source") or "rule"),
            operator=str(data.get("operator") or ""),
            decided_at=str(data.get("decided_at") or ""),
            schema=str(data.get("schema") or PLAN_SCHEMA),
        )

    def steps_text(self) -> str:
        return "\n".join(f"{index + 1}. {step}" for index, step in enumerate(self.steps))


def _clamp_score(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return SCORE_MIN
    if math.isnan(number):
        return SCORE_MIN
    return round(min(SCORE_MAX, max(SCORE_MIN, number)), 2)


def _safe_int(value: Any, default: int, *, minimum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, number)


def _len(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


DEFAULT_PLAN_LAYER: dict[str, Any] = {
    # 默认关闭：不开启时所有任务（含复杂需求）沿用原有单方案执行路径。
    "enabled": False,
    "require_for_all": False,
    # 显式复杂度规则：验收+测试线索条数（父子/未映射各加分）达到阈值即复杂。
    "min_steps": 4,
    # 复杂需求一次至少产出 2 个候选方案。
    "min_candidates": 2,
    "force_kinds": [],
    # 四个维度权重；归一化后和为 1，总分固定 0..100。
    "score_weights": {"clarity": 0.25, "risk": 0.25, "scope": 0.25, "trace": 0.25},
}


def normalise_plan_layer(value: Any) -> dict[str, Any]:
    """把 UI/文件中的 plan_layer 配置收敛成安全、可解释的默认值。"""
    raw = value if isinstance(value, dict) else {}
    weights_raw = raw.get("score_weights")
    weights_raw = weights_raw if isinstance(weights_raw, dict) else {}
    weights: dict[str, float] = {}
    for key in _SCORE_DIMENSIONS:
        try:
            weight = float(weights_raw.get(key, DEFAULT_PLAN_LAYER["score_weights"][key]))
        except (TypeError, ValueError):
            weight = DEFAULT_PLAN_LAYER["score_weights"][key]
        weights[key] = min(1.0, max(0.0, weight))
    total = sum(weights.values())
    if total <= 0:
        weights = dict(DEFAULT_PLAN_LAYER["score_weights"])
        total = sum(weights.values())
    weights = {key: round(value / total, 4) for key, value in weights.items()}
    force_kinds = raw.get("force_kinds") or []
    if isinstance(force_kinds, str):
        force_kinds = [part.strip() for part in force_kinds.split(",") if part.strip()]
    return {
        "enabled": bool(raw.get("enabled", DEFAULT_PLAN_LAYER["enabled"])),
        "require_for_all": bool(
            raw.get("require_for_all", DEFAULT_PLAN_LAYER["require_for_all"])
        ),
        "min_steps": _safe_int(
            raw.get("min_steps", DEFAULT_PLAN_LAYER["min_steps"]),
            DEFAULT_PLAN_LAYER["min_steps"], minimum=1,
        ),
        "min_candidates": _safe_int(
            raw.get("min_candidates", DEFAULT_PLAN_LAYER["min_candidates"]),
            DEFAULT_PLAN_LAYER["min_candidates"], minimum=2,
        ),
        "force_kinds": [str(kind).strip() for kind in force_kinds if str(kind).strip()],
        "score_weights": weights,
    }


def complexity_steps(item: WorkItem) -> int:
    """显式的复杂度计数；公式固定，阈值由 ``plan_layer.min_steps`` 配置。"""
    spec = item.spec if isinstance(item.spec, dict) else {}
    count = _len(spec.get("acceptance")) + _len(spec.get("test_hints"))
    if item.parent_ref or item.children_ids:
        count += 2
    if item.repo is None:
        count += 1
    if item.kind == ItemKind.FEATURE:
        count += 1
    return int(count)


def is_complex(item: WorkItem, config: dict[str, Any] | None = None) -> bool:
    """复杂需求判定（代码中显式定义、配置可调）。容器节点永不进入执行队列。"""
    cfg = normalise_plan_layer(config)
    if not cfg["enabled"] or item.is_container:
        return False
    if cfg["require_for_all"]:
        return True
    if item.kind.value in cfg["force_kinds"]:
        return True
    return complexity_steps(item) >= cfg["min_steps"]


# 方案模板：纯规则文本，不伪装成模型输出。不同思路的步骤数与风险不同，
# 评分因此有确定性的区分度；顺序由 score + plan_id 决定，可复现。
_APPROACHES: tuple[dict[str, Any], ...] = (
    {
        "suffix": "保守增量",
        "rationale": "改动面最小、回归风险最低，优先保证现有行为不被破坏。",
        "steps": (
            "阅读需求与相关代码，定位最小改动点",
            "在不改变既有接口语义的前提下做增量修改",
            "运行相关测试，确认无回归",
        ),
        "risk": 90.0,
        "scope_bonus": 18.0,
        "clarity_bonus": 0.0,
    },
    {
        "suffix": "分层落地",
        "rationale": "先划清模块边界再分层实现，适合跨模块需求，代价是改动面更大。",
        "steps": (
            "梳理需求涉及的模块边界与依赖关系",
            "分层实现新能力并完成必要接线",
            "为新增逻辑补充测试",
            "运行相关测试并复核接口契约",
        ),
        "risk": 72.0,
        "scope_bonus": 4.0,
        "clarity_bonus": 8.0,
    },
    {
        "suffix": "端到端验证",
        "rationale": "以验收标准反向驱动实现，验证最充分，但步骤最多、周期最长。",
        "steps": (
            "把验收标准转成可执行的检查项",
            "按检查项完成实现与接线",
            "补充覆盖验收项的测试",
            "运行完整测试与构建并汇总结果",
        ),
        "risk": 80.0,
        "scope_bonus": 0.0,
        "clarity_bonus": 14.0,
    },
)


def _score(item: WorkItem, approach: dict[str, Any], config: dict[str, Any]) -> dict[str, float]:
    """确定性评分：各维度 0..100，加权和仍落在 0..100。"""
    spec = item.spec if isinstance(item.spec, dict) else {}
    acceptance_count = _len(spec.get("acceptance"))
    test_count = _len(spec.get("test_hints"))
    risk = str(spec.get("risk") or "low").lower()

    clarity = min(
        SCORE_MAX, 55.0 + 10.0 * acceptance_count + float(approach["clarity_bonus"])
    )
    risk_value = {"low": 92.0, "medium": 74.0, "high": 50.0}.get(risk, 80.0)
    risk_value = min(SCORE_MAX, max(0.0, risk_value - (100.0 - float(approach["risk"])) / 4.0))
    scope = min(
        SCORE_MAX,
        max(20.0, 100.0 - 12.0 * (len(approach["steps"]) - 2) + float(approach["scope_bonus"])),
    )
    trace = 70.0
    if item.parent_ref or item.children_ids:
        trace += 12.0
    if item.tapd_workspace_id or item.source_ref:
        trace += 10.0
    if test_count:
        trace += min(8.0, 2.0 * test_count)
    values = {
        "clarity": _clamp_score(clarity),
        "risk": _clamp_score(risk_value),
        "scope": _clamp_score(scope),
        "trace": _clamp_score(trace),
    }
    weights = config["score_weights"]
    values["total"] = _clamp_score(
        sum(values[key] * float(weights[key]) for key in _SCORE_DIMENSIONS)
    )
    return values


def generate_candidates(
    item: WorkItem, config: dict[str, Any] | None = None
) -> list[PlanCandidate]:
    """为一条需求产出至少 ``min_candidates`` 个候选方案（纯规则、零外部依赖）。

    不写任何存储，由调用方决定何时落盘；重复调用得到相同 plan_id 与评分，
    因此对同一需求的重复生成是幂等的（不会凭空多出第二套方案）。
    """
    cfg = normalise_plan_layer(config)
    count = max(2, int(cfg["min_candidates"]))
    title = item.title.strip() or "该需求"
    plans: list[PlanCandidate] = []
    for index in range(count):
        approach = _APPROACHES[index % len(_APPROACHES)]
        breakdown = _score(item, approach, cfg)
        # 首步带上需求标题，其余步骤沿用模板；总步数由模板决定，评分可解释。
        steps = [f"阅读需求「{title}」与相关代码，明确改动范围"] + list(
            approach["steps"][1:]
        )
        plan = PlanCandidate(
            plan_id=f"plan-{item.id}-{index + 1}",
            requirement_id=item.id,
            title=f"方案 {chr(65 + index)} · {approach['suffix']}",
            steps=list(steps),
            score=breakdown.pop("total"),
            score_breakdown=breakdown,
            rationale=approach["rationale"],
            status=PlanStatus.PENDING_CONFIRMATION,
            source="rule",
        )
        plans.append(plan)
    return sorted(plans, key=lambda plan: (-plan.score, plan.plan_id))


def _transition(plan: PlanCandidate, target: PlanStatus) -> PlanCandidate:
    """内部状态机原语；对外的确认/驳回只能通过 confirm_plan/reject_plan。"""
    if target not in ALLOWED_TRANSITIONS.get(plan.status, frozenset()):
        raise PlanError(
            ERR_INVALID_TRANSITION,
            f"方案 {plan.plan_id} 不允许从 {plan.status.value} 变为 {target.value}",
        )
    plan.status = target
    return plan


class PlanStore:
    """按需求维度持久化候选方案（新增文件，原子写，进程重启后可查）。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._lock_path = self.root / ".lock"

    @contextmanager
    def locked(self) -> Iterator[None]:
        """线程 + 进程级互斥；确认/生成的读-改-写必须整体在锁内完成。"""
        with self._lock, self._lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _path(self, requirement_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", requirement_id) or "unnamed"
        return self.root / f"{safe}.json"

    def save_unlocked(self, plans: list[PlanCandidate]) -> None:
        requirement_id = plans[0].requirement_id
        payload = {
            "schema": PLAN_SCHEMA,
            "requirement_id": requirement_id,
            "plans": [plan.to_dict() for plan in plans],
        }
        path = self._path(requirement_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(path)

    def save(self, plans: list[PlanCandidate]) -> list[PlanCandidate]:
        if not plans:
            return []
        with self.locked():
            self.save_unlocked(plans)
        return plans

    def list_by_requirement(self, requirement_id: str) -> list[PlanCandidate]:
        """该需求的全部候选方案，按 score 降序（同分按 plan_id 稳定排序）。"""
        path = self._path(requirement_id)
        if not path.is_file():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        rows = payload.get("plans") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []
        plans = [
            PlanCandidate.from_dict(row) for row in rows if isinstance(row, dict)
        ]
        return sorted(plans, key=lambda plan: (-plan.score, plan.plan_id))

    def find(self, plan_id: str) -> tuple[str, PlanCandidate] | None:
        """跨需求按 plan_id 查找；返回 (requirement_id, plan)。"""
        for path in sorted(self.root.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rows = payload.get("plans") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, dict) and str(row.get("plan_id") or "") == plan_id:
                    plan = PlanCandidate.from_dict(row)
                    requirement_id = str(
                        row.get("requirement_id") or plan.requirement_id
                    )
                    return requirement_id, plan
        return None

    def confirmed_plan(self, requirement_id: str) -> PlanCandidate | None:
        for plan in self.list_by_requirement(requirement_id):
            if plan.status == PlanStatus.CONFIRMED:
                return plan
        return None


def _decide(
    store: PlanStore,
    requirement_id: str,
    plan_id: str,
    *,
    target: PlanStatus,
    operator: str,
) -> tuple[PlanCandidate, list[PlanCandidate]]:
    with store.locked():
        # 读-改-写整体持锁：跨进程并发的确认/生成不会互相覆盖或产生两条 confirmed。
        plans = store.list_by_requirement(requirement_id)
        matched = next((plan for plan in plans if plan.plan_id == plan_id), None)
        if matched is None:
            raise PlanError(ERR_PLAN_NOT_FOUND, f"方案不存在：{plan_id}")
        # 重复决策幂等：不写盘、不改变状态，调用方不应产生二次执行。
        if matched.status == target:
            return matched, plans
        if matched.status != PlanStatus.PENDING_CONFIRMATION:
            raise PlanError(
                ERR_INVALID_TRANSITION,
                f"方案 {plan_id} 已处于 {matched.status.value}，不能改为 {target.value}",
            )
        who = str(operator or "").strip() or "unattributed"
        decided_at = _now()
        _transition(matched, target)
        matched.operator = who
        matched.decided_at = decided_at
        if target == PlanStatus.CONFIRMED:
            # 确认一份，同需求其余 pending 候选全部自动置为 superseded。
            for other in plans:
                if (
                    other.plan_id == plan_id
                    or other.status != PlanStatus.PENDING_CONFIRMATION
                ):
                    continue
                _transition(other, PlanStatus.SUPERSEDED)
                other.operator = who
                other.decided_at = decided_at
        store.save_unlocked(plans)
        return matched, store.list_by_requirement(requirement_id)


def confirm_plan(
    store: PlanStore, requirement_id: str, plan_id: str, *, operator: str = ""
) -> tuple[PlanCandidate, list[PlanCandidate]]:
    """人工确认唯一方案；其余候选自动 superseded。幂等，重复确认不产生副作用。"""
    return _decide(
        store, requirement_id, plan_id, target=PlanStatus.CONFIRMED, operator=operator
    )


def reject_plan(
    store: PlanStore, requirement_id: str, plan_id: str, *, operator: str = ""
) -> tuple[PlanCandidate, list[PlanCandidate]]:
    """人工驳回方案；不改变同需求其他候选的状态。"""
    return _decide(
        store, requirement_id, plan_id, target=PlanStatus.REJECTED, operator=operator
    )
