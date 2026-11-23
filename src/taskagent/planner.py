"""规划阶段：在多候选仓库时让 LLM 选一个仓库并给出执行方案。

只有「规则判不出来」的场景才调用 LLM（TAPD 项目绑定了多个本地仓库）。单候选
直接命中，零额外调用；调用失败就如实返回 ``None``，任务留在待人工映射，绝不
瞎猜一个仓库把代码改错地方。规划用的 CLI 调用是只读的（codebuddy `plan` 模式 /
DSH `read-only`），不会产生任何文件改动。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .models import ItemKind, ProjectMapping, WorkItem
from .report import extract_conclusion

_KIND_LABEL = {
    ItemKind.BUG: "缺陷修复",
    ItemKind.FEATURE: "新需求",
    ItemKind.TASK: "开发任务",
}


@dataclass
class Plan:
    repo: str
    reason: str = ""
    steps: list[str] = field(default_factory=list)
    source: str = "rule"  # rule | llm

    def to_prompt(self) -> str:
        if not self.steps:
            return ""
        return "\n".join(f"{i + 1}. {step}" for i, step in enumerate(self.steps))


def _extract_json_object(text: str) -> dict | None:
    """Pull one JSON object out of an LLM reply (fenced, raw, or embedded)."""
    for block in reversed(re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)):
        try:
            value = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            return value
    decoder = json.JSONDecoder()
    for match in reversed(list(re.finditer(r"\{", text))[-80:]):
        try:
            value, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _repo_brief(mapping: ProjectMapping, *, dirs: int = 12) -> str:
    """Cheap, local context about a candidate repo (no git, no network)."""
    bits = [f"- 名称：{mapping.name}", f"  路径：{mapping.path}"]
    if mapping.note:
        bits.append(f"  备注：{mapping.note}")
    path = Path(mapping.path).expanduser()
    if path.is_dir():
        try:
            names = sorted(
                entry.name for entry in os.scandir(path)
                if not entry.name.startswith(".") and entry.name not in {"node_modules", "dist", "build"}
            )
            if names:
                bits.append("  顶层目录：" + "、".join(names[:dirs]))
        except OSError:
            pass
        for readme in ("README.md", "README.rst", "README.txt", "readme.md"):
            candidate = path / readme
            if candidate.is_file():
                try:
                    text = candidate.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    break
                excerpt = " ".join(text.split())[:400]
                if excerpt:
                    bits.append(f"  README：{excerpt}")
                break
    return "\n".join(bits)


def build_plan_prompt(item: WorkItem, candidates: list[ProjectMapping]) -> str:
    repo_block = "\n".join(_repo_brief(c) for c in candidates)
    return (
        "你是需求分派助手。下面是一条开发任务，以及若干候选本地仓库。\n"
        "请判断这条任务应该在哪个仓库改代码，并给出 2-4 步执行方案。\n"
        "只输出一个 ```json 代码块，不要输出其他内容：\n"
        '{"repo":"候选仓库名称（必须与下面完全一致）","reason":"选择理由（一句话）",'
        '"steps":["步骤1","步骤2"]}\n\n'
        "--- 任务 ---\n"
        f"类型：{_KIND_LABEL.get(item.kind, item.kind.value)}\n"
        f"标题：{item.title}\n"
        f"描述：{item.description[:4000]}\n"
        f"来源：{item.source.value} {item.source_ref}\n"
        "--- 候选仓库 ---\n"
        f"{repo_block}\n"
        "--- 结束 ---\n"
        "判断依据：任务里的业务词、模块名、技术栈与仓库目录/README 的匹配度。"
        "如果都不匹配，repo 返回空字符串。\n"
    )


def parse_plan(raw: str, candidates: list[ProjectMapping]) -> Plan | None:
    if not raw:
        return None
    text = extract_conclusion(raw, limit=200_000).text or ""
    data = _extract_json_object(text)
    if not data:
        return None
    repo = str(data.get("repo") or "").strip()
    if not repo:
        return None
    by_name = {c.name.strip().lower(): c for c in candidates}
    match = by_name.get(repo.lower())
    if match is None:
        return None
    steps = data.get("steps")
    if isinstance(steps, str):
        steps = [steps]
    clean_steps = [str(s).strip() for s in (steps or []) if str(s).strip()][:8]
    return Plan(
        repo=match.name,
        reason=str(data.get("reason") or "").strip()[:500],
        steps=clean_steps,
        source="llm",
    )


async def plan_repo(
    item: WorkItem,
    candidates: list[ProjectMapping],
    *,
    backend,
    enabled: bool,
    timeout_s: int,
    max_candidates: int,
) -> Plan | None:
    """Pick a repo + steps for an unmapped item; ``None`` means "leave to human"."""
    if not candidates:
        return None
    if len(candidates) == 1:
        return Plan(repo=candidates[0].name, reason="该项目只关联了一个本地仓库", source="rule")
    if not enabled or backend is None:
        return None
    subset = candidates[: max(1, max_candidates)]
    prompt = build_plan_prompt(item, subset)
    try:
        raw = await backend.ask(prompt, timeout_s=timeout_s)
    except Exception:  # noqa: BLE001 - planning must never break the dispatcher
        return None
    return parse_plan(raw, subset)