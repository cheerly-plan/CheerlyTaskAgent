"""自动执行准入 / 熔断 / dry-run 语义（P0）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from taskagent.admission import (
    AutomationPolicy,
    CircuitBreaker,
    admit,
    normalise_automation,
    outcome_of,
)
from taskagent.automation import AutomationEngine
from taskagent.events import EventBus
from taskagent.models import ItemStatus, LocalRepo, RunResult, WorkItem
from taskagent.store import TaskStore


class FakeSettings:
    def __init__(self, automation: dict | None = None) -> None:
        self.automation = automation or {"failure_threshold": 2, "cooldown_minutes": 10}

    def load_ui_settings(self) -> dict:
        return {"automation": self.automation, "models": [], "agent_model": ""}


class FakeScheduler:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.projects = None
        self.auto_outcome_hook = None

    def run_auto(self, item_id: str, *, plan: str = "", exec_mode: str = "apply", manual: bool = False) -> None:
        self.calls.append({"id": item_id, "plan": plan, "exec_mode": exec_mode})

    def record_usage(self, item, kind, usage):  # pragma: no cover - planner path only
        return 0


def _item(tmp_path: Path, *, kind: str = "bug", repo: str = "repo-a") -> WorkItem:
    item = WorkItem.create(title="修登录", description="x", kind=kind, source="tapd")
    item.repo = LocalRepo(path=str(tmp_path / repo), origin="", name=repo)
    item.status = ItemStatus.READY
    return item


# ── 配置归一化 ────────────────────────────────────────────────────────────────
def test_normalise_automation_clamps_and_filters():
    cfg = normalise_automation({
        "allowed_kinds": ["BUG", "nope", "feature", "bug"],
        "repo_allowlist": [" a ", "", "a", "b"],
        "failure_threshold": 0,
        "cooldown_minutes": 99999,
        "max_recoveries": -5,
    })
    assert cfg["allowed_kinds"] == ["bug", "feature"]
    assert cfg["repo_allowlist"] == ["a", "b"]
    assert cfg["failure_threshold"] == 1
    assert cfg["cooldown_minutes"] == 24 * 60
    assert cfg["max_recoveries"] == 0

    # 手写 settings.json 的逗号字符串也认，避免静默放开全部。
    loose = normalise_automation({"allowed_kinds": "bug, task", "repo_allowlist": "a, b"})
    assert loose["allowed_kinds"] == ["bug", "task"]
    assert loose["repo_allowlist"] == ["a", "b"]


def test_policy_empty_lists_mean_unrestricted():
    policy = AutomationPolicy.from_settings({})
    assert policy.allows_kind("feature") and policy.allows_repo("anything")
    restricted = AutomationPolicy.from_settings({"allowed_kinds": ["bug"], "repo_allowlist": ["repo-a"]})
    assert restricted.allows_kind("bug")
    assert not restricted.allows_kind("feature")
    assert restricted.allows_repo("repo-a")
    assert not restricted.allows_repo("repo-b")


# ── 熔断 ─────────────────────────────────────────────────────────────────────
def test_circuit_opens_after_threshold_and_persists(tmp_path: Path):
    path = tmp_path / "circuit.json"
    circuit = CircuitBreaker(path)
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    assert circuit.record_failure("repo-a", error="boom", threshold=2, cooldown_minutes=10,
                                  now=moment)["open"] is False
    second = circuit.record_failure("repo-a", error="boom2", threshold=2, cooldown_minutes=10,
                                    now=moment)
    assert second["open"] is True
    assert second["failures"] == 2
    assert circuit.check("repo-a", now=moment)["open"] is True

    reloaded = CircuitBreaker(path)
    assert reloaded.check("repo-a", now=moment)["open"] is True

    # 冷却到期：自动清零，允许一次新的试探。
    later = moment + timedelta(minutes=11)
    assert circuit.check("repo-a", now=later)["open"] is False
    assert circuit.check("repo-a", now=later)["failures"] == 0


def test_circuit_success_and_reset_clear_state(tmp_path: Path):
    circuit = CircuitBreaker(tmp_path / "circuit.json")
    circuit.record_failure("repo-a", threshold=1, cooldown_minutes=10)
    assert circuit.check("repo-a")["open"] is True
    circuit.reset("repo-a")
    assert circuit.check("repo-a")["open"] is False

    circuit.record_failure("repo-b", threshold=1, cooldown_minutes=10)
    circuit.record_success("repo-b")
    assert circuit.check("repo-b")["open"] is False
    assert circuit.snapshot() == {}


# ── 准入决策 ─────────────────────────────────────────────────────────────────
def test_admit_denies_kind_repo_and_open_circuit(tmp_path: Path):
    item = _item(tmp_path, kind="feature", repo="repo-a")
    policy = AutomationPolicy.from_settings({"allowed_kinds": ["bug"]})
    decision = admit(item, policy)
    assert not decision.ok and decision.code == "kind_not_allowed"

    policy = AutomationPolicy.from_settings({"repo_allowlist": ["repo-b"]})
    decision = admit(item, policy)
    assert not decision.ok and decision.code == "repo_not_allowed"

    circuit = CircuitBreaker(tmp_path / "c.json")
    circuit.record_failure("repo-a", threshold=1, cooldown_minutes=10)
    decision = admit(item, AutomationPolicy(), circuit)
    assert not decision.ok and decision.code == "circuit_open"


# ── dispatch：准入、dry-run 语义、人工绕过 ────────────────────────────────────
async def test_dispatch_skips_disallowed_kind(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks")
    scheduler = FakeScheduler()
    engine = AutomationEngine(
        scheduler=scheduler, store=store, events=EventBus(),
        settings=FakeSettings({"allowed_kinds": ["bug"]}),
    )
    item = _item(tmp_path, kind="feature")
    store.save(item)

    result = await engine.dispatch([item.id], trigger="tapd-poll", exec_mode="apply")
    assert result["started"] == []
    assert result["skipped"][0]["code"] == "kind_not_allowed"
    assert scheduler.calls == []


async def test_dispatch_honours_dry_run_mode(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks")
    scheduler = FakeScheduler()
    engine = AutomationEngine(scheduler=scheduler, store=store, events=EventBus())
    item = _item(tmp_path)
    store.save(item)

    result = await engine.dispatch([item.id], trigger="tapd-poll", exec_mode="dry-run")
    assert result["started"] == [item.id]
    assert scheduler.calls[0]["exec_mode"] == "dry-run"


async def test_manual_dispatch_bypasses_admission(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks")
    scheduler = FakeScheduler()
    engine = AutomationEngine(
        scheduler=scheduler, store=store, events=EventBus(),
        settings=FakeSettings({"allowed_kinds": ["bug"], "repo_allowlist": ["other"]}),
    )
    item = _item(tmp_path, kind="feature", repo="repo-a")
    store.save(item)

    result = await engine.dispatch(
        [item.id], trigger="manual", exec_mode="apply", force=True
    )
    assert result["started"] == [item.id]
    assert scheduler.calls[0]["exec_mode"] == "apply"


async def test_dispatch_circuit_blocks_automatic_but_not_manual(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks")
    scheduler = FakeScheduler()
    circuit = CircuitBreaker(tmp_path / "circuit.json")
    circuit.record_failure("repo-a", threshold=1, cooldown_minutes=30)
    engine = AutomationEngine(
        scheduler=scheduler, store=store, events=EventBus(), circuit=circuit,
        settings=FakeSettings(),
    )
    item = _item(tmp_path, repo="repo-a")
    store.save(item)

    auto = await engine.dispatch([item.id], trigger="tapd-poll", exec_mode="apply")
    assert auto["started"] == []
    assert auto["skipped"][0]["code"] == "circuit_open"

    manual = await engine.dispatch(
        [item.id], trigger="manual", exec_mode="apply", force=True
    )
    assert manual["started"] == [item.id]


# ── 成败记账 ─────────────────────────────────────────────────────────────────
def test_record_outcome_opens_and_closes_circuit(tmp_path: Path):
    circuit = CircuitBreaker(tmp_path / "circuit.json")
    engine = AutomationEngine(
        scheduler=FakeScheduler(), store=TaskStore(tmp_path / "t"), events=EventBus(),
        settings=FakeSettings({"failure_threshold": 1}), circuit=circuit,
    )
    item = _item(tmp_path)
    item.auto = True
    item.approved_by = "auto"
    item.status = ItemStatus.FAILED
    item.result = RunResult(ok=False, dry_run=False, command="x", error="检查失败")
    engine.record_outcome(item)
    assert circuit.check("repo-a")["open"] is True

    item.status = ItemStatus.SUCCEEDED
    item.result = RunResult(ok=True, dry_run=False, command="x")
    item.merge_state = "merged"
    engine.record_outcome(item)
    assert circuit.check("repo-a")["open"] is False


def test_dry_run_outcome_does_not_touch_circuit(tmp_path: Path):
    circuit = CircuitBreaker(tmp_path / "circuit.json")
    engine = AutomationEngine(
        scheduler=FakeScheduler(), store=TaskStore(tmp_path / "t"), events=EventBus(),
        settings=FakeSettings({"failure_threshold": 1}), circuit=circuit,
    )
    item = _item(tmp_path)
    item.auto = True
    item.approved_by = "auto"
    item.status = ItemStatus.SUCCEEDED
    item.result = RunResult(ok=True, dry_run=True, command="x")
    engine.record_outcome(item)
    assert circuit.snapshot() == {}
    assert outcome_of(item).dry_run is True


def test_outcome_of_merge_blocked_is_failure(tmp_path: Path):
    item = _item(tmp_path)
    item.status = ItemStatus.SUCCEEDED
    item.merge_state = "blocked"
    item.result = RunResult(ok=True, dry_run=False, command="x")
    assert outcome_of(item).ok is False

def test_manual_authorized_run_does_not_touch_circuit(tmp_path: Path):
    circuit = CircuitBreaker(tmp_path / "circuit.json")
    engine = AutomationEngine(
        scheduler=FakeScheduler(), store=TaskStore(tmp_path / "t"), events=EventBus(),
        settings=FakeSettings({"failure_threshold": 1}), circuit=circuit,
    )
    item = _item(tmp_path)
    item.auto = True
    item.approved_by = "user"
    item.status = ItemStatus.FAILED
    item.result = RunResult(ok=False, dry_run=False, command="x", error="验证失败")
    engine.record_outcome(item)
    assert circuit.check("repo-a")["open"] is False


def test_environment_failure_does_not_open_circuit(tmp_path: Path):
    circuit = CircuitBreaker(tmp_path / "circuit.json")
    engine = AutomationEngine(
        scheduler=FakeScheduler(), store=TaskStore(tmp_path / "t"), events=EventBus(),
        settings=FakeSettings({"failure_threshold": 1}), circuit=circuit,
    )
    item = _item(tmp_path)
    item.auto = True
    item.approved_by = "auto"
    item.status = ItemStatus.FAILED
    item.result = RunResult(
        ok=False, dry_run=False, command="x", error="CLI not found",
        failure_scope="environment",
    )
    engine.record_outcome(item)
    assert circuit.check("repo-a")["open"] is False
    assert outcome_of(item).countable is False
