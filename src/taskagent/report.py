"""Local Markdown reports — the only result sink in this phase."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .baseline import describe as describe_baseline
from .executor import build_prompt
from .memory import memory_text
from .models import WorkItem
from .spec import TaskSpec

# Raw agent stdout is a verbose event stream (often >100k chars, including the
# agent's own system prompt). Keep only conclusions in the report; the full
# stream stays on the WorkItem for the API.
_STDOUT_PREVIEW_LIMIT = 4000

# Human-facing name for the backend that ran a task. The prompt itself is
# backend-agnostic, but the report section title must name the actual executor
# (e.g. "DSH" for dsh tasks, not the historical hard-coded "codebuddy").
_BACKEND_LABELS: dict[str, str] = {
    "codebuddy": "codebuddy",  # keep the long-standing wording for legacy reports/tests
    "dsh": "DSH",
}


def executor_label(executor: str | None) -> str:
    """Display name for an executor id; unknown/custom ids are shown verbatim."""
    eid = (executor or "").strip()
    return _BACKEND_LABELS.get(eid.lower(), eid) or "codebuddy"


@dataclass(frozen=True)
class Conclusion:
    """Extracted answer plus metadata about how trustworthy it is."""

    text: str
    is_final: bool = False
    truncated_stream: bool = False


def _decode_events(raw: str) -> tuple[list[dict[str, Any]], bool]:
    """Decode both CLI stdout dialects; report whether the stream was cut off.

    - codebuddy prints a pretty-printed JSON *array* that is often truncated
      mid-event; decode element by element and keep the parsed prefix.
    - DSH prints newline-delimited JSON (session/status/tool_call/text/final);
      line-by-line decoding keeps the trailing ``final`` answer.
    """
    text = (raw or "").strip()
    if not text:
        return [], False
    if text.startswith("["):
        decoder = json.JSONDecoder()
        pos = 1
        events: list[dict[str, Any]] = []
        truncated = not text.endswith("]")
        while pos < len(text):
            while pos < len(text) and text[pos] in " \n\r\t,":
                pos += 1
            if pos >= len(text) or text[pos] == "]":
                break
            try:
                event, end = decoder.raw_decode(text, pos)
            except json.JSONDecodeError:
                truncated = True
                break  # keep what we already parsed
            if isinstance(event, dict):
                events.append(event)
            pos = end
        return events, truncated

    events = []
    parsed = 0
    broken = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            broken += 1
            continue
        parsed += 1
        if isinstance(event, dict):
            events.append(event)
    # No parseable JSON at all: this is plain text, not a truncated stream.
    return events, parsed > 0 and broken > 0


def parent_title_of(item: WorkItem, store) -> str:
    """子需求的父需求标题；父不存在或未建单时返回空串。"""
    if not item.parent_ref:
        return ""
    for candidate in store.list(limit=100000):
        if candidate.source_ref == item.parent_ref:
            return candidate.title
    return ""


def parent_context_of(item: WorkItem, store, *, limit: int = 2000) -> str:
    """子任务提示词里的父需求上下文：标题 + 截断描述。

    父未入库时退回工单自带的 parent_title 快照；两者都为空则不注入。只取
    直接父一层，描述超长截断，避免挤占子任务上下文。
    """
    if not item.parent_ref:
        return ""
    parent = None
    for candidate in store.list(limit=100000):
        if candidate.source_ref == item.parent_ref:
            parent = candidate
            break
    title = (parent.title if parent else "") or item.parent_title
    if not title:
        return ""
    description = ((parent.description if parent else "") or "").strip()
    if len(description) > limit:
        description = description[:limit].rstrip() + "（父需求描述已截断）"
    context = f"你正在实现一个更大需求下的子任务。\n父需求：{title}"
    if description:
        context += f"\n父需求描述：{description}"
    return context


def _clip(text: str, raw: str, limit: int) -> str:
    if len(text) > limit:
        return text[:limit].rstrip() + f"\n\n…（原始输出共 {len(raw)} 字符，已截断；完整内容见工单数据）"
    return text


def extract_conclusion(raw: str, limit: int = _STDOUT_PREVIEW_LIMIT) -> Conclusion:
    """Pick the agent's answer, not its intermediate "let me look around" notes.

    Priority: explicit ``final``/``result`` events (DSH / CLI result envelope),
    then assistant output_text blocks, then DSH ``text`` events, then raw
    plain text. Multiple assistant blocks with no explicit final event are
    process notes: show them but mark them as not a final conclusion.
    """
    events, truncated = _decode_events(raw)

    # DSH's final answer: {"type":"final","text":"...","reason":{...}}
    for event in reversed(events):
        if event.get("type") == "final":
            text = str(event.get("text") or "").strip()
            if text:
                return Conclusion(_clip(text, raw or "", limit), True, truncated)

    # codebuddy's result envelope: {"type":"result","result":"..."}
    for event in reversed(events):
        if event.get("type") == "result":
            text = str(event.get("result") or event.get("text") or "").strip()
            if text:
                return Conclusion(_clip(text, raw or "", limit), True, truncated)

    assistant: list[str] = []
    for event in events:
        if event.get("type") == "message" and event.get("role") == "assistant":
            for content in event.get("content") or []:
                if content.get("type") == "output_text":
                    value = str(content.get("text") or "").strip()
                    if value:
                        assistant.append(value)
    if assistant:
        # A single block is the answer; several are step-by-step narration.
        return Conclusion(_clip("\n\n".join(assistant), raw or "", limit),
                          len(assistant) == 1, truncated)

    # DSH text events without a final event are also process notes.
    texts = [str(e.get("text") or "").strip() for e in events if e.get("type") == "text"]
    texts = [t for t in texts if t]
    if texts:
        return Conclusion(_clip("\n\n".join(texts), raw or "", limit), False, truncated)

    plain = (raw or "").strip()
    return Conclusion(_clip(plain, raw or "", limit), False, False) if plain else Conclusion("")


def summarize_stdout(raw: str, limit: int = _STDOUT_PREVIEW_LIMIT) -> str:
    """Backward-compatible text-only view of :func:`extract_conclusion`."""
    return extract_conclusion(raw, limit).text


def _command_skeleton(command: str) -> str:
    """Show the command without repeating the full embedded prompt."""
    # codebuddy command ends with the prompt as the last single-quoted argument.
    marker = " '你是一名资深工程师"
    if marker in command:
        return command.split(marker, 1)[0] + " '<提示词见上方>'"
    return command


def _collapsed(title: str, body: str, *, fenced: bool = True, opened: bool = False) -> list[str]:
    """Emit an HTML <details> block (renders collapsed in most Markdown viewers).

    Real conclusions keep their Markdown structure (headings/lists/bold); raw
    event streams and process notes stay in a plain-text fence. Pass ``opened``
    for blocks that should be visible without a click (e.g. the prompt).
    """
    inner = ["```text", body.rstrip(), "```"] if fenced else [body.rstrip()]
    attrs = " open" if opened else ""
    return [
        f"<details{attrs}><summary>{title}</summary>",
        "",
        *inner,
        "",
        "</details>",
        "",
    ]


_VERIFY_STATE_LABELS: dict[str, str] = {
    "passed": "通过",
    "failed": "未通过",
    "unconfigured": "未配置",
    "no_changes": "无改动",
    "passed_with_warnings": "通过（有警告）",
    "test_files_changed": "改动修改/删除了测试文件",
    "protected_files_changed": "改动受保护文件",
    "too_many_files": "改动文件过多",
    "diff_too_large": "改动行数过多",
    "incomplete": "验证类别不齐",
    "review_blocked": "LLM 评审 block",
    "review_needs_human": "LLM 评审转人工审批",
    "error": "验证出错",
    "disabled": "已禁用",
}


def _review_section(review: dict[str, Any]) -> list[str]:
    """Render the LLM review verdict folded into the verification gate."""
    verdict_label = {
        "approve": "通过", "comment": "建议", "block": "阻塞",
    }.get(str(review.get("verdict") or ""), str(review.get("verdict") or "未评审"))
    lines = ["### LLM 评审", "", f"> 结论：**{verdict_label}**"]
    state = str(review.get("state") or "")
    if state and state != "done":
        lines[-1] += f"（{state}）"
    if review.get("reason"):
        lines.append(f"> {review['reason']}")
    lines.append("")
    issues = review.get("issues") or []
    if issues:
        lines += ["问题清单：", ""]
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            item = f"- [{issue.get('severity') or 'medium'}] {issue.get('message') or ''}"
            if issue.get("file"):
                item += f"（`{issue['file']}`）"
            lines.append(item)
        lines.append("")
    risks = review.get("risks") or []
    if risks:
        lines += [f"风险标签：{'、'.join(str(tag) for tag in risks)}", ""]
    if review.get("needs_human"):
        lines += ["> ⚠️ 大 diff 或高风险，已转人工审批，未自动合并。", ""]
    return lines


def _verification_section(verification: dict[str, Any]) -> list[str]:
    """Render the post-execution gate verdict (empty for legacy/dry-run)."""
    state = str(verification.get("state") or "")
    label = _VERIFY_STATE_LABELS.get(state, state or "未知")
    reason = str(verification.get("reason") or "")
    lines = ["### 验证", "", f"> 验证结果：**{label}**" + (f"（{reason}）" if reason else ""), ""]
    review = verification.get("review") or {}
    if isinstance(review, dict) and review:
        lines += _review_section(review)
    if verification.get("override"):
        lines += [
            (
                f"> ⚠️ 人工强制合并：{verification.get('override_by') or 'user'} 于 "
                f"{verification.get('override_at') or '—'} 跳过验证门禁。"
            ),
            "",
        ]
    test_files = verification.get("test_files") or []
    reasons = verification.get("test_file_reasons") or {}
    if test_files:
        lines += ["被修改/删除的测试文件：", ""] + [
            f"- `{name}`" + (
                f"：{reasons.get(name)}" if isinstance(reasons, dict) and reasons.get(name) else ""
            )
            for name in test_files
        ] + [""]
    test_added = verification.get("test_files_added") or []
    if test_added:
        lines += [
            "新增的测试文件（阻塞判定仍使用基线测试，新增测试结果仅供参考）：",
            "",
        ] + [f"- `{name}`" for name in test_added] + [""]
    test_augmented = verification.get("test_files_augmented") or []
    if test_augmented:
        lines += [
            "仅在原测试文件中新增用例（原有用例未被删改；判定仍使用基线测试）：",
            "",
        ] + [f"- `{name}`" for name in test_augmented] + [""]
    protected = verification.get("protected_files") or []
    if protected:
        lines += ["改动的受保护文件：", ""] + [f"- `{name}`" for name in protected] + [""]
    warnings = verification.get("warnings") or []
    if warnings:
        lines += ["警告：", ""] + [f"- {item}" for item in warnings] + [""]
    checks = verification.get("checks") or []
    if checks:
        lines += ["| 检查 | 命令 | 结果 | 基线 | 耗时 |", "|---|---|---|---|---|"]
        for check in checks:
            command = " ".join(str(part) for part in (check.get("command") or []))
            if check.get("pre_existing"):
                outcome = "失败（既有问题）"
            elif check.get("timed_out"):
                outcome = "超时"
            elif check.get("ok"):
                outcome = "通过（重试）" if int(check.get("attempts") or 1) > 1 else "通过"
            elif check.get("exit_code") is None:
                outcome = "无法执行"
            else:
                outcome = f"失败（退出码 {check.get('exit_code')}）"
            baseline = check.get("baseline")
            if isinstance(baseline, dict):
                if baseline.get("ok"):
                    base_text = "通过"
                elif baseline.get("timed_out"):
                    base_text = "超时"
                elif baseline.get("error"):
                    base_text = "无法执行"
                else:
                    base_text = "失败"
            else:
                base_text = "—"
            lines.append(
                f"| {check.get('label') or check.get('id')} | `{command}` | {outcome} "
                f"| {base_text} | {check.get('duration_s', 0)}s |"
            )
        lines += [""]
    new_tests = verification.get("new_tests") or []
    if new_tests:
        lines += [
            "含新增测试的全量检查（仅告警，不参与合并判定）：",
            "",
            "| 检查 | 结果 | 耗时 |",
            "|---|---|---|",
        ]
        for check in new_tests:
            if check.get("timed_out"):
                outcome = "超时"
            elif check.get("ok"):
                outcome = "通过"
            elif check.get("exit_code") is None:
                outcome = "无法执行"
            else:
                outcome = f"失败（退出码 {check.get('exit_code')}）"
            lines.append(
                f"| {check.get('label') or check.get('id')} | {outcome} "
                f"| {check.get('duration_s', 0)}s |"
            )
        lines += [""]
    missing = verification.get("missing_categories") or []
    if missing:
        lines += [f"> ⚠️ 缺少验证类别：{'、'.join(str(item) for item in missing)}", ""]
    expired = verification.get("expired_na") or []
    if expired:
        lines += [
            f"> ⚠️ N/A 已过期（不再生效）：{'、'.join(str(item) for item in expired)}",
            "",
        ]
    na = verification.get("na") or {}
    if isinstance(na, dict) and na:
        lines += ["未适用（N/A）：", ""] + [
            f"- **{category}**：{reason}" for category, reason in na.items()
        ] + [""]
    for check in checks:
        tail = str(check.get("tail") or "")
        if tail.strip():
            lines += _collapsed(
                f"验证输出：{check.get('label') or check.get('id')}", tail
            )
    return lines


def _delivery_section(delivery: dict[str, Any]) -> list[str]:
    """交付结果：任务分支上的提交、push、PR/MR。"""
    if not isinstance(delivery, dict) or not delivery:
        return []
    lines = ["### 交付", "", "| 字段 | 值 |", "|---|---|"]
    lines.append(f"| 模式 | {delivery.get('mode') or '—'} |")
    if delivery.get("branch"):
        lines.append(f"| 任务分支 | `{delivery['branch']}` |")
    if delivery.get("commit"):
        lines.append(f"| 提交 | `{delivery['commit']}` |")
    if delivery.get("pushed"):
        lines.append(f"| 已推送 | 是（{delivery.get('remote') or 'origin'}） |")
    if delivery.get("pr_url"):
        lines.append(f"| PR / MR | {delivery['pr_url']} |")
    if delivery.get("error"):
        lines.append(f"| 错误 | {delivery['error']} |")
    lines.append("")
    return lines


def _usage_section(usage: dict[str, Any]) -> list[str]:
    """Token 账本：CLI 与后端 Agent 分列，最后给合计。"""
    if not isinstance(usage, dict) or not usage:
        return []
    rows = []
    for key, label in (("cli", "执行后端 CLI"), ("llm", "后端 Agent（规格/评审/选仓）")):
        bucket = usage.get(key)
        if not isinstance(bucket, dict) or not int(bucket.get("total_tokens") or 0):
            continue
        rows.append(
            f"| {label} | {int(bucket.get('input_tokens') or 0)} "
            f"| {int(bucket.get('output_tokens') or 0)} "
            f"| {int(bucket.get('total_tokens') or 0)} "
            f"| {int(bucket.get('calls') or 0)} |"
        )
    total = int(usage.get("total_tokens") or 0)
    if not rows or not total:
        return []
    rows.append(f"| **合计** | | | **{total}** | {int(usage.get('calls') or 0)} |")
    return [
        "## Token 用量",
        "",
        "| 来源 | 输入 | 输出 | 合计 | 调用 |",
        "|---|---|---|---|---|",
        *rows,
        "",
    ]


def _spec_section(spec: dict[str, Any]) -> list[str]:
    """任务规格：目标、验收标准、约束与风险（评审就是按这些逐条核对）。"""
    parsed = TaskSpec.from_dict(spec)
    if parsed is None:
        return []
    lines = ["## 任务规格", ""]
    if parsed.goal:
        lines += [f"> **目标**：{parsed.goal}", ""]
    if parsed.acceptance:
        lines += ["验收标准：", ""] + [
            f"{index}. {text}" for index, text in enumerate(parsed.acceptance, 1)
        ] + [""]
    if parsed.constraints:
        lines += ["约束：", ""] + [f"- {text}" for text in parsed.constraints] + [""]
    meta = f"风险等级：**{parsed.risk}**；来源：{'LLM' if parsed.source == 'llm' else '规则'}"
    if parsed.reason:
        meta += f"（{parsed.reason}）"
    lines += [f"> {meta}", ""]
    return lines


def render_report(item: WorkItem, parent_title: str = "") -> str:
    repo = item.repo
    result = item.result
    lines = [
        f"# 工作项报告：{item.title}",
        "",
        "| 字段 | 值 |",
        "|---|---|",
        f"| ID | `{item.id}` |",
        f"| 类型 | {item.kind.value} |",
    ]
    if item.parent_ref:
        lines.append(f"| 所属父需求 | {parent_title or item.parent_ref} |")
    lines += [
        f"| 来源 | {item.source.value} {item.source_ref} |",
        f"| 状态 | {item.status.value} |",
        f"| 执行模式 | {item.exec_mode} |",
        f"| 模型 | {item.model or '执行器默认'} |",
        f"| git 地址 | {item.git_url or '—'} |",
        f"| 映射仓库 | {repo.path if repo else '未映射'} |",
        f"| 批准来源 | {'自动执行' if item.approved_by == 'auto' else '人工'} |",
        "",
    ]
    if item.description:
        lines += ["## 需求描述", "", item.description.rstrip(), ""]
    lines += _spec_section(item.spec)

    if result:
        verification = result.verification if isinstance(result.verification, dict) else {}
        gate_ok = bool(verification.get("ok", True))
        lines += [
            "## 执行结果",
            "",
            "| 字段 | 值 |",
            "|---|---|",
            f"| 成功 | {'是' if result.ok and gate_ok else '否'} |",
            f"| dry-run | {'是' if result.dry_run else '否'} |",
            f"| 耗时 | {result.duration_s:.2f}s |",
            f"| 改动文件 | {len(result.changed_files)} 个 |",
            f"| 执行轮次 | {max(1, int(result.rounds or 1))} 轮 |",
            f"| worktree | {result.worktree or '—'} |",
        ]
        baseline_note = describe_baseline(
            result.baseline if isinstance(result.baseline, dict) else {}
        )
        if baseline_note:
            lines.append(f"| 基线同步 | {baseline_note} |")
        lines.append("")
        if result.error:
            lines += [f"> ⚠️ 执行错误：{result.error}", ""]
        if result.changed_files:
            lines += ["### 改动文件", ""] + [f"- `{name}`" for name in result.changed_files] + [""]
        lines += _usage_section(item.usage)

        if verification:
            lines += _verification_section(verification)
        memory_hint = memory_text(item.memory)
        if memory_hint:
            lines += _collapsed("本次注入的跨任务记忆", memory_hint)
        if item.feedback and int(result.rounds or 1) > 1:
            lines += _collapsed("最后一轮反馈（未通过的原因）", item.feedback)

        # 所有 apply（人工/自动）：门禁通过后由 scheduler 做原子合并。
        if item.hold_merge:
            if item.merge_state == "merged":
                note = "已自动合并到主仓库工作区（未提交；冲突文件不会被覆盖）。"
            elif item.merge_state == "blocked":
                n = len(result.sync_conflicts)
                note = (
                    f"自动合并有 {n} 个文件冲突，**一个文件都没有写入主仓库**。"
                    "到任务中心对该任务点「合并」可重试（先处理冲突）。"
                ) if n else "自动合并失败，**没有写入主仓库**；可在任务中心点「合并」重试。"
            elif verification and not verification.get("ok", True):
                note = "验证未通过，**没有写入主仓库**；worktree 保留供排查。"
            else:
                note = "自动执行结果尚未合并到主仓库工作区。"
            lines += ["### 合并状态", "", f"> {note}", ""]
        lines += _delivery_section(item.delivery)

        # Auto-sync outcome: which files landed in the main repo, what was
        # mirrored as a deletion, and where originals were backed up.
        if result.synced_files or result.sync_deleted or result.sync_error or result.sync_conflicts:
            lines += [
                "### 自动同步到主仓库",
                "",
                "| 字段 | 值 |",
                "|---|---|",
                f"| 已同步 | {len(result.synced_files)} 个文件 |",
                f"| 已删除 | {len(result.sync_deleted)} 个文件 |",
                f"| 冲突跳过 | {len(result.sync_conflicts)} 个文件 |",
                f"| 备份目录 | {result.sync_backup_dir or '—'} |",
                "",
            ]
            if result.sync_error:
                lines += [f"> ⚠️ 同步错误：{result.sync_error}", ""]
            if result.synced_files:
                lines += ["已写入工作区："] + [f"- `{name}`" for name in result.synced_files] + [""]
            if result.sync_deleted:
                lines += ["已删除："] + [f"- `{name}`" for name in result.sync_deleted] + [""]
        if result.sync_conflicts:
            lines += [
                "#### ⚠️ 同步冲突（主仓库改动未被覆盖）",
                "",
                "以下文件在主仓库与 codebuddy 之间分叉，出于安全未自动覆盖。" +
                "请人工比对备份目录中的 `.main` / `.codebuddy` 版本后手动合并：",
                "",
            ] + [f"- {name}" for name in result.sync_conflicts] + [""]
        if result.sync_skipped:
            lines += ["同步时跳过（忽略规则/路径不安全/失败）：", ""] + [
                f"- `{name}`" for name in result.sync_skipped
            ] + [""]

        # Verbose material goes to collapsed appendices instead of the main body.
        conclusion = extract_conclusion(result.stdout)
        if conclusion.text:
            if conclusion.is_final:
                title = "执行输出摘要（最终结论）"
            elif conclusion.truncated_stream:
                title = "执行过程节选（输出被截断，未捕获最终结论）"
            else:
                title = "执行输出摘要（无明确最终结论）"
            lines += _collapsed(title, conclusion.text, fenced=not conclusion.is_final)
        if result.command:
            lines += _collapsed("执行命令", _command_skeleton(result.command))
        lines += _collapsed(f"下发给 {executor_label(item.executor)} 的提示词", build_prompt(item), opened=True)
        if result.stderr:
            lines += _collapsed("stderr", result.stderr)
    else:
        lines += _collapsed(f"下发给 {executor_label(item.executor)} 的提示词", build_prompt(item), opened=True)

    if item.error:
        lines += ["## 错误", "", f"`{item.error}`", ""]
    return "\n".join(lines)


def write_report(item: WorkItem, reports_dir: Path, parent_title: str = "") -> str:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{item.id}.md"
    path.write_text(render_report(item, parent_title), encoding="utf-8")
    return str(path)
