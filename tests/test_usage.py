"""Token 账本与预算：CLI/LLM 用量解析、按天累计、任务/日预算判定。"""

from __future__ import annotations

import json
from pathlib import Path

from taskagent.events import EventBus
from taskagent.executor import ExecutorRegistry
from taskagent.gitmap import GitProjectRegistry
from taskagent.models import WorkItem
from taskagent.scheduler import Scheduler, SchedulerSettings
from taskagent.store import TaskStore
from taskagent.usage import (
    TokenUsage,
    UsageLedger,
    extract_cli_usage,
    extract_llm_usage,
)


def test_extract_cli_usage_snake_case() -> None:
    stdout = json.dumps({
        "type": "result", "result": "done",
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    })
    usage = extract_cli_usage(stdout)
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (10, 5, 15)
    assert usage.calls == 1


def test_extract_cli_usage_camel_case_nested_provider_data() -> None:
    stdout = json.dumps([
        {"type": "message", "role": "user"},
        {
            "type": "reasoning",
            "providerData": {"usage": {"inputTokens": 30010, "outputTokens": 47}},
        },
    ])
    usage = extract_cli_usage(stdout)
    assert usage.input_tokens == 30010
    assert usage.output_tokens == 47


def test_extract_cli_usage_picks_largest_block() -> None:
    stdout = (
        '{"usage": {"input_tokens": 2, "output_tokens": 1}}\n'
        '{"usage": {"input_tokens": 20, "output_tokens": 10}}\n'
    )
    assert extract_cli_usage(stdout).total_tokens == 30


def test_extract_cli_usage_total_only_and_empty() -> None:
    assert extract_cli_usage('{"usage": {"total_tokens": 42}}').total_tokens == 42
    assert extract_cli_usage("no usage here").total_tokens == 0
    assert extract_cli_usage("").calls == 0


def test_extract_llm_usage() -> None:
    payload = {"usage": {"prompt_tokens": 120, "completion_tokens": 30}}
    usage = extract_llm_usage(payload)
    assert (usage.input_tokens, usage.output_tokens, usage.calls) == (120, 30, 1)
    assert extract_llm_usage({"choices": []}).total_tokens == 0
    assert extract_llm_usage(None).calls == 0


def test_token_usage_add_and_dict() -> None:
    total = TokenUsage(10, 5, 1).add(TokenUsage(2, 3, 1))
    assert total.total_tokens == 20
    assert total.calls == 2
    assert TokenUsage.from_dict(total.to_dict()) == total
    assert TokenUsage.from_dict(None) == TokenUsage()
    assert bool(TokenUsage()) is False


def test_ledger_accumulates_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    ledger = UsageLedger(path)

    assert ledger.record("work-1", TokenUsage(10, 5, 1)) == 15
    assert ledger.record("work-2", TokenUsage(3, 2, 1)) == 20
    assert ledger.total_today == 20
    assert ledger.snapshot()["tasks"] == {"work-1": 15, "work-2": 5}

    reloaded = UsageLedger(path)
    assert reloaded.total_today == 20


def test_ledger_rolls_over_on_a_new_day(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    path.write_text(
        json.dumps({"date": "2000-01-01", "total_tokens": 999, "tasks": {"x": 999}}),
        encoding="utf-8",
    )
    ledger = UsageLedger(path)
    assert ledger.total_today == 0
    assert ledger.snapshot()["tasks"] == {}


def _scheduler(tmp_path: Path, **settings) -> Scheduler:
    return Scheduler(
        store=TaskStore(tmp_path / "tasks"),
        gitmap=GitProjectRegistry([]),
        executors=ExecutorRegistry(),
        events=EventBus(),
        settings=SchedulerSettings(reports_dir=tmp_path / "reports", **settings),
    )


def _item() -> WorkItem:
    return WorkItem.create(title="预算测试", description="x", kind="bug")


def test_budget_reason_task_and_daily_limits(tmp_path: Path) -> None:
    scheduler = _scheduler(tmp_path, task_token_budget=100, daily_token_budget=150)
    item = _item()

    assert scheduler._budget_reason(item) == ""
    scheduler.record_usage(item, "cli", TokenUsage(60, 40, 1))  # 100
    assert "任务 token 预算" in scheduler._budget_reason(item)

    # 任务预算放大后，日预算仍然生效。
    scheduler.settings.task_token_budget = 10_000
    scheduler.record_usage(_item(), "llm", TokenUsage(40, 20, 1))  # today 160
    assert "今日 token 预算" in scheduler._budget_reason(item)


def test_record_usage_aggregates_by_source(tmp_path: Path) -> None:
    scheduler = _scheduler(tmp_path)
    item = _item()
    scheduler.record_usage(item, "cli", TokenUsage(10, 5, 1))
    scheduler.record_usage(item, "llm", TokenUsage(7, 3, 1))

    assert item.usage["cli"]["total_tokens"] == 15
    assert item.usage["llm"]["total_tokens"] == 10
    assert item.usage["total_tokens"] == 25
    assert item.usage["calls"] == 2
    assert scheduler.ledger.total_today == 25