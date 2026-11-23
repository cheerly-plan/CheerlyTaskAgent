from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from taskagent.automation import AutomationEngine
from taskagent.events import EventBus
from taskagent.executor import CodeBuddyExecutor, ExecutorRegistry
from taskagent.gitmap import GitProjectRegistry
from taskagent.models import (
    ItemStatus,
    LocalRepo,
    ProjectMapping,
    RunResult,
    WorkItem,
)
from taskagent.planner import parse_plan, plan_repo
from taskagent.review import build_diff, diff_stat
from taskagent.scheduler import Scheduler, SchedulerSettings
from taskagent.store import TaskStore


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


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


def _scheduler(tmp_path: Path, registry: ExecutorRegistry | None = None):
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
    )
    return scheduler, store, events, data


def _held_item(store: TaskStore, repo: Path, worktree: Path, changed: list[str]) -> WorkItem:
    item = WorkItem.create(
        title="自动合并测试", description="x", kind="bug",
        source="tapd", source_ref="tapd:story:1",
    )
    item.repo = LocalRepo(path=str(repo), origin="", name=repo.name)
    item.status = ItemStatus.SUCCEEDED
    item.exec_mode = "apply"
    item.auto = True
    item.approved_by = "auto"
    item.hold_merge = True
    item.merge_state = ""
    item.result = RunResult(
        ok=True, dry_run=False, command="fake",
        changed_files=list(changed), worktree=str(worktree),
    )
    store.save(item)
    return item


