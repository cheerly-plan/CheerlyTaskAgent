"""「我的待办」：自动执行成功后的合并评审队列。

自动链路（`WorkItem.auto=True`）在隔离 worktree 里跑完 apply 后**不会**同步回
主仓库工作区（`hold_merge=True`）。每个成功结果生成一条 :class:`TodoEntry`，
记录改动清单、diff 统计、LLM 汇总与功能影响评估，并给出「能不能合」的建议。
人工在待办页点「合并」时，才复用执行器既有的三方安全同步写回主仓（仍不提交）。

一个「批次」（:class:`BatchSummary`）对应一轮连续自动出队：引擎在开始派发时
建批次，队列清空时收口并生成汇总，供「一系列任务跑完之后」集中评审。
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# 待办状态：只描述「合并评审」这一维度，与 WorkItem.status 独立。
TODO_PENDING = "pending"
TODO_MERGED = "merged"
TODO_DISCARDED = "discarded"
TODO_BLOCKED = "blocked"      # 合并失败（冲突/路径缺失），需要人工处理后重试


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _new_id(prefix: str = "todo") -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"


@dataclass
class TodoEntry:
    """一条待合并的自动执行结果。"""

    id: str
    item_id: str
    batch_id: str = ""
    title: str = ""
    repo: str = ""
    repo_path: str = ""
    status: str = TODO_PENDING
    # 人工评审需要的四件事：汇总、改了哪些、影响、能不能合。
    summary: str = ""
    changes: list[str] = field(default_factory=list)
    impact_level: str = "unknown"      # none | low | medium | high | unknown
    impact_areas: list[str] = field(default_factory=list)
    impact_notes: str = ""
    can_merge: bool = True
    blockers: list[str] = field(default_factory=list)
    confidence: float = 0.0
    changed_files: list[str] = field(default_factory=list)
    diff_stat: dict[str, int] = field(default_factory=dict)
    review_source: str = "rule"        # llm | rule
    review_raw: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    merged_at: str = ""
    merge: dict[str, Any] = field(default_factory=dict)

    def touch(self) -> None:
        self.updated_at = _now()

    def summary_row(self) -> dict[str, Any]:
        """列表视图用的轻量字段（不含 diff / 原始评审文本）。"""
        return {
            "id": self.id,
            "item_id": self.item_id,
            "batch_id": self.batch_id,
            "title": self.title,
            "repo": self.repo,
            "repo_path": self.repo_path,
            "status": self.status,
            "summary": self.summary,
            "impact_level": self.impact_level,
            "impact_areas": list(self.impact_areas),
            "impact_notes": self.impact_notes,
            "can_merge": self.can_merge,
            "blockers": list(self.blockers),
            "confidence": self.confidence,
            "changed_files": list(self.changed_files),
            "diff_stat": dict(self.diff_stat),
            "review_source": self.review_source,
            "created_at": self.created_at,
            "merged_at": self.merged_at,
            "merge": dict(self.merge),
        }

    def to_dict(self) -> dict[str, Any]:
        data = self.summary_row()
        data.update({
            "changes": list(self.changes),
            "review_raw": self.review_raw,
            "updated_at": self.updated_at,
        })
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TodoEntry:
        return cls(
            id=str(data["id"]),
            item_id=str(data.get("item_id", "")),
            batch_id=str(data.get("batch_id", "")),
            title=str(data.get("title", "")),
            repo=str(data.get("repo", "")),
            repo_path=str(data.get("repo_path", "")),
            status=str(data.get("status", TODO_PENDING)),
            summary=str(data.get("summary", "")),
            changes=[str(c) for c in (data.get("changes") or []) if str(c).strip()],
            impact_level=str(data.get("impact_level", "unknown") or "unknown"),
            impact_areas=[str(a) for a in (data.get("impact_areas") or []) if str(a).strip()],
            impact_notes=str(data.get("impact_notes", "")),
            can_merge=bool(data.get("can_merge", True)),
            blockers=[str(b) for b in (data.get("blockers") or []) if str(b).strip()],
            confidence=float(data.get("confidence", 0.0) or 0.0),
            changed_files=[str(f) for f in (data.get("changed_files") or []) if str(f).strip()],
            diff_stat={str(k): int(v) for k, v in (data.get("diff_stat") or {}).items()},
            review_source=str(data.get("review_source", "rule") or "rule"),
            review_raw=str(data.get("review_raw", "")),
            created_at=str(data.get("created_at", "") or _now()),
            updated_at=str(data.get("updated_at", "") or _now()),
            merged_at=str(data.get("merged_at", "")),
            merge=dict(data.get("merge") or {}),
        )


@dataclass
class BatchSummary:
    """一轮自动执行的收口汇总（「一系列任务跑完之后生成待办数据」）。"""

    id: str
    started_at: str = field(default_factory=_now)
    finished_at: str = ""
    item_ids: list[str] = field(default_factory=list)
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    todo_count: int = 0
    mergeable: int = 0
    high_impact: int = 0
    repos: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "item_ids": list(self.item_ids),
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "todo_count": self.todo_count,
            "mergeable": self.mergeable,
            "high_impact": self.high_impact,
            "repos": list(self.repos),
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BatchSummary:
        return cls(
            id=str(data["id"]),
            started_at=str(data.get("started_at", "") or _now()),
            finished_at=str(data.get("finished_at", "")),
            item_ids=[str(i) for i in (data.get("item_ids") or [])],
            total=int(data.get("total", 0) or 0),
            succeeded=int(data.get("succeeded", 0) or 0),
            failed=int(data.get("failed", 0) or 0),
            todo_count=int(data.get("todo_count", 0) or 0),
            mergeable=int(data.get("mergeable", 0) or 0),
            high_impact=int(data.get("high_impact", 0) or 0),
            repos=[str(r) for r in (data.get("repos") or [])],
            summary=str(data.get("summary", "")),
        )


class TodoStore:
    """待办与批次的一文件一 JSON 存储（原子写，损坏文件跳过）。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.batches_dir = self.root / "batches"
        self.root.mkdir(parents=True, exist_ok=True)
        self.batches_dir.mkdir(parents=True, exist_ok=True)

    # ── todos ────────────────────────────────────────────────────────────────
    def save(self, todo: TodoEntry) -> None:
        todo.touch()
        path = self.root / f"{todo.id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(todo.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def get(self, todo_id: str) -> TodoEntry | None:
        path = self.root / f"{todo_id}.json"
        if not path.is_file():
            return None
        try:
            return TodoEntry.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError):
            return None

    def list(self, status: str | None = None, limit: int = 500) -> list[TodoEntry]:
        todos: list[TodoEntry] = []
        for path in self.root.glob("todo-*.json"):
            try:
                todo = TodoEntry.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, KeyError):
                continue
            if status and todo.status != status:
                continue
            todos.append(todo)
        todos.sort(key=lambda t: t.created_at, reverse=True)
        return todos[: max(1, limit)]

    def find_by_item(self, item_id: str) -> TodoEntry | None:
        for todo in self.list(limit=5000):
            if todo.item_id == item_id:
                return todo
        return None

    def delete(self, todo_id: str) -> bool:
        """Remove a todo record (used when its work item is deleted)."""
        path = self.root / f"{todo_id}.json"
        if not path.is_file():
            return False
        try:
            path.unlink()
        except OSError:
            return False
        return True

    def counts(self) -> dict[str, int]:
        result = {TODO_PENDING: 0, TODO_MERGED: 0, TODO_DISCARDED: 0, TODO_BLOCKED: 0}
        for todo in self.list(limit=100000):
            result[todo.status] = result.get(todo.status, 0) + 1
        return result

    # ── batches ──────────────────────────────────────────────────────────────
    def save_batch(self, batch: BatchSummary) -> None:
        path = self.batches_dir / f"{batch.id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(batch.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def get_batch(self, batch_id: str) -> BatchSummary | None:
        path = self.batches_dir / f"{batch_id}.json"
        if not path.is_file():
            return None
        try:
            return BatchSummary.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError):
            return None

    def closed_batch_ids(self) -> set[str]:
        """已收口批次的 id，供调度循环 O(1) 判断批次是否还开着。"""
        return {path.stem for path in self.batches_dir.glob("batch-*.json")}

    def list_batches(self, limit: int = 20) -> list[BatchSummary]:
        batches: list[BatchSummary] = []
        for path in self.batches_dir.glob("batch-*.json"):
            try:
                batches.append(BatchSummary.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError, KeyError):
                continue
        batches.sort(key=lambda b: b.started_at, reverse=True)
        return batches[: max(1, limit)]

    @staticmethod
    def new_batch_id() -> str:
        return _new_id("batch")

    @staticmethod
    def new_todo_id() -> str:
        return _new_id("todo")