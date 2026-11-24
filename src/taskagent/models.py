"""Work item model for the email/TAPD -> local CLI agent pipeline.

A WorkItem is one dev requirement or bug harvested from a source (email, TAPD,
manual). The scheduler maps it to a local repo by git URL, then dispatches a
CLI agent (codebuddy) to fix/implement it. Everything on the wire is here.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _new_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"work-{stamp}-{secrets.token_hex(2)}"


class ItemKind(str, Enum):
    BUG = "bug"
    FEATURE = "feature"
    TASK = "task"


class ItemStatus(str, Enum):
    DISCOVERED = "discovered"   # imported from a source, not yet mapped
    READY = "ready"             # mapped to a local repo, ready to run
    QUEUED = "queued"           # admitted, waiting for an executor slot
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"         # duplicate / unmapped
    CANCELLED = "cancelled"


TERMINAL_STATUSES = {
    ItemStatus.SUCCEEDED,
    ItemStatus.FAILED,
    ItemStatus.SKIPPED,
    ItemStatus.CANCELLED,
}

# 标记「来源已关闭而自动归档」的工单，用于来源重新活跃（如 reopened）时识别并复活。
ARCHIVED_SOURCE_STAGE = "来源已关闭，仅归档供历史查看"

# 父需求容器：只用于任务中心的树形分组，不映射仓库、不进执行队列。
CONTAINER_STAGE = "父需求容器（用于分组，不进入执行队列）"


class SourceType(str, Enum):
    EMAIL = "email"
    TAPD = "tapd"
    MANUAL = "manual"


@dataclass
class LocalRepo:
    """A local git checkout resolved from an origin URL."""

    path: str
    origin: str
    name: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "origin": self.origin, "name": self.name}


@dataclass
class ProjectMapping:
    """An explicit ``project name / aliases -> local folder`` mapping.

    Unlike git-scan-derived LocalRepo, this is user-maintained: any alias that
    appears in an email title/body resolves to ``path`` directly, without
    relying on git origin. Stored as a JSON list, editable from the UI.
    """

    id: str
    name: str                       # canonical display name, e.g. "CheerlyAIChat"
    aliases: list[str] = field(default_factory=list)  # text tokens that trigger a match
    path: str = ""                  # absolute local folder
    enabled: bool = True
    note: str = ""
    tapd_workspace_ids: list[str] = field(default_factory=list)  # 关联的 TAPD 项目（一个项目可关联多个仓库）
    tapd_workspace_id: str = ""     # 兼容旧字段：取 tapd_workspace_ids 第一个
    # 交付策略覆盖：""=跟随全局，off/commit/push/pr（见 delivery.py）。
    delivery: str = ""
    # 合并策略覆盖：""=跟随全局，manual/auto（CI 通过后是否自动合并 PR）。
    merge_policy: str = ""

    def __post_init__(self) -> None:
        if not self.tapd_workspace_ids and self.tapd_workspace_id:
            self.tapd_workspace_ids = [self.tapd_workspace_id]
        if not self.tapd_workspace_id and self.tapd_workspace_ids:
            self.tapd_workspace_id = self.tapd_workspace_ids[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "aliases": list(self.aliases),
            "path": self.path,
            "enabled": self.enabled,
            "note": self.note,
            "tapd_workspace_ids": list(self.tapd_workspace_ids),
            "tapd_workspace_id": self.tapd_workspace_ids[0] if self.tapd_workspace_ids else "",
            "delivery": self.delivery,
            "merge_policy": self.merge_policy,
        }

    def to_local_repo(self) -> LocalRepo:
        """View this mapping as a LocalRepo for the executor/WorkItem."""
        return LocalRepo(path=self.path, origin="", name=self.name)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectMapping:
        aliases = data.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [a for a in (part.strip() for part in aliases.split(",")) if a]
        raw_ids = data.get("tapd_workspace_ids") or data.get("tapd_workspace_id") or []
        if isinstance(raw_ids, str):
            raw_ids = [part for part in raw_ids.replace("，", ",").split(",") if part.strip()]
        ids = list(dict.fromkeys(str(item).strip() for item in raw_ids if str(item).strip()))
        return cls(
            id=str(data.get("id") or _new_id()),
            name=str(data.get("name", "")).strip(),
            aliases=[str(a).strip() for a in aliases if str(a).strip()],
            path=str(data.get("path", "")).strip(),
            enabled=bool(data.get("enabled", True)),
            note=str(data.get("note", "")),
            tapd_workspace_ids=ids,
            delivery=str(data.get("delivery") or "").strip().lower(),
            merge_policy=str(data.get("merge_policy") or "").strip().lower(),
        )


@dataclass
class RunResult:
    """Outcome of one CLI-agent execution."""

    ok: bool
    dry_run: bool
    command: str
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    changed_files: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    worktree: str | None = None
    # Optional auto-sync of apply changes back into the main repo worktree.
    # All fields are optional so older persisted results still deserialize
    # via ``RunResult(**result_data)``.
    synced_files: list[str] = field(default_factory=list)
    sync_deleted: list[str] = field(default_factory=list)
    sync_skipped: list[str] = field(default_factory=list)
    sync_conflicts: list[str] = field(default_factory=list)
    sync_backup_dir: str | None = None
    sync_error: str | None = None
    # Post-execution verification gate verdict (see ``verification.py``).
    # Empty for dry-run / legacy results; every apply (manual or auto) records it.
    verification: dict[str, Any] = field(default_factory=dict)
    # Excerpt of the worktree diff captured right after the agent ran. Kept on
    # the result because the worktree is deleted on a successful merge, while
    # the TAPD comment still needs to show *what* changed.
    diff: str = ""
    # Number of agent runs in this execution (1 = single-shot, >1 = the
    # feedback loop retried after verification/review asked for changes).
    rounds: int = 0
    # Token usage of the last CLI run (input/output/total/calls).
    usage: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "dry_run": self.dry_run,
            "command": self.command,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
            "changed_files": self.changed_files,
            "duration_s": round(self.duration_s, 2),
            "worktree": self.worktree,
            "synced_files": self.synced_files,
            "sync_deleted": self.sync_deleted,
            "sync_skipped": self.sync_skipped,
            "sync_conflicts": self.sync_conflicts,
            "sync_backup_dir": self.sync_backup_dir,
            "sync_error": self.sync_error,
            "verification": self.verification,
            "diff": self.diff,
            "rounds": self.rounds,
            "usage": self.usage,
        }


@dataclass
class CommitResult:
    """Outcome of an optional, filtered git commit of a worktree's changes."""

    ok: bool
    commit: str = ""                       # short hash
    branch: str = ""
    message: str = ""
    kept: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    skipped_reasons: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "commit": self.commit,
            "branch": self.branch,
            "message": self.message,
            "kept": list(self.kept),
            "skipped": list(self.skipped),
            "skipped_reasons": dict(self.skipped_reasons),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommitResult:
        return cls(
            ok=bool(data.get("ok", False)),
            commit=str(data.get("commit", "")),
            branch=str(data.get("branch", "")),
            message=str(data.get("message", "")),
            kept=list(data.get("kept") or []),
            skipped=list(data.get("skipped") or []),
            skipped_reasons=dict(data.get("skipped_reasons") or {}),
            error=data.get("error"),
        )


