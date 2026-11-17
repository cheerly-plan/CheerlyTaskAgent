"""Agent abstraction, offline mock executors and the registry.

Real generation happens in WorkBuddy via MCP tools. This module defines the
seam those tools will implement, plus two deterministic offline agents so the
whole scheduler can be exercised without keys or network.

A real adapter only has to satisfy :class:`Agent`; the scheduler never imports
a concrete vendor (same philosophy as CheerlyHarness's Provider seam).
"""

from __future__ import annotations

import asyncio
import hashlib
import html
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import Artifact, Task, TaskKind

ProgressFn = Callable[[float, str], None]
ShouldCancelFn = Callable[[], bool]


class AgentError(RuntimeError):
    """An agent failed. ``retryable`` tells the scheduler whether to back off."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class AgentResult:
    artifacts: list[Artifact] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


class Agent(ABC):
    id: str
    name: str
    description: str
    kinds: frozenset[TaskKind]

    @abstractmethod
    async def run(
        self,
        task: Task,
        *,
        progress: ProgressFn,
        should_cancel: ShouldCancelFn,
    ) -> AgentResult: ...

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "kinds": sorted(kind.value for kind in self.kinds),
            "offline": True,
        }


def compute_idempotency_key(task: Task) -> str:
    """Same prompt + kind + options = same work; never pay for it twice."""
    payload = f"{task.kind.value}|{task.prompt}|{sorted(task.options.items(), key=str)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


async def _simulate_work(task: Task, progress: ProgressFn, should_cancel: ShouldCancelFn) -> None:
    """Advance through a few stages with small sleeps, honoring cancellation."""
    delay = float(task.options.get("delay", 0.20))
    delay = max(0.0, min(delay, 5.0))
    stages = ["准备提示词", "生成中", "写入产物"]
    for index, stage in enumerate(stages):
        for step in range(1, 4):
            if should_cancel():
                raise asyncio.CancelledError()
            progress((index * 3 + step) / (len(stages) * 3), stage)
            await asyncio.sleep(delay / 3)
    progress(1.0, "完成")


def _fail_if_requested(task: Task) -> None:
    """Options-driven failure injection for demoing retry / failed states."""
    mode = task.options.get("simulate_fail")
    if not mode:
        return
    if isinstance(mode, bool) and mode:
        raise AgentError("模拟失败：agent 按 options 配置主动失败", retryable=True)
    if isinstance(mode, (int, float)) and task.attempt <= int(mode):
        raise AgentError(
            f"模拟失败：第 {task.attempt} 次尝试失败（共 {int(mode)} 次）", retryable=True
        )


def _write_placeholder(task: Task, artifacts_dir: Path, label: str) -> Artifact:
    """Write a viewable SVG placeholder so artifacts are previewable offline.

    A real WorkBuddy agent replaces this with the MCP tool's returned file.
    """
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{task.id}-{label}.svg"
    path = artifacts_dir / filename
    safe_prompt = html.escape(task.prompt[:120])
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" '
        'viewBox="0 0 640 360">'
        '<rect width="640" height="360" fill="#EEEDFE"/>'
        f'<text x="32" y="70" font-family="sans-serif" font-size="22" fill="#26215C">'
        f"{label} · 模拟产物</text>"
        f'<text x="32" y="150" font-family="sans-serif" font-size="18" fill="#534AB7">'
        f"{safe_prompt}</text>"
        '<text x="32" y="320" font-family="sans-serif" font-size="13" fill="#7F77DD">'
        '接入 WorkBuddy MCP 工具后这里会是真实生成结果</text>'
        "</svg>"
    )
    path.write_text(svg, encoding="utf-8")
    return Artifact(
        filename=filename,
        path=str(path),
        mime="image/svg+xml",
        meta={"simulated": True, "kind": task.kind.value},
    )


class MockImageAgent(Agent):
    id = "mock-image"
    name = "模拟生图 agent"
    description = "离线生图：产出可预览的占位图，支持进度/延迟/模拟失败。"
    kinds = frozenset({TaskKind.IMAGE})

    def __init__(self, artifacts_dir: Path) -> None:
        self.artifacts_dir = artifacts_dir

    async def run(
        self, task: Task, *, progress: ProgressFn, should_cancel: ShouldCancelFn
    ) -> AgentResult:
        _fail_if_requested(task)
        await _simulate_work(task, progress, should_cancel)
        artifact = _write_placeholder(task, self.artifacts_dir, "image")
        return AgentResult(artifacts=[artifact], meta={"upstream": "mock-image"})


class MockVideoAgent(Agent):
    id = "mock-video"
    name = "模拟生视频 agent"
    description = "离线生视频：产出可预览的封面占位图，支持进度/延迟/模拟失败。"
    kinds = frozenset({TaskKind.VIDEO})

    def __init__(self, artifacts_dir: Path) -> None:
        self.artifacts_dir = artifacts_dir

    async def run(
        self, task: Task, *, progress: ProgressFn, should_cancel: ShouldCancelFn
    ) -> AgentResult:
        _fail_if_requested(task)
        await _simulate_work(task, progress, should_cancel)
        artifact = _write_placeholder(task, self.artifacts_dir, "video")
        meta = {"upstream": "mock-video"}
        if task.upstream_task_id:
            meta["resumed_from"] = task.upstream_task_id
        return AgentResult(artifacts=[artifact], meta=meta)


class AgentRegistry:
    """Capability-based lookup so 'choose an agent' only offers valid ones."""

    def __init__(self, agents: list[Agent] | None = None) -> None:
        self._agents: dict[str, Agent] = {agent.id: agent for agent in agents or []}

    def register(self, agent: Agent) -> None:
        self._agents[agent.id] = agent

    def get(self, agent_id: str) -> Agent | None:
        return self._agents.get(agent_id)

    def list(self) -> list[Agent]:
        return sorted(self._agents.values(), key=lambda item: item.id)

    def for_kind(self, kind: TaskKind) -> list[Agent]:
        return [agent for agent in self.list() if kind in agent.kinds]

    def describe(self) -> list[dict[str, Any]]:
        return [agent.describe() for agent in self.list()]


def build_default_registry(data_dir: Path) -> AgentRegistry:
    artifacts_dir = data_dir / "artifacts"
    return AgentRegistry([MockImageAgent(artifacts_dir), MockVideoAgent(artifacts_dir)])


# ── Seam for the real thing ────────────────────────────────────────────────────
# class WorkBuddyMcpAgent(Agent):
#     """Future adapter: maps a Task to an mcp__miora__* tool call.
#
#     run() should: choose the tool by asset/kind (text->image, reference->video,
#     frame->video ...), call the MCP tool, save the returned file as an Artifact,
#     and store the remote handle in task.upstream_task_id so a timeout can be
#     queried instead of re-submitted. Requires a WorkBuddy-side executor that
#     pulls tasks over HTTP (see docs/architecture.md, section 5).
#     """
