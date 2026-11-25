from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from taskagent.automation import AutomationEngine
from taskagent.events import EventBus
from taskagent.executor import CodeBuddyExecutor, ExecutorRegistry, build_prompt
from taskagent.gitmap import GitProjectRegistry
from taskagent.models import (
    ItemStatus,
    LocalRepo,
    ProjectMapping,
    RunResult,
    WorkItem,
)
from taskagent.planner import build_short_plan, parse_plan, plan_repo
from taskagent.report import parent_context_of
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

    def fake_run_auto(item_id: str, *, plan: str = "", exec_mode: str = "apply", manual: bool = False):
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


async def test_dispatch_ready_item_passes_short_plan(tmp_path: Path, real_repo: Path):
    scheduler, store, _, _ = _scheduler(tmp_path)
    engine = AutomationEngine(scheduler=scheduler, store=store, events=scheduler.events)
    item = _ready_item(store, real_repo, "自动跑我")
    plans: list[str] = []

    def fake_run_auto(item_id: str, *, plan: str = "", exec_mode: str = "apply", manual: bool = False):
        plans.append(plan)
        fresh = store.get(item_id)
        fresh.auto_dispatched_at = "2026-01-01T00:00:00+00:00"
        store.save(fresh)

    scheduler.run_auto = fake_run_auto  # type: ignore[method-assign]
    result = await engine.dispatch([item.id])

    assert result["started"] == [item.id]
    assert plans and "自动跑我" in plans[0] and "1." in plans[0]


async def test_dispatch_single_repo_needs_no_llm(tmp_path: Path, real_repo: Path):
    from taskagent.projectmap import ProjectMappingStore, ProjectRegistry

    scheduler, store, _, _ = _scheduler(tmp_path)
    registry = ProjectRegistry(ProjectMappingStore(tmp_path / "projects.json"))
    registry.add({
        "id": "single", "name": "single", "path": str(real_repo),
        "tapd_workspace_ids": ["ws-1"],
    })
    scheduler.projects = registry
    engine = AutomationEngine(scheduler=scheduler, store=store, events=scheduler.events)

    item = WorkItem.create(
        title="单仓任务", description="x", kind="bug",
        source="tapd", source_ref="tapd:story:1",
    )
    item.tapd_workspace_id = "ws-1"
    store.save(item)
    plans: list[str] = []

    def fake_run_auto(item_id: str, *, plan: str = "", exec_mode: str = "apply", manual: bool = False):
        plans.append(plan)
        fresh = store.get(item_id)
        fresh.auto_dispatched_at = "2026-01-01T00:00:00+00:00"
        store.save(fresh)

    scheduler.run_auto = fake_run_auto  # type: ignore[method-assign]
    result = await engine.dispatch([item.id])

    assert result["started"] == [item.id]
    assert plans and "单仓任务" in plans[0]
    assert store.get(item.id).plan  # 计划已落库


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
        llm=None, enabled=True, timeout_s=5, max_candidates=3,
    )
    assert plan is not None and plan.repo == "only" and plan.source == "rule"
    assert plan.steps and "1." in plan.to_prompt()  # 单仓也生成短计划


async def test_plan_repo_degrades_when_backend_llm_fails(tmp_path: Path):
    candidates = [_mapping("api", tmp_path), _mapping("web", tmp_path)]

    class BrokenLLM:
        async def complete(self, prompt: str, *, timeout_s: float = 0) -> str:
            raise RuntimeError("endpoint down")

    plan = await plan_repo(
        WorkItem.create(title="改首页", description="x"), candidates,
        llm=BrokenLLM(), enabled=True, timeout_s=5, max_candidates=3,
    )
    assert plan is None


async def test_engine_reads_agent_model_from_settings(tmp_path: Path):
    scheduler, store, _, _ = _scheduler(tmp_path)

    class FakeSettings:
        def load_ui_settings(self):
            return {
                "models": [{
                    "id": "qwen-max",
                    "enabled": True,
                    "base_url": "https://example.test/v1",
                    "api_key": "sk-test",
                }],
                "agent_model": "qwen-max",
            }

    engine = AutomationEngine(
        scheduler=scheduler, store=store, events=scheduler.events,
        settings=FakeSettings(),
    )
    agent = engine._agent_model()
    assert agent is not None and agent.id == "qwen-max"