@dataclass
class WorkItem:
    id: str
    title: str
    description: str
    kind: ItemKind
    source: SourceType
    source_ref: str = ""            # email message id / TAPD story id / ""
    tapd_workspace_id: str = ""     # TAPD 项目 ID（用于按绑定自动映射仓库）
    git_url: str = ""               # git URL hint extracted from the source
    # 父子需求：parent_ref 指向父的 source_ref（tapd:<type>:<id>）；parent_ref 为空即顶层。
    parent_ref: str = ""
    parent_title: str = ""          # 父需求标题快照：父不在本地时也能显示归属
    children_ids: list[str] = field(default_factory=list)  # 子需求的 source_ref 列表
    is_container: bool = False      # True = 父容器节点，不进执行队列
    repo: LocalRepo | None = None   # resolved local checkout
    status: ItemStatus = ItemStatus.DISCOVERED
    dedupe_key: str = ""
    exec_mode: str = "dry-run"      # dry-run | apply
    executor: str = "codebuddy"    # execution backend id; None/legacy -> codebuddy
    model: str | None = None        # per-task model; None = backend default
    attempt: int = 0
    max_attempts: int = 2
    stage: str = ""
    error: str | None = None
    result: RunResult | None = None
    commit: CommitResult | None = None
    report_path: str = ""
    # ── 自动执行 / 待办评审 ────────────────────────────────────────────────
    # 自动链路（非人工点击）派发的任务：auto=True、approved_by="auto"。
    auto: bool = False
    approved_by: str = ""            # user | auto（谁批准的 apply）
    auto_dispatched_at: str = ""     # 已自动派发时间；非空即不再重复出队
    # True = 执行器不自行同步，由 scheduler 在任务结束后做**原子合并**
    hold_merge: bool = False
    merge_state: str = ""            # "" | "merged" | "blocked"（自动合并结果）
    plan: str = ""                   # LLM/规则规划出的执行步骤，注入提示词
    parent_context: str = ""         # 父需求标题+描述快照，执行时注入子任务提示词
    spec: dict[str, Any] = field(default_factory=dict)  # TaskSpec（目标/验收/约束/风险）
    feedback: str = ""               # 上一轮验证/评审未通过的原因，注入下一轮提示词
    # 本次执行的 token 账本（引擎维度聚合）：{cli:{...}, llm:{...}, total_tokens, calls}
    usage: dict[str, Any] = field(default_factory=dict)
    # 交付结果：mode/branch/commit/pushed/pr_url/error（见 delivery.py）
    delivery: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None

    @classmethod
    def create(
        cls,
        *,
        title: str,
        description: str,
        kind: str = ItemKind.TASK.value,
        source: str = SourceType.MANUAL.value,
        source_ref: str = "",
        git_url: str = "",
        dedupe_key: str = "",
        exec_mode: str = "dry-run",
        executor: str = "codebuddy",
        model: str | None = None,
        parent_ref: str = "",
        parent_title: str = "",
        children_ids: list[str] | None = None,
        is_container: bool = False,
    ) -> WorkItem:
        return cls(
            id=_new_id(),
            title=title.strip(),
            description=description.strip(),
            kind=ItemKind(kind),
            source=SourceType(source),
            source_ref=source_ref,
            git_url=git_url.strip(),
            parent_ref=parent_ref.strip(),
            parent_title=parent_title.strip(),
            children_ids=list(children_ids or []),
            is_container=bool(is_container),
            dedupe_key=dedupe_key or source_ref or _new_id(),
            exec_mode=exec_mode if exec_mode in {"dry-run", "apply"} else "dry-run",
            executor=executor or "codebuddy",
            model=model or None,
        )

    def touch(self) -> None:
        self.updated_at = _now()

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def is_dry_run_done(self) -> bool:
        """A successful dry-run only built the prompt; it can still be applied."""
        return (
            self.status == ItemStatus.SUCCEEDED
            and self.result is not None
            and self.result.dry_run
        )

    def is_retryable(self) -> bool:
        """Failed/cancelled items can be re-armed and run again.

        `skipped` (duplicate/unmapped) stays terminal — re-running it has no
        meaning. `succeeded` apply items also stay terminal.
        """
        return self.status in (ItemStatus.FAILED, ItemStatus.CANCELLED)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind.value,
            "source": self.source.value,
            "source_ref": self.source_ref,
            "tapd_workspace_id": self.tapd_workspace_id,
            "git_url": self.git_url,
            "parent_ref": self.parent_ref,
            "parent_title": self.parent_title,
            "children_ids": list(self.children_ids),
            "is_container": self.is_container,
            "repo": self.repo.name if self.repo else None,
            "status": self.status.value,
            "exec_mode": self.exec_mode,
            "executor": self.executor,
            "model": self.model,
            "attempt": self.attempt,
            "stage": self.stage,
            "error": self.error,
            "commit": self.commit.to_dict() if self.commit else None,
            "report_path": self.report_path,
            "auto": self.auto,
            "approved_by": self.approved_by,
            "hold_merge": self.hold_merge,
            "merge_state": self.merge_state,
            "created_at": self.created_at,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "kind": self.kind.value,
            "source": self.source.value,
            "source_ref": self.source_ref,
            "tapd_workspace_id": self.tapd_workspace_id,
            "git_url": self.git_url,
            "parent_ref": self.parent_ref,
            "parent_title": self.parent_title,
            "children_ids": list(self.children_ids),
            "is_container": self.is_container,
            "repo": self.repo.to_dict() if self.repo else None,
            "status": self.status.value,
            "dedupe_key": self.dedupe_key,
            "exec_mode": self.exec_mode,
            "executor": self.executor,
            "model": self.model,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "stage": self.stage,
            "error": self.error,
            "result": self.result.to_dict() if self.result else None,
            "commit": self.commit.to_dict() if self.commit else None,
            "report_path": self.report_path,
            "auto": self.auto,
            "approved_by": self.approved_by,
            "auto_dispatched_at": self.auto_dispatched_at,
            "hold_merge": self.hold_merge,
            "merge_state": self.merge_state,
            "plan": self.plan,
            "parent_context": self.parent_context,
            "spec": dict(self.spec),
            "feedback": self.feedback,
            "usage": dict(self.usage),
            "delivery": dict(self.delivery),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkItem:
        repo_data = data.get("repo")
        repo = LocalRepo(**repo_data) if repo_data else None
        result_data = data.get("result")
        result = RunResult(**result_data) if result_data else None
        commit_data = data.get("commit")
        commit = CommitResult.from_dict(commit_data) if commit_data else None
        return cls(
            id=data["id"],
            title=data.get("title", ""),
            description=data.get("description", ""),
            kind=ItemKind(data.get("kind", ItemKind.TASK.value)),
            source=SourceType(data.get("source", SourceType.MANUAL.value)),
            source_ref=data.get("source_ref", ""),
            tapd_workspace_id=str(data.get("tapd_workspace_id", "") or ""),
            git_url=data.get("git_url", ""),
            parent_ref=data.get("parent_ref", "") or "",
            parent_title=str(data.get("parent_title", "") or ""),
            children_ids=[
                str(c).strip() for c in (data.get("children_ids") or []) if str(c).strip()
            ],
            is_container=bool(data.get("is_container", False)),
            repo=repo,
            status=ItemStatus(data.get("status", ItemStatus.DISCOVERED.value)),
            dedupe_key=data.get("dedupe_key", ""),
            exec_mode=data.get("exec_mode", "dry-run"),
            executor=data.get("executor") or "codebuddy",  # legacy items -> codebuddy
            model=data.get("model") or None,
            attempt=int(data.get("attempt", 0)),
            max_attempts=int(data.get("max_attempts", 2)),
            stage=data.get("stage", ""),
            error=data.get("error"),
            result=result,
            commit=commit,
            report_path=data.get("report_path", ""),
            auto=bool(data.get("auto", False)),
            approved_by=str(data.get("approved_by", "") or ""),
            auto_dispatched_at=str(data.get("auto_dispatched_at", "") or ""),
            hold_merge=bool(data.get("hold_merge", False)),
            merge_state=str(data.get("merge_state", "") or ""),
            plan=str(data.get("plan", "") or ""),
            parent_context=str(data.get("parent_context", "") or ""),
            spec=dict(data.get("spec") or {}),
            feedback=str(data.get("feedback", "") or ""),
            usage=dict(data.get("usage") or {}),
            delivery=dict(data.get("delivery") or {}),
            created_at=data.get("created_at") or _now(),
            updated_at=data.get("updated_at") or _now(),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
        )
