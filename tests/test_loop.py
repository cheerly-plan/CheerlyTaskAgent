"""执行-反馈循环：失败反馈注入、轮次上限、不可修复状态与取消。"""

from __future__ import annotations

from taskagent.loop import build_feedback, repairable, run_rounds
from taskagent.models import RunResult, WorkItem


def _item() -> WorkItem:
    item = WorkItem.create(
        title="修复登录", description="点登录 500", kind="bug", source="manual"
    )
    item.exec_mode = "apply"
    item.hold_merge = True
    return item


def _result(ok: bool = True, usage: dict | None = None) -> RunResult:
    return RunResult(
        ok=ok, dry_run=False, command="fake -p",
        changed_files=["src/a.py"] if ok else [],
        worktree="/tmp/worktree",
        error=None if ok else "boom",
        usage=usage or {},
    )


class FakeBackend:
    def __init__(self, results: list[RunResult]) -> None:
        self.results = list(results)
        self.calls = 0
        self.feedback_seen: list[str] = []

    async def run(self, item, *, progress, should_cancel):
        self.calls += 1
        self.feedback_seen.append(item.feedback)
        index = min(self.calls - 1, len(self.results) - 1)
        return self.results[index]


class FakeGate:
    def __init__(self, verdicts: list[dict]) -> None:
        self.verdicts = list(verdicts)
        self.calls = 0

    async def __call__(self, item, result):
        self.calls += 1
        return self.verdicts[min(self.calls - 1, len(self.verdicts) - 1)]


FAILED_GATE = {
    "ok": False,
    "state": "failed",
    "reason": "pytest 未通过（退出码 1）",
    "checks": [
        {
            "id": "test", "label": "pytest", "ok": False, "exit_code": 1,
            "tail": "E   AssertionError: 500 != 200",
        }
    ],
    "review": {
        "state": "done", "verdict": "block",
        "issues": [{"severity": "high", "message": "没有处理空 token", "file": "src/a.py"}],
        "risks": ["security"],
    },
}

PASSED_GATE = {"ok": True, "state": "passed", "reason": "全部验证通过", "checks": []}


async def test_first_round_pass_runs_once() -> None:
    backend = FakeBackend([_result()])
    gate = FakeGate([PASSED_GATE])
    item = _item()

    outcome = await run_rounds(item=item, backend=backend, gate=gate, max_rounds=3)

    assert outcome.rounds == 1
    assert outcome.gate["ok"] is True
    assert backend.calls == 1
    assert item.feedback == ""
    assert outcome.result.rounds == 1


async def test_failure_is_fed_back_and_second_round_can_pass() -> None:
    backend = FakeBackend([_result(), _result()])
    gate = FakeGate([FAILED_GATE, PASSED_GATE])
    item = _item()
    stages: list[str] = []

    outcome = await run_rounds(
        item=item, backend=backend, gate=gate, max_rounds=2, stage=stages.append
    )

    assert backend.calls == 2
    assert outcome.rounds == 2
    assert outcome.gate["ok"] is True
    # 第二轮提示词里带着第一轮的具体失败信息。
    assert "pytest 未通过" in backend.feedback_seen[1]
    assert "AssertionError" in backend.feedback_seen[1]
    assert "没有处理空 token" in backend.feedback_seen[1]
    assert any("第 2/2 轮" in text for text in stages)


async def test_runs_are_bounded_by_max_rounds() -> None:
    backend = FakeBackend([_result(), _result(), _result()])
    gate = FakeGate([FAILED_GATE])
    item = _item()

    outcome = await run_rounds(item=item, backend=backend, gate=gate, max_rounds=2)

    assert backend.calls == 2
    assert outcome.rounds == 2
    assert outcome.gate["ok"] is False
    assert outcome.result.ok is True  # 执行本身没失败，只是没通过门禁


async def test_unrepairable_gate_stops_after_first_round() -> None:
    backend = FakeBackend([_result()])
    gate = FakeGate([{
        "ok": False, "state": "unconfigured",
        "reason": "仓库未配置验证命令", "checks": [],
    }])
    item = _item()

    outcome = await run_rounds(item=item, backend=backend, gate=gate, max_rounds=3)

    assert backend.calls == 1
    assert outcome.rounds == 1
    assert item.feedback == ""
    assert repairable(outcome.gate) is False


async def test_infra_failure_skips_gate() -> None:
    backend = FakeBackend([_result(ok=False)])
    gate = FakeGate([PASSED_GATE])
    item = _item()

    outcome = await run_rounds(item=item, backend=backend, gate=gate, max_rounds=3)

    assert backend.calls == 1
    assert gate.calls == 0
    assert outcome.gate == {}
    assert outcome.result.ok is False


async def test_cancel_stops_between_rounds() -> None:
    backend = FakeBackend([_result(), _result()])
    gate = FakeGate([FAILED_GATE, PASSED_GATE])
    item = _item()

    def should_cancel() -> bool:
        return backend.calls >= 1

    outcome = await run_rounds(
        item=item,
        backend=backend,
        gate=gate,
        max_rounds=3,
        should_cancel=should_cancel,
    )

    assert outcome.cancelled is True
    assert backend.calls == 1


def test_build_feedback_includes_hints_checks_and_caps_length() -> None:
    feedback = build_feedback(FAILED_GATE, round_no=1, max_rounds=2)
    assert "第 1/2 轮" in feedback
    assert "pytest 未通过" in feedback
    assert "AssertionError" in feedback
    assert "没有处理空 token" in feedback
    assert "评审结论：block" in feedback

    test_change = dict(FAILED_GATE, state="test_files_changed", reason="改了测试")
    assert "不要修改或删除已有测试" in build_feedback(test_change, round_no=1, max_rounds=2)

    huge = dict(
        FAILED_GATE,
        reason="x" * 5_000,
        checks=[{"label": "t", "ok": False, "tail": "y" * 5_000}],
    )
    assert len(build_feedback(huge, round_no=1, max_rounds=2)) <= 4_000

async def test_budget_exhaustion_stops_before_second_round() -> None:
    backend = FakeBackend([
        _result(usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "calls": 1}),
        _result(),
    ])
    gate = FakeGate([FAILED_GATE, PASSED_GATE])
    item = _item()
    recorded: list[tuple[str, int]] = []
    checks = 0

    def budget_reason() -> str:
        nonlocal checks
        checks += 1
        return "今日 token 预算已用完（100/100）"

    outcome = await run_rounds(
        item=item,
        backend=backend,
        gate=gate,
        max_rounds=3,
        record_usage=lambda kind, usage: recorded.append((kind, usage.total_tokens)),
        budget_reason=budget_reason,
    )

    assert backend.calls == 1
    assert outcome.rounds == 1
    assert outcome.budget_reason
    assert checks == 1
    assert recorded == [("cli", 15)]
    # 预算耗尽时不会进入第二轮，也不会假装通过。
    assert outcome.gate["ok"] is False
