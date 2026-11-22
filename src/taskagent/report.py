"""Local Markdown reports — the only result sink in this phase."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .executor import build_prompt
from .models import WorkItem

# Raw agent stdout is a verbose event stream (often >100k chars, including the
# agent's own system prompt). Keep only conclusions in the report; the full
# stream stays on the WorkItem for the API.
_STDOUT_PREVIEW_LIMIT = 4000


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
        "",
    ]
    if item.description:
        lines += ["## 需求描述", "", item.description.rstrip(), ""]

    if result:
        lines += [
            "## 执行结果",
            "",
            "| 字段 | 值 |",
            "|---|---|",
            f"| 成功 | {'是' if result.ok else '否'} |",
            f"| dry-run | {'是' if result.dry_run else '否'} |",
            f"| 耗时 | {result.duration_s:.2f}s |",
            f"| 改动文件 | {len(result.changed_files)} 个 |",
            f"| worktree | {result.worktree or '—'} |",
            "",
        ]
        if result.error:
            lines += [f"> ⚠️ 执行错误：{result.error}", ""]
        if result.changed_files:
            lines += ["### 改动文件", ""] + [f"- `{name}`" for name in result.changed_files] + [""]

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
        lines += _collapsed("下发给 codebuddy 的提示词", build_prompt(item), opened=True)
        if result.stderr:
            lines += _collapsed("stderr", result.stderr)
    else:
        lines += _collapsed("下发给 codebuddy 的提示词", build_prompt(item), opened=True)

    if item.error:
        lines += ["## 错误", "", f"`{item.error}`", ""]
    return "\n".join(lines)


def write_report(item: WorkItem, reports_dir: Path, parent_title: str = "") -> str:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{item.id}.md"
    path.write_text(render_report(item, parent_title), encoding="utf-8")
    return str(path)