# ── 自动合并：无冲突直接写入，有冲突原子回滚 ─────────────────────────────────
def test_merge_item_writes_clean_changes_without_commit(tmp_path: Path, real_repo: Path):
    before = subprocess.run(
        ["git", "-C", str(real_repo), "rev-list", "--count", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    scheduler, store, _events, _ = _scheduler(tmp_path)
    wt = _worktree(real_repo, tmp_path / "wt")
    (wt / "app.py").write_text("def main():\n    return 2\n", encoding="utf-8")
    (wt / "util.py").write_text("VALUE = 1\n", encoding="utf-8")

    item = _held_item(store, real_repo, wt, ["app.py", "util.py"])
    merged = scheduler.merge_item(item.id)

    assert merged.merge_state == "merged"
    assert "已合并" in merged.stage
    assert "return 2" in (real_repo / "app.py").read_text(encoding="utf-8")
    assert (real_repo / "util.py").is_file()
    after = subprocess.run(
        ["git", "-C", str(real_repo), "rev-list", "--count", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert before == after  # 不产生提交
    assert merged.result is not None and merged.result.synced_files


def test_merge_item_conflict_is_atomic_and_blocks(tmp_path: Path, real_repo: Path):
    scheduler, store, events, _ = _scheduler(tmp_path)
    wt = _worktree(real_repo, tmp_path / "wt")
    (wt / "app.py").write_text("def main():\n    return 2\n", encoding="utf-8")
    (wt / "util.py").write_text("VALUE = 1\n", encoding="utf-8")
    # 主仓在 base 之后也改了同一行：冲突。
    (real_repo / "app.py").write_text("def main():\n    return 99\n", encoding="utf-8")

    item = _held_item(store, real_repo, wt, ["app.py", "util.py"])
    blocked = scheduler.merge_item(item.id)

    assert blocked.merge_state == "blocked"
    assert "合并冲突" in blocked.stage
    assert "return 99" in (real_repo / "app.py").read_text(encoding="utf-8")
    # 原子：另一个本来不冲突的新文件也不能写入。
    assert not (real_repo / "util.py").exists()
    assert blocked.result is not None
    assert blocked.result.synced_files == []
    assert blocked.result.sync_conflicts
    assert any(e.type == "item_merge_blocked" for e in events.replay())


def test_merge_item_rejects_dry_run(tmp_path: Path, real_repo: Path):
    scheduler, store, _, _ = _scheduler(tmp_path)
    item = WorkItem.create(title="dry", description="x")
    item.repo = LocalRepo(path=str(real_repo), origin="", name=real_repo.name)
    item.status = ItemStatus.SUCCEEDED
    item.exec_mode = "dry-run"
    item.result = RunResult(ok=True, dry_run=True, command="fake")
    store.save(item)
    with pytest.raises(ValueError, match="dry-run"):
        scheduler.merge_item(item.id)


def test_diff_helpers_cover_modified_and_new_files(tmp_path: Path, real_repo: Path):
    wt = _worktree(real_repo, tmp_path / "wt")
    (wt / "app.py").write_text("def main():\n    return 2\n", encoding="utf-8")
    (wt / "util.py").write_text("VALUE = 1\n", encoding="utf-8")

    diff = build_diff(wt, ["app.py", "util.py"])
    stat = diff_stat(wt, ["app.py", "util.py"])
    assert "return 2" in diff
    assert "util.py" in diff
    assert stat["files"] == 2
    assert stat["added"] >= 2


# ── 自动执行引擎 ─────────────────────────────────────────────────────────────
def _ready_item(store: TaskStore, repo: Path, title: str) -> WorkItem:
    item = WorkItem.create(title=title, description="x", kind="bug")
    item.repo = LocalRepo(path=str(repo), origin="", name=repo.name)
    item.status = ItemStatus.READY
    store.save(item)
    return item


async def test_dispatch_starts_selected_ready_items(tmp_path: Path, real_repo: Path):
    scheduler, store, _, _ = _scheduler(tmp_path)
    engine = AutomationEngine(scheduler=scheduler, store=store, events=scheduler.events)
    item = _ready_item(store, real_repo, "自动跑我")
    calls: list[str] = []

    def fake_run_auto(item_id: str, *, plan: str = ""):
        fresh = store.get(item_id)
        fresh.auto = True
        fresh.auto_dispatched_at = "2026-01-01T00:00:00+00:00"
        fresh.status = ItemStatus.QUEUED
        store.save(fresh)
        calls.append(item_id)

    scheduler.run_auto = fake_run_auto  # type: ignore[method-assign]
    result = await engine.dispatch([item.id])
    assert result["started"] == [item.id]
    assert result["skipped"] == []
    assert calls == [item.id]


async def test_dispatch_skips_unknown_unmapped_and_dispatched(tmp_path: Path, real_repo: Path):
    scheduler, store, _, _ = _scheduler(tmp_path)
    engine = AutomationEngine(scheduler=scheduler, store=store, events=scheduler.events)
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


async def test_dispatch_empty_list(tmp_path: Path):
    scheduler, store, _, _ = _scheduler(tmp_path)
    engine = AutomationEngine(scheduler=scheduler, store=store, events=scheduler.events)
    assert (await engine.dispatch([]))["started"] == []


# ── planner ───────────────────────────────────────────────────────────────────
def _mapping(name: str, path: Path) -> ProjectMapping:
    return ProjectMapping(id=name, name=name, path=str(path))


def test_parse_plan_matches_candidate(tmp_path: Path):
    candidates = [_mapping("web", tmp_path), _mapping("api", tmp_path)]
    import json

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

# ── 父需求容器：只做分组头，不映射、不执行 ───────────────────────────────────
def test_parent_import_is_container_and_not_executable(tmp_path: Path):
    from taskagent.models import CONTAINER_STAGE, SourceType
    from taskagent.sources import RawItem

    scheduler, _store, _, _ = _scheduler(tmp_path)
    raw = RawItem(
        source=SourceType.TAPD, title="父需求", body="x",
        source_ref="tapd:story:100", is_parent=True,
    )
    item, created = scheduler.import_raw(raw)
    assert created
    assert item.is_container is True
    assert item.repo is None
    assert item.status == ItemStatus.DISCOVERED
    assert item.stage == CONTAINER_STAGE
    with pytest.raises(ValueError, match="容器"):
        scheduler.run(item.id, exec_mode="apply", approved=True)
    with pytest.raises(ValueError, match="容器"):
        scheduler.map_manually(item.id, "whatever")


def test_link_parent_relations_normalises_existing_container(tmp_path: Path, real_repo: Path):
    from taskagent.models import CONTAINER_STAGE

    scheduler, store, _, _ = _scheduler(tmp_path)
    parent = WorkItem.create(title="父需求", description="x", source_ref="tapd:story:200")
    parent.repo = LocalRepo(path=str(real_repo), origin="", name=real_repo.name)
    parent.status = ItemStatus.READY
    parent.stage = "已映射仓库"
    store.save(parent)
    child = WorkItem.create(
        title="子需求", description="x",
        source_ref="tapd:story:201", parent_ref=parent.source_ref,
    )
    store.save(child)

    scheduler.link_parent_relations()
    fixed = store.get(parent.id)
    assert fixed is not None
    assert fixed.is_container is True
    assert fixed.repo is None
    assert fixed.status == ItemStatus.DISCOVERED
    assert fixed.stage == CONTAINER_STAGE
    assert fixed.children_ids == [child.source_ref]
