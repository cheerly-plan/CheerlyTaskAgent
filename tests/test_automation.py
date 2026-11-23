from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from taskagent.automation import AutomationEngine
from taskagent.events import EventBus
from taskagent.executor import CodeBuddyExecutor, ExecutorRegistry
from taskagent.gitmap import GitProjectRegistry
from taskagent.models import ItemStatus, LocalRepo, ProjectMapping, RunResult, SourceType, WorkItem
from taskagent.planner import parse_plan, plan_repo
from taskagent.review import build_review, parse_review
from taskagent.scheduler import Scheduler, SchedulerSettings
from taskagent.store import TaskStore
from taskagent.todos import TODO_DISCARDED, TODO_MERGED, TodoStore


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True,
    )


@pytest.fixture
def real_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _worktree(repo: Path, target: Path) -> Path:
    _git(repo, "worktree", "add", "-b", f"wt-{target.name}", str(target))
    return target


def _agent_stdout(review: dict) -> str:
    """DSH-style NDJSON final event carrying a fenced review JSON."""
    text = "改完了\n```json\n" + json.dumps(review, ensure_ascii=False) + "\n```\n"
    return json.dumps({"type": "final", "text": text}, ensure_ascii=False) + "\n"


REVIEW = {
    "summary": "修复登录返回值",
    "changes": ["app.py 调整 main 返回", "新增 util.py"],
    "impact": {"level": "low", "areas": ["登录"], "notes": "只影响 main 的返回值"},
    "can_merge": True,
    "merge_blockers": [],
    "confidence": 0.8,
}


def _scheduler(tmp_path: Path, todos: TodoStore | None = None, registry: ExecutorRegistry | None = None):
    data = tmp_path / "data"
    store = TaskStore(data / "tasks")
    events = EventBus()
    executor = CodeBuddyExecutor(worktrees_dir=data / "worktrees", auto_sync=True)
    scheduler = Scheduler(
        store=store,
        gitmap=GitProjectRegistry([]),
        executors=registry or ExecutorRegistry({"codebuddy": executor}),
        events=events,
        settings=SchedulerSettings(max_concurrent=2, reports_dir=data / "reports"),
        todos=todos or TodoStore(data / "todos"),
    )
    return scheduler, store, events, data


def _held_item(store: TaskStore, repo: Path, worktree: Path, review: dict = REVIEW) -> WorkItem:
    item = WorkItem.create(
        title="修复登录", description="登录返回值不对", kind="bug",
        source=SourceType.TAPD.value, source_ref="tapd:story:1",
    )
    item.repo = LocalRepo(path=str(repo), origin="", name=repo.name)
    item.status = ItemStatus.SUCCEEDED
    item.exec_mode = "apply"
    item.auto = True
    item.approved_by = "auto"
    item.hold_merge = True
    item.auto_batch_id = "batch-test"
    item.result = RunResult(
        ok=True, dry_run=False, command="fake",
        stdout=_agent_stdout(review),
        changed_files=["app.py", "util.py"],
        worktree=str(worktree),
    )
    store.save(item)
    return item


# ── review parsing ────────────────────────────────────────────────────────────
def test_parse_review_from_fenced_json():
    assert parse_review(_agent_stdout(REVIEW))["can_merge"] is True


def test_build_review_normalises_and_falls_back():
    review = build_review("没有 JSON 的普通输出", ["a.py"])
    assert review["source"] == "rule"
    assert review["impact_level"] == "unknown"
    assert review["can_merge"] is True
    assert "a.py" in review["summary"]

    empty = build_review("无输出", [])
    assert empty["can_merge"] is False
    assert empty["blockers"]


def test_parse_review_accepts_plain_prose_object():
    raw = "done " + json.dumps({
        "summary": "x", "changes": [], "impact": {"level": "none"},
        "can_merge": False, "merge_blockers": ["要改库表"], "confidence": 2,
    }, ensure_ascii=False)
    parsed = parse_review(raw)
    assert parsed is not None
    normalised = build_review(raw, ["x.py"])
    assert normalised["can_merge"] is False
    assert normalised["blockers"] == ["要改库表"]
    assert normalised["confidence"] == 1.0


