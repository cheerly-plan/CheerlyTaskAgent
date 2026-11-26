"""调度级重试（L3）的反馈注入：失败后带着教训重跑。

覆盖两类调度级重试：① CLI 执行自身失败（_build_retry_feedback）；② 验证/评审
未通过（_build_verify_retry_feedback）。验证点均为失败原因经结构化处理后透传
进下一轮的 ``item.feedback``，进而由 build_prompt 注入提示词。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from taskagent.events import EventBus
from taskagent.executor import ExecutorRegistry
from taskagent.gitmap import GitProjectRegistry
from taskagent.models import ItemStatus, LocalRepo, RunResult, WorkItem
from taskagent.scheduler import (
    Scheduler,
    SchedulerSettings,
    _build_retry_feedback,
    _build_verify_retry_feedback,
    _is_retryable,
)
from taskagent.store import TaskStore


def _scheduler(tmp_path: Path, **overrides) -> tuple[Scheduler, TaskStore]:
    settings = SchedulerSettings(reports_dir=tmp_path / "reports", retry_delays=(0.0,))
    for key, value in overrides.items():
        setattr(settings, key, value)
    scheduler = Scheduler(
        store=TaskStore(tmp_path / "tasks"),
        gitmap=GitProjectRegistry([]),
        executors=ExecutorRegistry(),
        events=EventBus(),
        settings=settings,
    )
    return scheduler, scheduler.store


def _item(tmp_path: Path, **fields) -> WorkItem:
    item = WorkItem.create(title="重试反馈测试", description="x", kind="bug", source="tapd")
    item.repo = LocalRepo(path=str(tmp_path / "repo"), origin="", name="repo")
    item.status = ItemStatus.QUEUED
    item.hold_merge = False
    item.exec_mode = "dry-run"
    for key, value in fields.items():
        setattr(item, key, value)
    return item


# ── 反馈文本生成 ─────────────────────────────────────────────────────────────
def test_build_retry_feedback_timeout():
    feedback = _build_retry_feedback("CodeBuddy CLI timed out after 600s", attempt=1, max_attempts=2)
    assert "第 1/2 次执行" in feedback
    assert "超时" in feedback
    assert "600s" in feedback
    assert "缩小改动范围" in feedback
    assert "先确认现状再动手" in feedback


def test_build_retry_feedback_rate_limit():
    feedback = _build_retry_feedback("rate limit exceeded 429", attempt=2, max_attempts=3)
    assert "第 2/3 次执行" in feedback
    assert "限流" in feedback
    assert "429" in feedback


def test_build_retry_feedback_server_error():
    feedback = _build_retry_feedback("upstream returned 503", attempt=1, max_attempts=2)
    assert "502/503" in feedback
    assert "不要推倒重来" in feedback


def test_build_retry_feedback_is_actionable_not_raw_dump():
    """反馈应是行动指引而非仅回显原始错误；原始错误只作证据附带。"""
    feedback = _build_retry_feedback("boom timeout", attempt=1, max_attempts=2)
    assert len(feedback) > len("boom timeout")
    assert "原始错误：boom timeout" in feedback


def test_is_retryable_scope_unchanged():
    """L3 触发条件不在本次改动范围，保持原有关键词语义。"""
    assert _is_retryable("CLI timed out after 600s")
    assert _is_retryable("rate limit 429")
    assert _is_retryable("502 bad gateway")
    assert not _is_retryable("exit code 1")
    assert not _is_retryable(None)


# ── 端到端：重试把反馈带进下一轮 ─────────────────────────────────────────────
class _ScriptedBackend:
    label = "Scripted"
    backend_id = "codebuddy"

    def __init__(self, *results: RunResult) -> None:
        self._results = list(results)
        self.calls = 0
        self.feedbacks: list[str] = []

    async def run(self, item: WorkItem, *, progress, should_cancel) -> RunResult:
        self.calls += 1
        self.feedbacks.append(item.feedback)
        return self._results.pop(0)


async def test_l3_retry_carries_feedback_into_next_run(tmp_path, monkeypatch):
    scheduler, store = _scheduler(tmp_path)
    item = _item(tmp_path)
    store.save(item)

    backend = _ScriptedBackend(
        RunResult(ok=False, dry_run=False, command="fake", error="CLI timed out after 600s"),
        RunResult(ok=True, dry_run=True, command="fake"),
    )
    monkeypatch.setattr(scheduler.executors, "resolve", lambda _id: backend)
    monkeypatch.setattr("taskagent.scheduler.write_report", lambda *a, **k: "")

    # 重试通过 spawn 新 task 实现；捕获它以便在测试内等待完成。
    spawned: list[asyncio.Task] = []
    real_spawn = scheduler._spawn

    def capture(coro, *, name):
        task = real_spawn(coro, name=name)
        spawned.append(task)
        return task

    monkeypatch.setattr(scheduler, "_spawn", capture)

    await scheduler._execute(item.id)
    if spawned:
        await asyncio.gather(*spawned)

    assert backend.calls == 2, backend.feedbacks
    # 第 1 轮无历史反馈；第 2 轮带着超时诊断。
    assert backend.feedbacks[0] == ""
    assert "第 1/2 次执行" in backend.feedbacks[1]
    assert "超时" in backend.feedbacks[1]
    assert "600s" in backend.feedbacks[1]
    saved = store.get(item.id)
    assert saved is not None and saved.attempt == 2


async def test_normal_start_has_no_stale_feedback(tmp_path, monkeypatch):
    """非重试路径仍清空 feedback，严格兼容原有语义。"""
    scheduler, store = _scheduler(tmp_path)
    item = _item(tmp_path)
    item.feedback = "上一轮遗留的门禁反馈"
    store.save(item)

    backend = _ScriptedBackend(RunResult(ok=True, dry_run=True, command="fake"))
    monkeypatch.setattr(scheduler.executors, "resolve", lambda _id: backend)
    monkeypatch.setattr("taskagent.scheduler.write_report", lambda *a, **k: "")

    await scheduler._execute(item.id)

    assert backend.calls == 1
    assert backend.feedbacks[0] == ""


# ── 验证失败门禁用例 ─────────────────────────────────────────────────────────
def _fail_gate(state: str = "failed", reason: str = "pytest 失败") -> dict:
    """构造一个失败的门禁 verdict，state=failed 属于 agent 可修状态。"""
    return {
        "ok": False,
        "state": state,
        "reason": reason,
        "checks": [
            {
                "id": "pytest",
                "label": "pytest",
                "ok": False,
                "exit_code": 1,
                "diagnostics": ["src/app.py:10: AssertionError: 期望 2 得到 1"],
            }
        ],
    }


def test_build_verify_retry_feedback_uses_structured_diagnostics():
    """验证反馈复用 check diagnostics 与评审意见，而非整段原始输出。"""
    feedback = _build_verify_retry_feedback(_fail_gate(), attempt=1, max_attempts=2)
    assert "第 1/2 轮执行" in feedback
    assert "pytest 失败" in feedback
    assert "src/app.py:10: AssertionError" in feedback
    assert "不要推倒重来" in feedback
    assert "上一轮没有通过验证" in feedback or "上一轮" in feedback


def test_build_verify_retry_feedback_includes_review():
    gate = _fail_gate(state="review_blocked", reason="评审未通过")
    gate["review"] = {
        "state": "done",
        "verdict": "block",
        "reason": "缺少核心函数",
        "issues": [{"severity": "high", "file": "app.py", "message": "缺少 solve()"}],
    }
    feedback = _build_verify_retry_feedback(gate, attempt=1, max_attempts=2)
    assert "评审结论：block" in feedback
    assert "缺少 solve()" in feedback


async def test_verify_failure_auto_retries_with_diagnostics(tmp_path, monkeypatch):
    """自动 apply 门禁失败 → 有限次重试，结构化诊断进入下一轮 feedback。"""
    scheduler, store = _scheduler(tmp_path, max_rounds=1)
    item = _item(
        tmp_path, auto=True, exec_mode="apply", hold_merge=True, max_attempts=2
    )
    store.save(item)

    backend = _ScriptedBackend(
        RunResult(ok=True, dry_run=False, command="fake"),
        RunResult(ok=True, dry_run=False, command="fake"),
    )
    monkeypatch.setattr(scheduler.executors, "resolve", lambda _id: backend)
    monkeypatch.setattr("taskagent.scheduler.write_report", lambda *a, **k: "")

    async def fake_verify(_item, _result):
        return _fail_gate()

    monkeypatch.setattr(scheduler, "_verify", fake_verify)

    spawned: list[asyncio.Task] = []
    real_spawn = scheduler._spawn

    def capture(coro, *, name):
        task = real_spawn(coro, name=name)
        spawned.append(task)
        return task

    monkeypatch.setattr(scheduler, "_spawn", capture)

    await scheduler._execute(item.id)
    if spawned:
        await asyncio.gather(*spawned)

    assert backend.calls == 2, backend.feedbacks
    # 第 1 轮无历史反馈；重试轮带着门禁诊断。
    assert backend.feedbacks[0] == ""
    assert "pytest 失败" in backend.feedbacks[1]
    assert "src/app.py:10: AssertionError" in backend.feedbacks[1]
    saved = store.get(item.id)
    assert saved is not None and saved.attempt == 2
    assert saved.status == ItemStatus.FAILED  # 次数用尽后转 failed


async def test_verify_failure_non_repairable_no_retry(tmp_path, monkeypatch):
    """不可修状态（needs_review）不重试，直接 failed。"""
    scheduler, store = _scheduler(tmp_path, max_rounds=1)
    item = _item(
        tmp_path, auto=True, exec_mode="apply", hold_merge=True, max_attempts=2
    )
    store.save(item)

    backend = _ScriptedBackend(RunResult(ok=True, dry_run=False, command="fake"))
    monkeypatch.setattr(scheduler.executors, "resolve", lambda _id: backend)
    monkeypatch.setattr("taskagent.scheduler.write_report", lambda *a, **k: "")

    async def fake_verify(_item, _result):
        return _fail_gate(state="needs_review", reason="验收条目需人工复核")

    monkeypatch.setattr(scheduler, "_verify", fake_verify)

    await scheduler._execute(item.id)

    assert backend.calls == 1  # 没有重试
    saved = store.get(item.id)
    assert saved is not None and saved.status == ItemStatus.FAILED
    assert "验证未通过" in (saved.error or "")


async def test_verify_failure_manual_no_retry(tmp_path, monkeypatch):
    """人工 apply 保持「点一次跑一次」：门禁失败不自动重试。"""
    scheduler, store = _scheduler(tmp_path, max_rounds=1)
    item = _item(
        tmp_path, auto=False, exec_mode="apply", hold_merge=True, max_attempts=2
    )
    store.save(item)

    backend = _ScriptedBackend(RunResult(ok=True, dry_run=False, command="fake"))
    monkeypatch.setattr(scheduler.executors, "resolve", lambda _id: backend)
    monkeypatch.setattr("taskagent.scheduler.write_report", lambda *a, **k: "")

    async def fake_verify(_item, _result):
        return _fail_gate()

    monkeypatch.setattr(scheduler, "_verify", fake_verify)

    await scheduler._execute(item.id)

    assert backend.calls == 1
    saved = store.get(item.id)
    assert saved is not None and saved.status == ItemStatus.FAILED
