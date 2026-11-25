"""把 spec 编译为可执行断言：确定性、门禁判定、unverified、隔离与报告。

全部离线：命令用例只用 python / sleep / echo，不访问网络、真实数据库或凭据。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from taskagent import cli
from taskagent.assertions import (
    DEFAULT_UNVERIFIED_POLICY,
    apply_assertion_gate,
    compile_spec,
    gate_exit_code,
    render_cases,
    run_assertion_gate,
)
from taskagent.reviewer import Review, apply_review
from taskagent.verification import run_gate


def _spec(acceptance: list[str], **extra: object) -> dict[str, object]:
    return {
        "goal": "让 spec 变成可执行断言",
        "acceptance": acceptance,
        "constraints": [],
        "risk": "medium",
        "source": "rule",
        **extra,
    }


# ── 1. 可复现生成 + 一一对应 + 字节级一致 ──────────────────────────────────────
def test_compile_spec_maps_acceptance_ids_and_is_byte_identical() -> None:
    spec = _spec([
        "AC-001: exists README.md",
        "AC-002: contains README.md taskagent",
        "AC-003: exit0 python -c 'print(1)'",
    ])
    first = compile_spec(spec)
    second = compile_spec(spec)
    assert len(first.cases) == 3
    assert first.mapping == {
        "AC-001": "case-AC-001",
        "AC-002": "case-AC-002",
        "AC-003": "case-AC-003",
    }
    assert first.unverified == ()
    assert render_cases(first).encode("utf-8") == render_cases(second).encode("utf-8")


def test_cli_gen_tests_is_reproducible(tmp_path: Path, monkeypatch, capsys) -> None:
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(_spec(["AC-1: exists README.md"]), ensure_ascii=False),
                         encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["taskagent", "gen-tests", "--spec", str(spec_path)])
    assert cli.main() == 0
    first = capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["taskagent", "gen-tests", "--spec", str(spec_path)])
    assert cli.main() == 0
    second = capsys.readouterr().out
    assert first.encode("utf-8") == second.encode("utf-8")
    assert "case-AC-1" in first


# ── 2. 门禁以用例结果为准；LLM 不能把 fail 覆盖为 pass ──────────────────────────
def test_gate_pass_and_fail_are_decided_by_case_results(tmp_path: Path) -> None:
    (tmp_path / "ok.txt").write_text("hello\n", encoding="utf-8")
    passing = run_assertion_gate(
        _spec(["AC-001: exists ok.txt", "AC-002: contains ok.txt hello"]),
        worktree=tmp_path,
    )
    assert passing["verdict"] == "pass"
    assert passing["ok"] is True
    assert passing["decision_source"] == "assertion"
    assert gate_exit_code(passing) == 0

    failing = run_assertion_gate(
        _spec(["AC-001: exists ok.txt", "AC-002: exists missing.txt"]),
        worktree=tmp_path,
    )
    assert failing["verdict"] == "fail"
    assert failing["ok"] is False
    assert gate_exit_code(failing) != 0


def test_llm_approve_cannot_override_assertion_failure(tmp_path: Path) -> None:
    gate = run_assertion_gate(_spec(["AC-001: exists missing.txt"]), worktree=tmp_path)
    assert gate["ok"] is False
    reviewed = apply_review(
        gate,
        Review(verdict="approve", reason="mock LLM says pass"),
        deciding=False,
    )
    assert reviewed["ok"] is False
    assert reviewed["verdict"] == "fail"
    assert reviewed["decision_source"] == "assertion"
    assert reviewed["review"]["verdict"] == "approve"
    assert reviewed["review_is_reference"] is True


def test_legacy_review_still_decides_when_not_in_assertion_mode() -> None:
    gate = {"ok": True, "state": "passed", "reason": "legacy", "checks": []}
    reviewed = apply_review(gate, Review(verdict="block", reason="bad"))
    assert reviewed["ok"] is False
    assert reviewed["state"] == "review_blocked"


# ── 3. unverified 显式上报 + 默认策略 + 可切换 ──────────────────────────────────
def test_unverified_items_are_reported_and_block_pass_by_default(tmp_path: Path) -> None:
    (tmp_path / "ok.txt").write_text("x", encoding="utf-8")
    spec = _spec([
        "AC-001: exists ok.txt",
        "AC-002: 需求描述含糊，无法判定真伪",
    ])
    gate = run_assertion_gate(spec, worktree=tmp_path)
    assert gate["verdict"] == "needs_review"
    assert gate["ok"] is False
    assert gate["decision_source"] == "unverified"
    assert gate["assertions"]["unverified"][0]["acceptance_id"] == "AC-002"
    assert gate["assertions"]["unverified"][0]["status"] == "unverified"
    assert gate["assertions"]["default_unverified_policy"] == DEFAULT_UNVERIFIED_POLICY
    assert DEFAULT_UNVERIFIED_POLICY == "needs_review"

    switched = run_assertion_gate(
        spec, worktree=tmp_path, assertions={"unverified_policy": "allow"}
    )
    assert switched["verdict"] == "pass"
    assert switched["ok"] is True


def test_old_spec_without_structured_acceptance_still_loads(tmp_path: Path) -> None:
    gate = run_assertion_gate(_spec(["功能可以通过入口使用", "相关测试通过"]),
                              worktree=tmp_path)
    assert gate["verdict"] == "needs_review"
    assert len(gate["assertions"]["unverified"]) == 2
    assert gate["assertions"]["cases"] == []


# ── 4. 沙箱、超时、失败隔离与逐条记录 ───────────────────────────────────────────
def test_cases_are_isolated_and_each_has_status_duration_error(tmp_path: Path) -> None:
    (tmp_path / "ok.txt").write_text("x", encoding="utf-8")
    exe = Path(sys.executable).name
    spec = _spec(acceptance=[], assertions=[
        {"id": "AC-TIMEOUT", "type": "command_exit0",
         "command": [sys.executable, "-c", "import time; time.sleep(2)"]},
        {"id": "AC-FAIL", "type": "command_exit0",
         "command": [sys.executable, "-c", "raise SystemExit(3)"]},
        {"id": "AC-PASS", "type": "exists", "path": "ok.txt"},
        {"id": "AC-BLOCKED", "type": "command_exit0", "command": ["curl", "http://x"]},
        {"id": "AC-ESCAPE", "type": "exists", "path": "../secret.txt"},
    ])
    gate = run_assertion_gate(
        spec,
        worktree=tmp_path,
        assertions={"timeout_s": 0.3, "allowed_commands": ["python", "python3", exe]},
    )
    rows = gate["assertions"]["cases"]
    assert len(rows) == 5
    for row in rows:
        assert {"id", "acceptance_id", "status", "duration", "error"} <= set(row)
    statuses = [row["status"] for row in rows]
    assert statuses == ["timeout", "fail", "pass", "error", "error"]
    assert rows[0]["duration"] >= 0
    assert gate["verdict"] == "fail"


# ── 5. 机器可读报告 + 旧字段兼容 ───────────────────────────────────────────────
def test_report_keeps_legacy_fields_and_adds_mapping(tmp_path: Path) -> None:
    (tmp_path / "ok.txt").write_text("x", encoding="utf-8")
    legacy = {
        "ok": True,
        "state": "passed",
        "reason": "legacy reason",
        "checks": [{"id": "test", "ok": True, "duration_s": 0.1}],
        "warnings": [],
    }
    gate = apply_assertion_gate(
        legacy,
        spec=_spec(["AC-009: exists ok.txt"]),
        worktree=tmp_path,
        assertions={"enabled": True, "spec_id": "spec-fixed"},
    )
    assert gate["checks"] == legacy["checks"]
    assert gate["warnings"] == []
    assert gate["spec_id"] == "spec-fixed"
    assert gate["verdict"] == "pass"
    assert gate["assertions"]["mapping"] == {"AC-009": "case-AC-009"}
    assert gate["assertions"]["unverified"] == []
    report = json.loads(json.dumps(gate, ensure_ascii=False))
    assert report["legacy_verdict"]["state"] == "passed"
    assert report["assertions"]["summary"]["total"] == 1


def test_cli_spec_gate_writes_json_and_uses_exit_code(tmp_path: Path, monkeypatch,
                                                      capsys) -> None:
    (tmp_path / "ok.txt").write_text("x", encoding="utf-8")
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(_spec(["AC-1: exists ok.txt"])), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "taskagent", "spec-gate", "--spec", str(spec_path),
        "--worktree", str(tmp_path), "--out", str(tmp_path / "gate.json"),
    ])
    code = cli.main()
    capsys.readouterr()
    report = json.loads((tmp_path / "gate.json").read_text(encoding="utf-8"))
    assert code == gate_exit_code(report) == 0
    assert report["spec_id"]
    assert report["assertions"]["mapping"] == {"AC-1": "case-AC-1"}


def test_run_gate_default_path_is_unchanged(tmp_path: Path, monkeypatch) -> None:
    sentinel = {"ok": True, "state": "passed", "reason": "legacy", "checks": []}
    monkeypatch.setattr(
        "taskagent.verification._run_gate_checks", lambda **kwargs: sentinel
    )
    outcome = run_gate(
        repo_name="repo",
        repo_path=str(tmp_path),
        worktree=str(tmp_path),
        changed=["a.py"],
        item_id="item-1",
        config_path=tmp_path / "missing.json",
        data_dir=tmp_path,
    )
    assert outcome == sentinel
    assert "assertions" not in outcome


def test_markdown_report_shows_assertion_mapping_and_unverified() -> None:
    from taskagent.models import RunResult, WorkItem
    from taskagent.report import render_report

    item = WorkItem.create(title="spec", description="d", kind="task")
    item.result = RunResult(ok=False, dry_run=False, command="")
    item.result.verification = {
        "ok": False,
        "verdict": "needs_review",
        "state": "needs_review",
        "reason": "需人工复核",
        "checks": [],
        "assertions": {
            "spec_id": "spec-x",
            "mapping": {"AC-1": "case-AC-1"},
            "cases": [{
                "id": "case-AC-1", "acceptance_id": "AC-1",
                "status": "pass", "duration": 0.01, "error": "",
            }],
            "unverified": [{
                "acceptance_id": "AC-2", "status": "unverified",
                "reason": "无法编译", "text": "含糊描述",
            }],
            "summary": {"total": 1, "pass": 1, "unverified": 1},
        },
    }
    text = render_report(item)
    assert "验收断言" in text
    assert "case-AC-1" in text
    assert "AC-2" in text


# ── 6. 回归：不静默丢弃 / 沙箱 fail-closed / 旧字段语义 ────────────────────────
def test_uncompilable_and_duplicate_items_are_reported_not_dropped() -> None:
    spec = _spec(acceptance=[], assertions=[
        {"id": "AC-OK", "type": "exists", "path": "ok.txt"},
        {"id": "AC-OK", "type": "exists", "path": "ok.txt"},
        {"id": "AC-BAD", "type": "unknown_kind", "path": "ok.txt"},
    ])
    compilation = compile_spec(spec)
    assert len(compilation.cases) + len(compilation.unverified) == 3
    assert [case.acceptance_id for case in compilation.cases] == ["AC-OK"]
    assert [item["acceptance_id"] for item in compilation.unverified] == [
        "AC-OK", "AC-BAD",
    ]
    assert all(item["status"] == "unverified" for item in compilation.unverified)


def test_missing_worktree_fails_closed(tmp_path: Path) -> None:
    spec = _spec(["AC-1: exists ok.txt"])
    for worktree in (None, tmp_path / "missing"):
        gate = run_assertion_gate(spec, worktree=worktree)
        assert gate["ok"] is False
        assert gate["verdict"] == "fail"
        row = gate["assertions"]["cases"][0]
        assert row["status"] == "error"
        assert "worktree" in row["error"]


def test_command_cannot_use_local_executable_or_outside_paths(tmp_path: Path) -> None:
    fake = tmp_path / "python"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    spec = _spec(acceptance=[], assertions=[
        {"id": "AC-REL", "type": "command_exit0", "command": ["./python"]},
        {"id": "AC-LOCAL", "type": "command_exit0", "command": [str(fake)]},
        {"id": "AC-SYSFILE", "type": "command_exit0", "command": ["cat", "/etc/passwd"]},
    ])
    gate = run_assertion_gate(spec, worktree=tmp_path)
    assert [row["status"] for row in gate["assertions"]["cases"]] == [
        "error", "error", "error",
    ]


def test_unverified_policy_fail_is_supported(tmp_path: Path) -> None:
    gate = run_assertion_gate(
        _spec(["AC-X: 需求描述含糊，无法判定真伪"]),
        worktree=tmp_path,
        assertions={"unverified_policy": "fail"},
    )
    assert gate["verdict"] == "fail"
    assert gate["ok"] is False
    assert gate_exit_code(gate) != 0


def test_apply_gate_keeps_legacy_state_and_reason(tmp_path: Path) -> None:
    legacy = {
        "ok": True, "state": "passed_with_warnings", "reason": "legacy reason",
        "checks": [],
    }
    gate = apply_assertion_gate(
        legacy, spec=_spec(["AC-1: exists missing.txt"]), worktree=tmp_path
    )
    assert gate["state"] == "passed_with_warnings"
    assert gate["reason"] == "legacy reason"
    assert gate["assertion_state"] == "assertion_failed"
    assert gate["verdict"] == "fail"
    assert gate["ok"] is False


def test_loop_still_repairs_when_legacy_state_is_preserved() -> None:
    from taskagent.loop import repairable

    gate = {
        "ok": False, "state": "passed", "reason": "legacy",
        "assertion_state": "assertion_failed",
    }
    assert repairable(gate) is True


def test_run_gate_only_enables_when_explicit_and_llm_is_reference(
    tmp_path: Path, monkeypatch
) -> None:
    spec = _spec(["AC-1: exists missing.txt"])
    sentinel = {"ok": True, "state": "passed", "reason": "legacy", "checks": []}
    monkeypatch.setattr("taskagent.verification._run_gate_checks", lambda **kw: sentinel)
    kwargs = {
        "repo_name": "repo",
        "repo_path": str(tmp_path),
        "worktree": str(tmp_path),
        "changed": [],
        "item_id": "item-1",
        "config_path": tmp_path / "missing.json",
        "data_dir": tmp_path,
    }
    untouched = run_gate(**kwargs, spec=spec, assertions={"timeout_s": 5})
    assert untouched == sentinel

    gated = run_gate(**kwargs, spec=spec, assertions={"enabled": True})
    assert gated["ok"] is False
    assert gated["verdict"] == "fail"
    reviewed = apply_review(
        gated, Review(verdict="approve", reason="mock LLM says pass"), deciding=False
    )
    assert reviewed["ok"] is False
    assert reviewed["verdict"] == "fail"
    assert reviewed["review"]["verdict"] == "approve"


async def test_scheduler_assertion_mode_keeps_mock_llm_out_of_verdict(
    env, tmp_path: Path, monkeypatch
) -> None:
    from taskagent.models import RunResult, WorkItem

    scheduler, _store, _gitmap, _events, _data = env
    monkeypatch.delenv("TASKAGENT_REVIEW", raising=False)
    (tmp_path / "ok.txt").write_text("x", encoding="utf-8")

    async def fake_review(item, changed, worktree, **kwargs):
        return Review(verdict="approve", reason="mock LLM says pass")

    monkeypatch.setattr("taskagent.reviewer.review_diff", fake_review)
    scheduler.settings.review_provider = lambda: object()
    scheduler.settings.assertions_enabled = True

    item = WorkItem.create(title="spec", description="d", kind="task")
    item.spec = _spec(["AC-1: exists missing.txt"])
    result = RunResult(
        ok=True, dry_run=False, command="", changed_files=["ok.txt"],
        worktree=str(tmp_path),
    )
    gate = await scheduler._verify(item, result)
    assert gate["ok"] is False
    assert gate["verdict"] == "fail"
    assert gate["decision_source"] == "assertion"
    assert gate["review"]["verdict"] == "approve"
    assert gate["review_is_reference"] is True