# ── todo lifecycle ────────────────────────────────────────────────────────────
def test_create_todo_keeps_changes_out_of_main_repo(tmp_path: Path, real_repo: Path):
    todos = TodoStore(tmp_path / "data" / "todos")
    scheduler, store, events, _ = _scheduler(tmp_path, todos)
    wt = _worktree(real_repo, tmp_path / "wt")
    (wt / "app.py").write_text("def main():\n    return 2\n", encoding="utf-8")
    (wt / "util.py").write_text("VALUE = 1\n", encoding="utf-8")

    item = _held_item(store, real_repo, wt)
    todo = scheduler._create_todo(item)

    assert todo is not None
    assert todo.status == "pending"
    assert todo.can_merge is True
    assert todo.impact_level == "low"
    assert todo.review_source == "llm"
    assert set(todo.changed_files) == {"app.py", "util.py"}
    assert todo.diff_stat["files"] == 2
    # 自动链路不写主仓：主仓文件仍是原样。
    assert "return 1" in (real_repo / "app.py").read_text(encoding="utf-8")
    assert not (real_repo / "util.py").exists()
    assert store.get(item.id).todo_id == todo.id
    assert any(e.type == "todo_created" for e in events.replay())


def test_merge_todo_writes_worktree_into_main_repo_without_commit(tmp_path: Path, real_repo: Path):
    before = subprocess.run(
        ["git", "-C", str(real_repo), "rev-list", "--count", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    todos = TodoStore(tmp_path / "data" / "todos")
    scheduler, store, _, _ = _scheduler(tmp_path, todos)
    wt = _worktree(real_repo, tmp_path / "wt")
    (wt / "app.py").write_text("def main():\n    return 2\n", encoding="utf-8")
    (wt / "util.py").write_text("VALUE = 1\n", encoding="utf-8")

    item = _held_item(store, real_repo, wt)
    todo = scheduler._create_todo(item)
    merged = scheduler.merge_todo(todo.id)

    assert merged.status == TODO_MERGED
    assert "return 2" in (real_repo / "app.py").read_text(encoding="utf-8")
    assert (real_repo / "util.py").is_file()
    # 合并只写工作区，不产生提交。
    after = subprocess.run(
        ["git", "-C", str(real_repo), "rev-list", "--count", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert before == after
    assert store.get(item.id).result.synced_files


def test_merge_conflict_blocks_and_preserves_main_edit(tmp_path: Path, real_repo: Path):
    todos = TodoStore(tmp_path / "data" / "todos")
    scheduler, store, _, _ = _scheduler(tmp_path, todos)
    wt = _worktree(real_repo, tmp_path / "wt")
    (wt / "app.py").write_text("def main():\n    return 2\n", encoding="utf-8")
    # 主仓在 base 之后也改了同一行：三方合并冲突，不能覆盖。
    (real_repo / "app.py").write_text("def main():\n    return 99\n", encoding="utf-8")

    item = _held_item(store, real_repo, wt, review={**REVIEW, "can_merge": True})
    todo = scheduler._create_todo(item)
    blocked = scheduler.merge_todo(todo.id)

    assert blocked.status != TODO_MERGED
    assert "return 99" in (real_repo / "app.py").read_text(encoding="utf-8")
    assert blocked.merge["conflicts"] and blocked.merge["conflicts"][0].startswith("app.py")


def test_discard_removes_worktree(tmp_path: Path, real_repo: Path):
    todos = TodoStore(tmp_path / "data" / "todos")
    scheduler, store, _, _ = _scheduler(tmp_path, todos)
    wt = _worktree(real_repo, tmp_path / "wt")
    (wt / "app.py").write_text("def main():\n    return 3\n", encoding="utf-8")
    item = _held_item(store, real_repo, wt)
    todo = scheduler._create_todo(item)

    discarded = scheduler.discard_todo(todo.id)
    assert discarded.status == TODO_DISCARDED
    assert not wt.exists()


# ── automation engine ─────────────────────────────────────────────────────────
def _ready_item(store: TaskStore, repo: Path, title: str) -> WorkItem:
    item = WorkItem.create(title=title, description="x", kind="bug")
    item.repo = LocalRepo(path=str(repo), origin="", name=repo.name)
    item.status = ItemStatus.READY
    store.save(item)
    return item


async def test_dispatch_ready_item_and_close_batch(tmp_path: Path, real_repo: Path):
    todos = TodoStore(tmp_path / "data" / "todos")
    scheduler, store, _, _ = _scheduler(tmp_path, todos)
    engine = AutomationEngine(
        scheduler=scheduler, store=store, todos=todos, events=scheduler.events,
    )
    item = _ready_item(store, real_repo, "自动跑我")
    calls: list[tuple[str, str]] = []

    def fake_run_auto(item_id: str, *, batch_id: str = "", plan: str = ""):
        fresh = store.get(item_id)
        fresh.auto = True
        fresh.auto_dispatched_at = "2026-01-01T00:00:00+00:00"
        fresh.auto_batch_id = batch_id
        fresh.status = ItemStatus.QUEUED
        store.save(fresh)
        calls.append((item_id, batch_id))

    scheduler.run_auto = fake_run_auto  # type: ignore[method-assign]
    result = await engine.dispatch([item.id])
    assert result["started"] == [item.id]
    assert result["skipped"] == []
    assert len(calls) == 1
    assert calls[0][1].startswith("batch-")

    # 还在队列里 → 批次不收口；收敛成终态后 → 收口并写汇总。
    assert engine.maybe_close_batch(result["batch_id"]) == ""
    fresh = store.get(item.id)
    fresh.status = ItemStatus.SUCCEEDED
    store.save(fresh)
    assert engine.maybe_close_batch(result["batch_id"])
    batches = todos.list_batches()
    assert len(batches) == 1
    assert batches[0].total == 1
    assert batches[0].succeeded == 1


async def test_dispatch_skips_unknown_unmapped_and_dispatched(tmp_path: Path, real_repo: Path):
    todos = TodoStore(tmp_path / "data" / "todos")
    scheduler, store, _, _ = _scheduler(tmp_path, todos)
    engine = AutomationEngine(
        scheduler=scheduler, store=store, todos=todos, events=scheduler.events,
    )
    scheduler.run_auto = lambda *a, **kw: None  # type: ignore[method-assign]

    unmapped = WorkItem.create(title="没有仓库", description="x", kind="bug")
    store.save(unmapped)
    dispatched = _ready_item(store, real_repo, "已跑过")
    dispatched.auto_dispatched_at = "2026-01-01T00:00:00+00:00"
    store.save(dispatched)

    result = await engine.dispatch(["missing", unmapped.id, dispatched.id])
    assert result["started"] == []
    reasons = {entry["id"]: entry["reason"] for entry in result["skipped"]}
    assert reasons["missing"] == "任务不存在"
    assert "未映射仓库" in reasons[unmapped.id]
    assert "已经自动执行过" in reasons[dispatched.id]


async def test_dispatch_empty_list_and_on_item_done(tmp_path: Path, real_repo: Path):
    todos = TodoStore(tmp_path / "data" / "todos")
    scheduler, store, _, _ = _scheduler(tmp_path, todos)
    engine = AutomationEngine(
        scheduler=scheduler, store=store, todos=todos, events=scheduler.events,
    )
    assert (await engine.dispatch([]))["started"] == []

    item = _ready_item(store, real_repo, "跑完收口")

    def fake_run_auto(item_id: str, *, batch_id: str = "", plan: str = ""):
        fresh = store.get(item_id)
        fresh.auto = True
        fresh.auto_dispatched_at = "2026-01-01T00:00:00+00:00"
        fresh.auto_batch_id = batch_id
        fresh.status = ItemStatus.QUEUED
        store.save(fresh)

    scheduler.run_auto = fake_run_auto  # type: ignore[method-assign]
    batch_id = (await engine.dispatch([item.id]))["batch_id"]
    fresh = store.get(item.id)
    fresh.status = ItemStatus.FAILED
    store.save(fresh)
    engine.on_item_done(item.id)
    assert todos.get_batch(batch_id) is not None
    batch = todos.get_batch(batch_id)
    assert batch is not None and batch.failed == 1


# ── planner ───────────────────────────────────────────────────────────────────
def _mapping(name: str, path: Path) -> ProjectMapping:
    return ProjectMapping(id=name, name=name, path=str(path))


def test_parse_plan_matches_candidate(tmp_path: Path):
    candidates = [_mapping("web", tmp_path), _mapping("api", tmp_path)]
    raw = json.dumps({"repo": "api", "reason": "后端接口", "steps": ["改 controller"]})
    plan = parse_plan(raw, candidates)
    assert plan is not None
    assert plan.repo == "api"
    assert plan.to_prompt() == "1. 改 controller"

    bad = parse_plan(json.dumps({"repo": "不存在"}), candidates)
    assert bad is None


async def test_plan_repo_single_candidate_skips_llm(tmp_path: Path):
    candidates = [_mapping("only", tmp_path)]
    plan = await plan_repo(
        WorkItem.create(title="x", description="y"), candidates,
        backend=None, enabled=True, timeout_s=5, max_candidates=3,
    )
    assert plan is not None and plan.repo == "only" and plan.source == "rule"