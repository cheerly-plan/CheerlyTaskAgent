"""Token 运行账本：把每次模型/CLI 调用的用量记下来，并支持预算上限。

两条来源：

- **执行后端 CLI**：stdout 里的 ``usage``（codebuddy 的 snake_case、
  streamed 事件的 ``providerData.usage`` camelCase、DSH 的 NDJSON 结果都覆盖）；
- **后端 Agent LLM**：OpenAI 兼容响应里的 ``usage``（任务规格 / 评审 / 选仓）。

账本按天持久化到 ``data/usage.json``（任务维度也留一份，便于排查），预算是
**token 数**而不是金额——各家单价不同，先把量管住。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# OpenAI / codebuddy / DSH 三种命名都见得到；统一映射到 input/output。
_INPUT_KEYS = ("input_tokens", "inputTokens", "prompt_tokens", "promptTokens")
_OUTPUT_KEYS = ("output_tokens", "outputTokens", "completion_tokens", "completionTokens")
_TOTAL_KEYS = ("total_tokens", "totalTokens")


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, other: TokenUsage | None) -> TokenUsage:
        if other is None:
            return self
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.calls += other.calls
        return self

    def __bool__(self) -> bool:
        return bool(self.input_tokens or self.output_tokens or self.calls)

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "calls": self.calls,
        }

    @classmethod
    def from_dict(cls, data: Any) -> TokenUsage:
        if not isinstance(data, dict):
            return cls()
        return cls(
            input_tokens=_number(data.get("input_tokens")),
            output_tokens=_number(data.get("output_tokens")),
            calls=_number(data.get("calls")),
        )


def _number(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _pick(block: str, keys: tuple[str, ...]) -> int:
    for key in keys:
        match = re.search(rf'"{re.escape(key)}"\s*:\s*(\d+)', block)
        if match:
            return int(match.group(1))
    return 0


def _from_text(text: str) -> TokenUsage:
    """Largest usage block found in a CLI output stream.

    CLI 输出可能是单个 result、pretty-printed 事件数组或 NDJSON。每段 ``usage``
    通常是该次运行的累计值，取「总量最大」的一段可以避免把中间事件误当成最终值。
    """
    best = TokenUsage()
    blocks = re.findall(
        r'"(?:usage|tokenUsage|token_usage)"\s*:\s*\{([^{}]*)\}', text or "", re.DOTALL
    )
    for block in blocks:
        input_tokens = _pick(block, _INPUT_KEYS)
        output_tokens = _pick(block, _OUTPUT_KEYS)
        total = _pick(block, _TOTAL_KEYS)
        if total and not (input_tokens or output_tokens):
            # 只有 total 时按输出计，至少不丢总量。
            output_tokens = total
        usage = TokenUsage(input_tokens, output_tokens, calls=1)
        if usage.total_tokens > best.total_tokens:
            best = usage
    if best.total_tokens:
        return best
    # 没有 usage 包裹、但字段直接出现在顶层（少数 CLI 版本）。
    input_tokens = _pick(text, _INPUT_KEYS)
    output_tokens = _pick(text, _OUTPUT_KEYS)
    return TokenUsage(input_tokens, output_tokens, calls=1 if (input_tokens or output_tokens) else 0)


def extract_cli_usage(stdout: str) -> TokenUsage:
    """Best-effort token usage from one CLI run (zero when the CLI omits it)."""
    return _from_text(stdout)


def extract_llm_usage(payload: Any) -> TokenUsage:
    """Token usage from an OpenAI-compatible chat response."""
    if not isinstance(payload, dict):
        return TokenUsage()
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return TokenUsage()
    input_tokens = _pick(json.dumps(usage), _INPUT_KEYS)
    output_tokens = _pick(json.dumps(usage), _OUTPUT_KEYS)
    total = _pick(json.dumps(usage), _TOTAL_KEYS)
    if total and not (input_tokens or output_tokens):
        output_tokens = total
    return TokenUsage(
        input_tokens, output_tokens, calls=1 if (input_tokens or output_tokens) else 0
    )


class UsageLedger:
    """按天累计 token 用量，落盘 ``data/usage.json``（原子写）。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self._state = self._load()

    @staticmethod
    def _today() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _empty(self) -> dict[str, Any]:
        return {"date": self._today(), "total_tokens": 0, "tasks": {}}

    def _load(self) -> dict[str, Any]:
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self._empty()
        if not isinstance(state, dict) or state.get("date") != self._today():
            return self._empty()
        tasks = state.get("tasks")
        return {
            "date": self._today(),
            "total_tokens": _number(state.get("total_tokens")),
            "tasks": dict(tasks) if isinstance(tasks, dict) else {},
        }

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(self.path)
        except OSError:
            logger.warning("保存 token 账本失败：%s", self.path, exc_info=True)

    def _rollover(self) -> None:
        if self._state.get("date") != self._today():
            self._state = self._empty()

    def record(self, task_id: str, usage: TokenUsage) -> int:
        """Record one usage event; returns today's cumulative total."""
        self._rollover()
        if not usage:
            return self._state["total_tokens"]
        added = usage.total_tokens
        self._state["total_tokens"] += added
        if task_id:
            tasks = self._state.setdefault("tasks", {})
            tasks[task_id] = _number(tasks.get(task_id)) + added
        self._save()
        return self._state["total_tokens"]

    @property
    def total_today(self) -> int:
        self._rollover()
        return int(self._state.get("total_tokens") or 0)

    @property
    def date(self) -> str:
        self._rollover()
        return str(self._state.get("date") or "")

    def snapshot(self) -> dict[str, Any]:
        self._rollover()
        return {
            "date": self._state["date"],
            "total_tokens": self._state["total_tokens"],
            "tasks": dict(self._state.get("tasks") or {}),
        }