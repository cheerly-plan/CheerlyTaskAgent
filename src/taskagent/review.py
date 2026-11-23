"""执行结果的结构化评审：让执行任务的 LLM 顺手回答「改了什么、有没有影响、能不能合」。

为什么不再起一次 CLI：自动链路本身就要跑一次长任务（140–500s），再为评审加一次
只读调用会让墙钟时间几乎翻倍。这里改为在**同一个执行提示词**末尾要求模型输出
一个固定 JSON 块，执行器照常返回 stdout；评审只做**解析 + 兜底**，零额外调用。

解析失败的兜底必须是诚实的：标 `review_source="rule"`、`impact_level="unknown"`，
只用确定性的改动清单说话，绝不编造「无影响」。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .models import WorkItem
from .report import extract_conclusion

IMPACT_LEVELS = ("none", "low", "medium", "high", "unknown")

REVIEW_INSTRUCTION = (
    "5. 改完后，在最终回复的最后追加一个 ```json 代码块（且只放这一个对象，不要夹带其他文字）：\n"
    '{"summary":"一句话说明本次改了什么",'
    '"changes":["按文件的改动要点，1-3 条"],'
    '"impact":{"level":"none|low|medium|high","areas":["受影响的模块/功能"],'
    '"notes":"对现有功能的影响说明；没有影响也要写明依据"},'
    '"can_merge":true,"merge_blockers":[],"confidence":0.0}\n'
    "   - can_merge 表示这些改动能否安全合并进主分支工作区；不确定时给 false，"
    "并把原因写进 merge_blockers。\n"
    "   - 不要修改这个 JSON 块的字段名。\n"
)


def review_instruction() -> str:
    return REVIEW_INSTRUCTION


def _tail(text: str, limit: int = 200_000) -> str:
    return text if len(text) <= limit else text[-limit:]


def _iter_fenced_json(text: str):
    """Yield JSON payloads from ```json fences, last fence first."""
    fences = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    for block in reversed(fences):
        block = block.strip()
        if block:
            yield block


def _iter_braced_json(text: str):
    """Yield decodable JSON objects found by scanning from the end.

    Handles agents that drop the fence and mix the object into prose. Scanning
    from the end keeps the *final* verdict when earlier narration also has JSON.
    """
    decoder = json.JSONDecoder()
    positions = [m.start() for m in re.finditer(r"\{", text)]
    for pos in reversed(positions[-80:]):
        try:
            value, _ = decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def _iter_objects(text: str):
    """Yield every decodable JSON object in ``text`` (fences first, last-to-first)."""
    for block in _iter_fenced_json(text):
        try:
            value = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            yield value
    yield from _iter_braced_json(_tail(text))


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Public helper for other LLM prompts that ask for one JSON object."""
    for value in _iter_objects(text):
        return value
    return None


def parse_review(raw: str) -> dict[str, Any] | None:
    """Best-effort extraction of the review object from agent stdout."""
    if not raw:
        return None
    text = extract_conclusion(raw, limit=400_000).text or ""
    for value in _iter_objects(text):
        if _looks_like_review(value):
            return value
        # Some agents wrap the verdict: {"review": {...}} / {"result": {...}}.
        for key in ("review", "result", "output", "data"):
            inner = value.get(key)
            if isinstance(inner, dict) and _looks_like_review(inner):
                return inner
    return None


def _looks_like_review(value: dict[str, Any]) -> bool:
    keys = set(value)
    return bool(keys & {"can_merge", "merge_blockers"}) or (
        "summary" in keys and "impact" in keys
    )


def normalise_review(data: dict[str, Any]) -> dict[str, Any]:
    """Coerce the agent's JSON into the fixed shape the todo page renders."""
    impact = data.get("impact")
    if not isinstance(impact, dict):
        impact = {}
    level = str(impact.get("level") or data.get("impact_level") or "unknown").strip().lower()
    if level not in IMPACT_LEVELS:
        level = "unknown"

    def _str_list(value: Any) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        return [str(v).strip() for v in value if str(v).strip()][:20]

    try:
        confidence = float(data.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    return {
        "summary": str(data.get("summary") or "").strip()[:2000],
        "changes": _str_list(data.get("changes")),
        "impact_level": level,
        "impact_areas": _str_list(impact.get("areas")),
        "impact_notes": str(impact.get("notes") or "").strip()[:2000],
        "can_merge": bool(data.get("can_merge", True)),
        "blockers": _str_list(data.get("merge_blockers") or data.get("blockers")),
        "confidence": max(0.0, min(1.0, confidence)),
        "source": "llm",
    }


def fallback_review(changed: list[str], *, error: str = "") -> dict[str, Any]:
    """Deterministic review when the agent gave no parseable verdict."""
    n = len(changed)
    preview = "、".join(changed[:6]) + ("…" if n > 6 else "") if changed else "（无改动文件）"
    return {
        "summary": f"改动 {n} 个文件：{preview}" + ("（未能取得模型结构化评估，请人工确认）" if not error else f"（评审失败：{error}）"),
        "changes": [],
        "impact_level": "unknown",
        "impact_areas": [],
        "impact_notes": "未取得模型评估，无法判断对现有功能的影响；合并前请查看 diff。",
        "can_merge": bool(changed),
        "blockers": [] if changed else ["没有产生任何改动"],
        "confidence": 0.0,
        "source": "rule",
    }


def build_review(raw: str, changed: list[str]) -> dict[str, Any]:
    parsed = parse_review(raw)
    if parsed is None:
        return fallback_review(changed)
    return normalise_review(parsed)


# ── diff：给待办页与人工评审看 ────────────────────────────────────────────────

def _git(worktree: Path, *args: str, limit: int = 8_000_000) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(worktree), "--no-pager", *args],
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    out = proc.stdout.decode("utf-8", errors="replace")
    return out[:limit]


def diff_stat(worktree: str | Path, changed: list[str]) -> dict[str, int]:
    """Added/deleted line counts for the agent's changes (HEAD..worktree)."""
    path = Path(worktree).expanduser()
    if not path.is_dir():
        return {"files": len(changed), "added": 0, "removed": 0}
    added = removed = 0
    numstat = _git(path, "diff", "--numstat", "HEAD")
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        a, d = parts[0], parts[1]
        if a.isdigit():
            added += int(a)
        if d.isdigit():
            removed += int(d)
    # Untracked files never appear in `git diff HEAD`; count their lines.
    tracked = {line.split("\t")[2] for line in numstat.splitlines() if len(line.split("\t")) >= 3}
    for rel in changed:
        if rel in tracked:
            continue
        target = path / rel
        if target.is_file():
            try:
                added += sum(1 for _ in target.open("rb"))
            except OSError:
                pass
    return {"files": len(changed), "added": added, "removed": removed}


def build_diff(worktree: str | Path, changed: list[str], limit: int = 200_000) -> str:
    """Unified diff for the todo detail view; truncated by bytes, not lines."""
    path = Path(worktree).expanduser()
    if not path.is_dir():
        return ""
    parts: list[str] = []
    tracked_diff = _git(path, "diff", "HEAD", "--no-color", "--", *changed) if changed else ""
    if tracked_diff:
        parts.append(tracked_diff)
    # New files are invisible to `git diff HEAD` until intent-to-add; show them
    # explicitly instead of mutating the worktree.
    for rel in changed:
        if f"+++ b/{rel}" in tracked_diff:
            continue
        target = path / rel
        if not target.is_file():
            continue
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = content.splitlines()
        body = "\n".join(f"+{line}" for line in lines[:400])
        parts.append(f"diff --git a/{rel} b/{rel}\nnew file\n--- /dev/null\n+++ b/{rel}\n{body}\n")
    text = "\n".join(parts)
    if len(text) > limit:
        text = text[:limit] + f"\n…（diff 共 {len(text)} 字符，已截断）\n"
    return text


def review_fields(item: WorkItem, review: dict[str, Any]) -> dict[str, Any]:
    """Map a normalised review onto TodoEntry constructor fields."""
    return {
        "summary": review.get("summary", ""),
        "changes": list(review.get("changes") or []),
        "impact_level": review.get("impact_level", "unknown"),
        "impact_areas": list(review.get("impact_areas") or []),
        "impact_notes": review.get("impact_notes", ""),
        "can_merge": bool(review.get("can_merge", True)),
        "blockers": list(review.get("blockers") or []),
        "confidence": float(review.get("confidence", 0.0) or 0.0),
        "review_source": review.get("source", "rule"),
        "review_raw": json.dumps(review, ensure_ascii=False),
        "changed_files": list(item.result.changed_files if item.result else []),
    }