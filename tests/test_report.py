from __future__ import annotations

import json

from taskagent.models import ItemStatus, RunResult, WorkItem
from taskagent.report import extract_conclusion, parent_title_of, render_report, summarize_stdout


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


def test_extract_conclusion_from_dsh_jsonl_uses_final_event():
    lines = [
        {"type": "session", "sessionId": "s1"},
        {"type": "text", "text": "我先看一下仓库"},
        {"type": "tool_call", "tool": "bash", "input": {"command": "ls"}},
        {"type": "text", "text": "接下来改代码"},
        {"type": "final", "text": "所有修改已完成并通过验证。\n\n## 改动文件\n- a.py", "reason": {"kind": "completed"}},
    ]
    raw = "\n".join(json.dumps(line, ensure_ascii=False) for line in lines)
    conclusion = extract_conclusion(raw)
    assert conclusion.is_final is True
    assert conclusion.text.startswith("所有修改已完成并通过验证。")
    assert "我先看一下仓库" not in conclusion.text
    assert '"type"' not in conclusion.text
    assert '"tool_call"' not in conclusion.text


def test_extract_conclusion_marks_process_notes_as_not_final():
    # Real codebuddy stdout is truncated mid-stream and contains only narration.
    notes = [
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "我先读代码"}]},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Bash 被拒绝，改用 Read"}]},
    ]
    raw = "[" + json.dumps(notes[0], ensure_ascii=False) + ", " + json.dumps(notes[1], ensure_ascii=False)
    conclusion = extract_conclusion(raw)
    assert conclusion.is_final is False
    assert conclusion.truncated_stream is True
    assert "我先读代码" in conclusion.text and "Bash 被拒绝" in conclusion.text


def test_report_renders_final_answer_markdown_without_text_fence():
    events = [{"type": "final", "text": "改完了。\n\n## 改动文件\n- a.py\n- b.py"}]
    item = _item(stdout="\n".join(json.dumps(e, ensure_ascii=False) for e in events))
    report = render_report(item)
    assert "执行输出摘要（最终结论）" in report
    assert "## 改动文件\n- a.py" in report
    # A real answer keeps Markdown structure; only raw streams are fenced.
    fenced_block = "<details><summary>执行输出摘要（最终结论）</summary>\n\n```text"
    assert fenced_block not in report
    assert "<details><summary>执行命令</summary>\n\n```text" in report


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


def test_report_prompt_block_is_open():
    item = _item()
    report = render_report(item)
    assert "<details open><summary>下发给 codebuddy 的提示词</summary>" in report
    assert "<details><summary>执行命令</summary>" in report


def test_report_prompt_title_uses_actual_executor():
    item = WorkItem.create(title="DSH 任务", description="x", executor="dsh")
    report = render_report(item)
    assert "下发给 DSH 的提示词" in report
    assert "下发给 codebuddy 的提示词" not in report


def test_report_prompt_title_keeps_unknown_executor_id():
    item = WorkItem.create(title="自定义执行端", description="x", executor="my-cli")
    report = render_report(item)
    assert "下发给 my-cli 的提示词" in report


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