async def test_plan_repo_uses_backend_llm(tmp_path: Path):
    candidates = [_mapping("api", tmp_path), _mapping("web", tmp_path)]

    class FakeLLM:
        async def complete(self, prompt: str, *, timeout_s: float = 0) -> str:
            assert "候选仓库" in prompt
            return '```json\n{"repo":"web","reason":"前端页面","steps":["改页面"]}\n```'

    plan = await plan_repo(
        WorkItem.create(title="改首页", description="x"), candidates,
        llm=FakeLLM(), enabled=True, timeout_s=5, max_candidates=3,
    )
    assert plan is not None
    assert plan.repo == "web" and plan.source == "llm"
    assert plan.to_prompt() == "1. 改页面"


def test_short_plan_has_steps_without_llm():
    item = WorkItem.create(title="修复登录", description="bug", kind="bug")
    plan = build_short_plan(item)
    assert plan.steps and "修复登录" in plan.steps[0]
    assert "1." in plan.to_prompt()


def test_prompt_injects_parent_context(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks")
    parent = WorkItem.create(
        title="账号体系", description="统一登录与权限", source="tapd",
        source_ref="tapd:story:10",
    )
    store.save(parent)
    child = WorkItem.create(
        title="登录按钮", description="改前端", parent_ref=parent.source_ref,
    )

    context = parent_context_of(child, store)
    assert "父需求：账号体系" in context
    assert "父需求描述：统一登录与权限" in context

    child.parent_context = context
    child.plan = "1. 改按钮"
    prompt = build_prompt(child)
    assert "所属父需求（上下文）" in prompt
    assert "父需求：账号体系" in prompt
    assert "已确认的执行方案" in prompt

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


async def test_run_auto_dry_run_does_not_touch_repo(tmp_path: Path, real_repo: Path):
    """数据源 dry-run 时，自动链路只生成命令，绝不改仓库。"""
    import asyncio

    scheduler, store, _, _ = _scheduler(tmp_path)
    item = _ready_item(store, real_repo, "只出方案")
    before = (real_repo / "app.py").read_text(encoding="utf-8")

    scheduler.run_auto(item.id, exec_mode="dry-run")
    for _ in range(200):
        saved = store.get(item.id)
        if saved is not None and saved.status in (ItemStatus.SUCCEEDED, ItemStatus.FAILED):
            break
        await asyncio.sleep(0.02)

    saved = store.get(item.id)
    assert saved is not None
    assert saved.status == ItemStatus.SUCCEEDED
    assert saved.result is not None and saved.result.dry_run is True
    assert saved.auto is True
    # dry-run 不消耗“已自动派发”，评估后还能切 apply 补跑。
    assert saved.auto_dispatched_at == ""
    assert (real_repo / "app.py").read_text(encoding="utf-8") == before


async def test_dispatch_can_upgrade_dry_run_item_to_apply(tmp_path: Path, real_repo: Path):
    scheduler, store, _, _ = _scheduler(tmp_path)
    engine = AutomationEngine(scheduler=scheduler, store=store, events=scheduler.events)
    item = _ready_item(store, real_repo, "先评后跑")
    item.status = ItemStatus.SUCCEEDED
    item.exec_mode = "dry-run"
    item.auto = True
    item.auto_dispatched_at = "2026-01-01T00:00:00+00:00"
    item.result = RunResult(ok=True, dry_run=True, command="codebuddy -p ...")
    store.save(item)
    calls: list[str] = []

    def fake_run_auto(item_id: str, *, plan: str = "", exec_mode: str = "apply", manual: bool = False):
        calls.append(exec_mode)

    scheduler.run_auto = fake_run_auto  # type: ignore[method-assign]
    result = await engine.dispatch(
        [item.id], trigger="manual", exec_mode="apply", force=True
    )
    assert result["started"] == [item.id]
    assert calls == ["apply"]
