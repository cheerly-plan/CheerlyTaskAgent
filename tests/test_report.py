from __future__ import annotations

import json

from taskagent.models import ItemStatus, RunResult, WorkItem
from taskagent.report import parent_title_of, render_report, summarize_stdout


def _item(stdout: str = "", description: str = "改点东西") -> WorkItem:
    item = WorkItem.create(title="测试报告", description=description, model="hy4flash")
    item.status = ItemStatus.SUCCEEDED
    item.result = RunResult(
        ok=True,
        dry_run=False,
        command="codebuddy -p --model hy4flash '你是一名资深工程师。很长的提示词…'",
        stdout=stdout,
        changed_files=["a.py", "b.py"],
        duration_s=1.5,
        synced_files=["a.py"],
    )
    return item


def test_summarize_stdout_extracts_final_conclusions():
    events = [
        {"type": "message", "role": "user", "content": []},
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "第一步结论"}],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "最终结论：改完了"}],
        },
    ]
    out = summarize_stdout(json.dumps(events))
    assert "第一步结论" in out
    assert "最终结论：改完了" in out
    # Raw event scaffolding must not leak.
    assert "output_text" not in out
    assert '"role"' not in out


def test_summarize_stdout_tolerates_truncated_tail():
    good = json.dumps({"type": "message", "role": "assistant",
                       "content": [{"type": "output_text", "text": "结论在此"}]})
    raw = "[" + good + ", {\"type\":\"message\", \"role\":\"assist"  # broken tail
    out = summarize_stdout(raw)
    assert "结论在此" in out


def test_summarize_stdout_plain_text_fallback():
    assert summarize_stdout("plain output") == "plain output"
    assert summarize_stdout("") == ""


def test_summarize_stdout_truncates_huge_output():
    huge = "x" * 10000
    out = summarize_stdout(huge, limit=500)
    assert len(out) < 700
    assert "已截断" in out


def test_report_is_compact_with_long_stdout():
    # Simulate real codebuddy output: short conclusions buried in a huge JSON
    # event stream plus an oversized system reminder (the actual culprit).
    events = [
        {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "<system-reminder>" + "背景" * 20000 + "</system-reminder>"}]},
        {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "结论：改完了"}]},
    ]
    item = _item(stdout=json.dumps(events))
    report = render_report(item)
    # The verbose stream/prompt is stripped; report stays tiny.
    assert len(report) < 3000
    assert len(report) < len(item.result.stdout) / 10
    # Verbose material lives in collapsed details, not inline.
    assert "<details>" in report
    assert "执行输出摘要" in report
    assert "执行命令" in report
    assert "下发给 codebuddy 的提示词" in report
    # Command no longer embeds the full prompt text.
    assert "提示词见上方" in report


def test_report_keeps_key_fields():
    item = _item()
    report = render_report(item)
    for field in ["测试报告", "succeeded", "hy4flash", "a.py", "b.py", "自动同步到主仓库"]:
        assert field in report


def test_report_without_result_has_no_execution_section():
    item = WorkItem.create(title="草稿", description="还没执行")
    report = render_report(item)
    assert "## 执行结果" not in report
    assert "下发给 codebuddy 的提示词" in report


def test_child_report_shows_parent():
    child = WorkItem.create(title="登录页", description="", parent_ref="tapd:story:p1")
    # 有父标题时用标题。
    assert "| 所属父需求 | 用户中心 |" in render_report(child, parent_title="用户中心")
    # 没有父标题时降级到 source_ref，不会丢失归属信息。
    assert "| 所属父需求 | tapd:story:p1 |" in render_report(child)


def test_top_level_report_has_no_parent_line():
    top = WorkItem.create(title="顶层需求", description="")
    assert "所属父需求" not in render_report(top, parent_title="不应出现")


def test_parent_title_of_looks_up_by_source_ref():
    from taskagent.models import SourceType

    parent = WorkItem.create(title="父需求", description="")
    parent.source = SourceType.TAPD
    parent.source_ref = "tapd:story:p1"

    class FakeStore:
        def list(self, limit=200, offset=0):
            return [parent]

    child = WorkItem.create(title="子需求", description="", parent_ref="tapd:story:p1")
    assert parent_title_of(child, FakeStore()) == "父需求"
    assert parent_title_of(WorkItem.create(title="顶层", description=""), FakeStore()) == ""
