"""统一失败分类 + attempt trace 持久化的回归测试。

- 分类规则集中到 failure.py，三处消费点（重试/熔断/记忆）委托后判定等价；
- 每次执行尝试写入结构化记录，跨重新执行保留，序列化兼容旧数据。
"""

from __future__ import annotations

from taskagent.failure import (
    FailureKind,
    classify_exec_error,
    gate_repairable,
    is_countable_scope,
    is_retryable,
    scope_for,
)
from taskagent.loop import repairable
from taskagent.models import RunResult, WorkItem
from taskagent.scheduler import _is_retryable


# ── 统一分类：枚举与判定 ─────────────────────────────────────────────────────
def test_classify_exec_error_kinds():
    assert classify_exec_error("CLI timed out after 600s") is FailureKind.TRANSIENT
    assert classify_exec_error("rate limit 429") is FailureKind.TRANSIENT
    assert classify_exec_error("please use /login") is FailureKind.AUTH
    assert classify_exec_error("invalid api key") is FailureKind.AUTH
    assert classify_exec_error("exit code 1") is FailureKind.REPO
    assert classify_exec_error(None) is FailureKind.NONE


def test_is_retryable_equiv_old_rule():
    """统一后的重试判定与原关键词表逐字等价。"""
    for error in ("timeout", "timed out", "rate", "429", "503", "502", "x TIMEOUT y"):
        assert is_retryable(error) is True, error
    for error in ("exit code 1", "authentication required", "500 internal", None, ""):
        assert is_retryable(error) is False, error
    # scheduler 兼容入口仍返回同样结果。
    assert _is_retryable("rate limit") == is_retryable("rate limit")


def test_is_countable_scope_equiv_old_rule():
    for scope in ("", "repo", None):
        assert is_countable_scope(scope) is True
    for scope in ("environment", "budget", "unknown"):
        assert is_countable_scope(scope) is False


def test_scope_for_keeps_legacy_values():
    assert scope_for(FailureKind.ENVIRONMENT) == "environment"
    assert scope_for(FailureKind.BUDGET) == "budget"
    assert scope_for(FailureKind.REPO) == ""


def test_gate_repairable_equiv_loop():
    repairable_states = (
        "failed", "review_blocked", "test_files_changed", "protected_files_changed",
        "too_many_files", "diff_too_large", "no_changes", "assertion_failed",
    )
    for state in repairable_states:
        gate = {"state": state}
        assert gate_repairable(gate) is True
        assert repairable(gate) is True  # loop 兼容入口
    for state in ("needs_review", "unconfigured", "review_needs_human", "passed"):
        assert gate_repairable({"state": state}) is False
    # assertion_state 与 state 都参与判定（不改旧字段语义）。
    assert gate_repairable({"state": "", "assertion_state": "assertion_failed"}) is True


# ── attempt trace 序列化兼容 ─────────────────────────────────────────────────
def test_attempts_default_empty():
    item = WorkItem.create(title="t", description="d", kind="bug", source="manual")
    assert item.attempts == []


def test_attempts_roundtrip():
    item = WorkItem.create(title="t", description="d", kind="bug", source="manual")
    item.attempts.append({
        "seq": 1, "attempt": 1, "at": "2026-01-01T00:00:00+00:00",
        "outcome": "retry_scheduled", "error": "timeout", "kind": "transient",
        "rounds": 1, "gate_state": "failed", "feedback": "缩小范围",
    })
    data = item.to_dict()
    assert data["attempts"][0]["kind"] == "transient"
    loaded = WorkItem.from_dict(data)
    assert loaded.attempts == item.attempts


def test_attempts_backward_compat_missing_key():
    """旧数据没有 attempts 键 → 默认空列表，不报错。"""
    item = WorkItem.create(title="t", description="d", kind="bug", source="manual")
    data = item.to_dict()
    data.pop("attempts")
    loaded = WorkItem.from_dict(data)
    assert loaded.attempts == []


def test_attempts_ignores_malformed_records():
    item = WorkItem.create(title="t", description="d", kind="bug", source="manual")
    data = item.to_dict()
    data["attempts"] = [{"seq": 1}, "bad", None]
    loaded = WorkItem.from_dict(data)
    assert len(loaded.attempts) == 1
    assert loaded.attempts[0]["seq"] == 1


# ── trace 由执行链路写入 ─────────────────────────────────────────────────────
def test_record_attempt_trace_writes_structured_record(tmp_path):
    from taskagent.events import EventBus
    from taskagent.executor import ExecutorRegistry
    from taskagent.gitmap import GitProjectRegistry
    from taskagent.models import ItemStatus, LocalRepo
    from taskagent.scheduler import Scheduler, SchedulerSettings
    from taskagent.store import TaskStore

    store = TaskStore(tmp_path / "tasks")
    scheduler = Scheduler(
        store=store,
        gitmap=GitProjectRegistry([]),
        executors=ExecutorRegistry(),
        events=EventBus(),
        settings=SchedulerSettings(reports_dir=tmp_path / "reports"),
    )
    item = WorkItem.create(title="trace", description="x", kind="bug", source="manual")
    item.repo = LocalRepo(path=str(tmp_path / "repo"), origin="", name="repo")
    item.status = ItemStatus.FAILED
    item.attempt = 1
    item.error = "CLI timed out after 600s"
    item.result = RunResult(
        ok=False, dry_run=False, command="fake", error="CLI timed out after 600s",
        rounds=1, failure_scope="environment",
    )
    store.save(item)

    scheduler._record_attempt_trace(item.id)

    saved = store.get(item.id)
    assert saved is not None and len(saved.attempts) == 1
    record = saved.attempts[0]
    assert record["seq"] == 1
    assert record["attempt"] == 1
    assert record["outcome"] == "failed"
    assert record["kind"] == "environment"  # failure_scope 优先标注
    assert "timed out" in record["error"]


def test_record_attempt_trace_bounded_to_12(tmp_path):
    from taskagent.events import EventBus
    from taskagent.executor import ExecutorRegistry
    from taskagent.gitmap import GitProjectRegistry
    from taskagent.models import ItemStatus, LocalRepo
    from taskagent.scheduler import Scheduler, SchedulerSettings
    from taskagent.store import TaskStore

    store = TaskStore(tmp_path / "tasks")
    scheduler = Scheduler(
        store=store,
        gitmap=GitProjectRegistry([]),
        executors=ExecutorRegistry(),
        events=EventBus(),
        settings=SchedulerSettings(reports_dir=tmp_path / "reports"),
    )
    item = WorkItem.create(title="trace", description="x", kind="bug", source="manual")
    item.repo = LocalRepo(path=str(tmp_path / "repo"), origin="", name="repo")
    item.status = ItemStatus.FAILED
    item.attempt = 1
    item.attempts = [{"seq": i, "attempt": i} for i in range(1, 20)]
    store.save(item)

    scheduler._record_attempt_trace(item.id)

    saved = store.get(item.id)
    assert saved is not None and len(saved.attempts) == 12
    assert saved.attempts[-1]["seq"] == 20
