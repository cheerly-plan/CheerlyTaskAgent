"""自动执行准入（admission）与熔断（circuit breaker）。

自动链路在把任务送进 worktree 之前先过一道**确定性**准入，回答三个问题：

1. 这类任务允许自动跑吗（``allowed_kinds``，空 = 不限）？
2. 这个仓库在自动放行范围内吗（``repo_allowlist``，空 = 不限）？
3. 这个仓库是不是刚刚连续失败太多次（熔断中，冷却期内不再自动派发）？

原则与路由一致：**判不出来就不放行**，把任务留在任务中心等人工确认，而不是
猜一个仓库、或在一个已经连续失败的仓库上继续烧 token。人工勾选「自动执行」
是显式授权，默认绕过准入与熔断（仍要过验证门禁/评审）。

熔断状态落在 ``data/automation-circuit.json``，只记仓库维度的连续失败次数、
打开时间与最后一次错误；成功一次就清零。服务重启不会丢熔断状态——失败的仓库
不会因为重启而被“洗白”。
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

VALID_KINDS = ("bug", "feature", "task")

# UI / API 的默认值。空列表 = 不限制，保持老用户升级后的行为不变；
# 想收紧就在「系统设置 → 自动执行」里勾选。
AUTOMATION_DEFAULTS: dict[str, Any] = {
    "allowed_kinds": [],      # 空 = bug / feature / task 全允许
    "repo_allowlist": [],     # 空 = 所有已映射仓库都允许
    "failure_threshold": 3,   # 同仓连续失败 N 次 -> 熔断
    "cooldown_minutes": 30,   # 熔断冷却时长；到期后放行一次试探
    "max_recoveries": 1,      # 服务重启后每个自动任务最多自动重排几次
    # 多仓需求自动拆解：默认关闭（拆解会改变任务结构，先让人确认）。
    "decompose": False,
    "max_subtasks": 4,
}


def normalise_automation(value: Any) -> dict[str, Any]:
    """Clamp a UI/API payload to the known automation config shape."""
    base: dict[str, Any] = {
        **AUTOMATION_DEFAULTS,
        "allowed_kinds": list(AUTOMATION_DEFAULTS["allowed_kinds"]),
        "repo_allowlist": list(AUTOMATION_DEFAULTS["repo_allowlist"]),
    }
    if not isinstance(value, dict):
        return base

    raw_kinds = value.get("allowed_kinds")
    # 手写 settings.json 时允许 "bug,feature" 这种写法，避免字符串被当成
    # “没配置”而静默放开全部类型。
    if isinstance(raw_kinds, str):
        raw_kinds = raw_kinds.split(",")
    if isinstance(raw_kinds, (list, tuple)):
        kinds: list[str] = []
        for kind in raw_kinds:
            text = str(kind or "").strip().lower()
            if text in VALID_KINDS and text not in kinds:
                kinds.append(text)
        base["allowed_kinds"] = kinds

    raw_repos = value.get("repo_allowlist")
    if isinstance(raw_repos, str):
        raw_repos = raw_repos.split(",")
    if isinstance(raw_repos, (list, tuple)):
        repos: list[str] = []
        for repo in raw_repos:
            text = str(repo or "").strip()
            if text and text not in repos:
                repos.append(text)
        base["repo_allowlist"] = repos[:200]

    def _int(key: str, low: int, high: int) -> int:
        try:
            number = int(value.get(key, base[key]))
        except (TypeError, ValueError):
            number = int(base[key])
        return max(low, min(high, number))

    base["failure_threshold"] = _int("failure_threshold", 1, 50)
    base["cooldown_minutes"] = _int("cooldown_minutes", 1, 24 * 60)
    base["max_recoveries"] = _int("max_recoveries", 0, 10)
    base["decompose"] = bool(value.get("decompose", base["decompose"]))
    base["max_subtasks"] = _int("max_subtasks", 2, 5)
    return base


@dataclass(frozen=True)
class AutomationPolicy:
    """Resolved admission policy for one dispatch pass."""

    allowed_kinds: tuple[str, ...] = ()
    repo_allowlist: tuple[str, ...] = ()
    failure_threshold: int = 3
    cooldown_minutes: int = 30
    max_recoveries: int = 1
    decompose: bool = False
    max_subtasks: int = 4

    @classmethod
    def from_settings(cls, value: Any) -> AutomationPolicy:
        data = normalise_automation(value)
        return cls(
            allowed_kinds=tuple(data["allowed_kinds"]),
            repo_allowlist=tuple(data["repo_allowlist"]),
            failure_threshold=int(data["failure_threshold"]),
            cooldown_minutes=int(data["cooldown_minutes"]),
            max_recoveries=int(data["max_recoveries"]),
            decompose=bool(data["decompose"]),
            max_subtasks=int(data["max_subtasks"]),
        )

    def allows_kind(self, kind: str) -> bool:
        return not self.allowed_kinds or str(kind).lower() in self.allowed_kinds

    def allows_repo(self, repo: str) -> bool:
        return not self.repo_allowlist or str(repo) in self.repo_allowlist


@dataclass
class AdmissionDecision:
    """One deterministic yes/no with a human-readable reason."""

    ok: bool
    code: str = ""
    reason: str = ""

    @classmethod
    def allow(cls) -> AdmissionDecision:
        return cls(ok=True)

    @classmethod
    def deny(cls, code: str, reason: str) -> AdmissionDecision:
        return cls(ok=False, code=code, reason=reason)


def admit(
    item: Any,
    policy: AutomationPolicy,
    circuit: CircuitBreaker | None = None,
    *,
    check_circuit: bool = True,
) -> AdmissionDecision:
    """Admission for an item that already passed the structural checks.

    Structural checks (status/mapping/already-dispatched) stay in
    ``AutomationEngine._skip_reason``; this function only owns *policy*.
    """
    kind = getattr(getattr(item, "kind", ""), "value", getattr(item, "kind", ""))
    if not policy.allows_kind(str(kind)):
        allowed = "、".join(policy.allowed_kinds) or "全部"
        return AdmissionDecision.deny(
            "kind_not_allowed",
            f"任务类型 {kind} 不在自动执行允许范围（{allowed}）",
        )
    repo = getattr(getattr(item, "repo", None), "name", "") or ""
    if repo and not policy.allows_repo(repo):
        return AdmissionDecision.deny(
            "repo_not_allowed",
            f"仓库 {repo} 不在自动放行名单内",
        )
    if check_circuit and circuit is not None and repo:
        state = circuit.check(repo)
        if state.get("open"):
            until = str(state.get("until") or "")
            failures = int(state.get("failures") or 0)
            return AdmissionDecision.deny(
                "circuit_open",
                f"仓库 {repo} 连续失败 {failures} 次，熔断至 {until}（人工处理后可在自动执行里重试）",
            )
    return AdmissionDecision.allow()


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


class CircuitBreaker:
    """Per-repo consecutive-failure circuit, persisted as JSON.

    Deliberately small: one file, atomic replace, no background thread. Reads
    are cheap (called once per dispatched item) and writes only happen when a
    run finishes or a cooldown expires.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self._lock = threading.Lock()
        self._repos: dict[str, dict[str, Any]] = self._load()

    # ── persistence ───────────────────────────────────────────────────────────
    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        repos = data.get("repos") if isinstance(data, dict) else None
        if not isinstance(repos, dict):
            return {}
        return {
            str(name): dict(state)
            for name, state in repos.items()
            if isinstance(name, str) and isinstance(state, dict)
        }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps({"repos": self._repos}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    # ── queries ───────────────────────────────────────────────────────────────
    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {name: dict(state) for name, state in self._repos.items()}

    def check(self, repo: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Return the repo's circuit state; lazily close an expired cooldown."""
        moment = now or _now()
        with self._lock:
            state = self._repos.get(repo)
            if not state:
                return {"open": False, "failures": 0, "until": "", "last_error": ""}
            until = str(state.get("open_until") or "")
            changed = False
            if until:
                try:
                    deadline = datetime.fromisoformat(until)
                except ValueError:
                    deadline = None
                if deadline is not None and deadline <= moment:
                    # 冷却结束：清零失败计数，给这个仓库一次新的试探机会。
                    state.update({"failures": 0, "open_until": "", "last_error": ""})
                    changed = True
                    until = ""
            if changed:
                self._repos[repo] = state
                self._save()
            return {
                "open": bool(until),
                "failures": int(state.get("failures") or 0),
                "until": until,
                "last_error": str(state.get("last_error") or ""),
            }

    # ── recording ─────────────────────────────────────────────────────────────
    def record_success(self, repo: str) -> None:
        if not repo:
            return
        with self._lock:
            state = self._repos.get(repo)
            if not state:
                return
            if not state.get("failures") and not state.get("open_until"):
                return
            self._repos.pop(repo, None)
            self._save()
            logger.info("自动执行熔断：仓库 %s 成功一次，计数清零", repo)

    def record_failure(
        self,
        repo: str,
        *,
        error: str = "",
        threshold: int = 3,
        cooldown_minutes: int = 30,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Count one failure; open the circuit when the threshold is reached."""
        if not repo:
            return {"open": False, "failures": 0, "until": ""}
        moment = now or _now()
        limit = max(1, int(threshold))
        with self._lock:
            state = dict(self._repos.get(repo) or {})
            failures = int(state.get("failures") or 0) + 1
            state["failures"] = failures
            state["last_error"] = str(error or "")[:500]
            state["updated_at"] = _iso(moment)
            if failures >= limit:
                state["open_until"] = _iso(
                    moment + timedelta(minutes=max(1, int(cooldown_minutes)))
                )
                logger.warning(
                    "自动执行熔断：仓库 %s 连续失败 %d 次，冷却至 %s",
                    repo, failures, state["open_until"],
                )
            self._repos[repo] = state
            self._save()
            return {
                "open": bool(state.get("open_until")),
                "failures": failures,
                "until": str(state.get("open_until") or ""),
                "last_error": str(state.get("last_error") or ""),
            }

    def reset(self, repo: str | None = None) -> int:
        """Clear one repo (or all when ``repo`` is None). Returns removed count."""
        with self._lock:
            if repo is None:
                removed = len(self._repos)
                self._repos = {}
            else:
                removed = 1 if self._repos.pop(repo, None) is not None else 0
            if removed:
                self._save()
            return removed


@dataclass
class OutcomeRecord:
    """What one finished auto task means for the circuit."""

    repo: str = ""
    ok: bool = False
    error: str = ""
    dry_run: bool = False
    # False = 失败原因不在仓库（CLI 缺失/超时/预算），不应计入熔断。
    countable: bool = True
    detail: dict[str, Any] = field(default_factory=dict)


def outcome_of(item: Any) -> OutcomeRecord:
    """Classify a finished item into success/failure for the circuit.

    Dry-run proves nothing about the repo, so it neither opens nor closes the
    circuit. A merge that was blocked by conflicts counts as a failure signal:
    it usually means the base is stale or the change touches contested files.
    """
    repo = getattr(getattr(item, "repo", None), "name", "") or ""
    result = getattr(item, "result", None)
    if result is not None and getattr(result, "dry_run", False):
        return OutcomeRecord(repo=repo, ok=True, dry_run=True)
    verification = getattr(result, "verification", {}) if result is not None else {}
    verified = not isinstance(verification, dict) or verification.get("ok", True)
    succeeded = getattr(getattr(item, "status", ""), "value", getattr(item, "status", "")) == "succeeded"
    ok = bool(succeeded and getattr(item, "merge_state", "") != "blocked" and verified)
    scope = str(getattr(result, "failure_scope", "") or "")
    countable = result is not None and scope in ("", "repo")
    error = (
        getattr(item, "error", "")
        or getattr(result, "error", "")
        or getattr(item, "stage", "")
        or ""
    )
    return OutcomeRecord(
        repo=repo,
        ok=ok,
        error=str(error),
        countable=countable,
        detail={
            "status": getattr(getattr(item, "status", ""), "value", ""),
            "merge_state": getattr(item, "merge_state", ""),
        },
    )