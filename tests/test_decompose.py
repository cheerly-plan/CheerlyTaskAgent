"""需求拆解：解析校验、建子任务、自动派发接线。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from taskagent.automation import AutomationEngine
from taskagent.backfill import build_children_comment, parse_tapd_ref
from taskagent.decompose import SubTask, build_prompt, create_children, parse
from taskagent.events import EventBus
from taskagent.executor import ExecutorRegistry
from taskagent.gitmap import GitProjectRegistry
from taskagent.models import ItemStatus, RunResult, WorkItem
from taskagent.projectmap import ProjectMappingStore, ProjectRegistry
from taskagent.scheduler import Scheduler, SchedulerSettings
from taskagent.store import TaskStore


def _projects(tmp_path: Path) -> ProjectRegistry:
    registry = ProjectRegistry(ProjectMappingStore(tmp_path / "projects.json"))
    for name in ("repo-a", "repo-b"):
        (tmp_path / name).mkdir(exist_ok=True)
        registry.add({
            "name": name,
            "aliases": [],
            "path": str(tmp_path / name),
            "tapd_workspace_ids": ["ws-1"],
        })
    return registry


def _parent(tmp_path: Path, projects: ProjectRegistry | None = None) -> WorkItem:
    item = WorkItem.create(
        title="会员中心改版", description="前端页面 + 后端接口 + 数据库迁移",
        kind="feature", source="tapd", source_ref="tapd:story:42",
    )
    item.tapd_workspace_id = "ws-1"
    item.status = ItemStatus.DISCOVERED
    return item


def _reply(rows: list[dict]) -> str:
    return "```json\n" + json.dumps({"subtasks": rows}, ensure_ascii=False) + "\n```"


# ── 解析与校验 ────────────────────────────────────────────────────────────────
def test_parse_keeps_only_candidate_repos_and_valid_deps(tmp_path: Path):
    projects = _projects(tmp_path)
    candidates = projects.list()
    raw = _reply([
        {"title": "后端接口", "description": "加接口", "repo": "repo-a", "kind": "feature"},
        {"title": "前端页面", "repo": "repo-b", "kind": "feature", "depends_on": [0, 5]},
        {"title": "不存在的仓", "repo": "repo-x"},
    ])
    subtasks = parse(raw, candidates)
    assert subtasks is not None
    assert [task.repo for task in subtasks] == ["repo-a", "repo-b"]
    assert subtasks[1].depends_on == [0]
    assert subtasks[1].kind == "feature"


def test_parse_rejects_fewer_than_two(tmp_path: Path):
    projects = _projects(tmp_path)
    raw = _reply([{"title": "只有一个", "repo": "repo-a"}])
    assert parse(raw, projects.list()) is None


def test_parse_rejects_garbage(tmp_path: Path):
    projects = _projects(tmp_path)
    assert parse("", projects.list()) is None
    assert parse("不是 json", projects.list()) is None
    assert parse(_reply([]), projects.list()) is None


def test_parse_caps_subtasks(tmp_path: Path):
    projects = _projects(tmp_path)
    rows = [
        {"title": f"步骤 {i}", "repo": "repo-a" if i % 2 == 0 else "repo-b"}
        for i in range(8)
    ]
    subtasks = parse(_reply(rows), projects.list(), max_subtasks=3)
    assert subtasks is not None and len(subtasks) == 3


def test_build_prompt_lists_repos_and_contract(tmp_path: Path):
    projects = _projects(tmp_path)
    prompt = build_prompt(_parent(tmp_path), projects.list(), max_subtasks=4)
    assert "repo-a" in prompt and "repo-b" in prompt
    assert "subtasks" in prompt and "depends_on" in prompt
    assert "父需求" in prompt


# ── 建子任务 ─────────────────────────────────────────────────────────────────
def test_create_children_makes_parent_container(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks")
    projects = _projects(tmp_path)
    scheduler = SimpleNamespace(store=store, projects=projects)
    parent = _parent(tmp_path)
    store.save(parent)

    children = create_children(scheduler, parent, [
        SubTask(title="后端接口", repo="repo-a", kind="feature"),
        SubTask(title="前端页面", repo="repo-b", kind="feature", depends_on=[0]),
    ])

    assert len(children) == 2
    assert children[0].repo.name == "repo-a"
    assert children[0].status == ItemStatus.READY
    assert children[1].repo.name == "repo-b"
    assert all(child.parent_ref == "tapd:story:42" for child in children)
    assert [child.source_ref for child in children] == [
        "tapd:story:42#sub-1", "tapd:story:42#sub-2",
    ]

    saved = store.get(parent.id)
    assert saved is not None
    assert saved.is_container is True
    assert saved.status == ItemStatus.SKIPPED
    assert saved.repo is None
    assert saved.children_ids == [child.source_ref for child in children]
    assert "父需求容器" in saved.stage


def test_create_children_assigns_local_ref_when_parent_has_none(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks")
    projects = _projects(tmp_path)
    scheduler = SimpleNamespace(store=store, projects=projects)
    parent = WorkItem.create(title="手动需求", description="x", kind="feature")
    parent.status = ItemStatus.READY
    store.save(parent)

    children = create_children(scheduler, parent, [
        SubTask(title="步骤一", repo="repo-a", kind="feature"),
        SubTask(title="步骤二", repo="repo-b", kind="feature"),
    ])
    saved = store.get(parent.id)
    assert saved.source_ref.startswith("local:")
    assert all(child.parent_ref == saved.source_ref for child in children)


def test_create_children_keeps_unmapped_subtask_discovered(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks")
    projects = _projects(tmp_path)
    scheduler = SimpleNamespace(store=store, projects=projects)
    parent = _parent(tmp_path)
    store.save(parent)
    children = create_children(scheduler, parent, [
        SubTask(title="已知仓", repo="repo-a"),
        SubTask(title="未知仓", repo="repo-x"),
    ])
    assert children[1].status == ItemStatus.DISCOVERED
    assert "待映射" in children[1].stage


# ── 自动拆解接线 ─────────────────────────────────────────────────────────────
class FakeScheduler:
    def __init__(self, store, projects):
        self.store = store
        self.projects = projects
        self.calls: list[dict] = []
        self.auto_outcome_hook = None

    def run_auto(self, item_id, *, plan="", exec_mode="apply", manual=False):
        self.calls.append({"id": item_id, "exec_mode": exec_mode, "manual": manual})

    def record_usage(self, item, kind, usage):
        return 0


class FakeSettings:
    def load_ui_settings(self):
        return {"automation": {"decompose": True, "max_subtasks": 3}, "models": [], "agent_model": ""}


async def test_auto_decompose_multi_repo_feature(tmp_path: Path, monkeypatch):
    store = TaskStore(tmp_path / "tasks")
    projects = _projects(tmp_path)
    parent = _parent(tmp_path)
    store.save(parent)
    scheduler = FakeScheduler(store, projects)
    engine = AutomationEngine(
        scheduler=scheduler, store=store, events=EventBus(), settings=FakeSettings()
    )
    engine._agent_model = lambda: SimpleNamespace(base_url="http://x", api_key="k")  # type: ignore

    class FakeLLM:
        last_usage = None

        def __init__(self, *args, **kwargs):
            pass

        async def complete(self, *args, **kwargs):
            return _reply([
                {"title": "后端接口", "repo": "repo-a", "kind": "feature"},
                {"title": "前端页面", "repo": "repo-b", "kind": "feature", "depends_on": [0]},
            ])

    monkeypatch.setattr("taskagent.automation.AgentLLM", FakeLLM)

    result = await engine.dispatch([parent.id], trigger="tapd-poll", exec_mode="dry-run")
    assert len(result["started"]) == 2
    assert [call["exec_mode"] for call in scheduler.calls] == ["dry-run", "dry-run"]
    saved = store.get(parent.id)
    assert saved.is_container is True
    assert len(saved.children_ids) == 2


async def test_auto_decompose_failure_falls_back_to_single_repo(tmp_path: Path, monkeypatch):
    store = TaskStore(tmp_path / "tasks")
    projects = _projects(tmp_path)
    parent = _parent(tmp_path)
    store.save(parent)
    scheduler = FakeScheduler(store, projects)
    engine = AutomationEngine(
        scheduler=scheduler, store=store, events=EventBus(), settings=FakeSettings()
    )
    engine._agent_model = lambda: SimpleNamespace(base_url="http://x", api_key="k")  # type: ignore

    class BadLLM:
        last_usage = None

        def __init__(self, *args, **kwargs):
            pass

        async def complete(self, *args, **kwargs):
            return "拆不出来"

    monkeypatch.setattr("taskagent.automation.AgentLLM", BadLLM)
    # 回退规划也失败 → 任务留在待人工映射，不建子任务。
    result = await engine.dispatch([parent.id], trigger="tapd-poll", exec_mode="apply")
    assert result["started"] == []
    assert store.get(parent.id).is_container is False

# ── 父需求汇总 ───────────────────────────────────────────────────────────────
def _scheduler_with_children(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks")
    projects = _projects(tmp_path)
    calls: list[tuple[str, bool]] = []
    scheduler = Scheduler(
        store=store,
        gitmap=GitProjectRegistry([]),
        executors=ExecutorRegistry(),
        events=EventBus(),
        settings=SchedulerSettings(reports_dir=tmp_path / "reports"),
        projects=projects,
        on_children_done=lambda parent, children, ok: calls.append((parent.id, len(children))),
    )
    parent = _parent(tmp_path)
    store.save(parent)
    children = create_children(scheduler, parent, [
        SubTask(title="后端", repo="repo-a"),
        SubTask(title="前端", repo="repo-b", depends_on=[0]),
    ])
    return scheduler, store, parent, children, calls


def _finish(child: WorkItem, *, ok: bool) -> WorkItem:
    child.status = ItemStatus.SUCCEEDED if ok else ItemStatus.FAILED
    child.merge_state = "merged" if ok else ""
    child.result = RunResult(ok=ok, dry_run=False, command="x")
    return child


async def test_parent_aggregated_once_when_children_finish(tmp_path: Path):
    scheduler, store, parent, children, calls = _scheduler_with_children(tmp_path)
    for child in children:
        store.save(_finish(child, ok=True))

    await scheduler._finalize_parent(children[0].id)
    saved = store.get(parent.id)
    assert saved.aggregated is True
    assert "2/2" in saved.stage
    assert calls == [(parent.id, 2)]

    # 第二次不再重复汇总。
    await scheduler._finalize_parent(children[1].id)
    assert calls == [(parent.id, 2)]


async def test_parent_aggregation_marks_failure(tmp_path: Path):
    scheduler, store, parent, children, calls = _scheduler_with_children(tmp_path)
    store.save(_finish(children[0], ok=True))
    store.save(_finish(children[1], ok=False))

    await scheduler._finalize_parent(children[1].id)
    saved = store.get(parent.id)
    assert saved.aggregated is True
    assert "1/2" in saved.stage and "有失败" in saved.stage
    assert calls == [(parent.id, 2)]


async def test_parent_not_aggregated_while_child_running(tmp_path: Path):
    scheduler, store, parent, children, calls = _scheduler_with_children(tmp_path)
    store.save(_finish(children[0], ok=True))
    children[1].status = ItemStatus.RUNNING
    store.save(children[1])

    await scheduler._finalize_parent(children[0].id)
    assert store.get(parent.id).aggregated is False
    assert calls == []


def test_child_tapd_ref_is_not_backfilled_to_ticket():
    # 子任务的合成 source_ref 不能当成真实 TAPD 工单 ID。
    assert parse_tapd_ref("tapd:story:42#sub-1") is None
    assert parse_tapd_ref("tapd:story:42") == ("story", "42")


def test_build_children_comment_summarises(tmp_path: Path):
    _scheduler, store, parent, children, _calls = _scheduler_with_children(tmp_path)
    for child in children:
        store.save(_finish(child, ok=True))
    text = build_children_comment(parent, children, ok=True)
    assert "2/2" in text
    assert "后端" in text and "前端" in text


async def test_all_dry_run_children_do_not_close_parent(tmp_path: Path):
    scheduler, store, parent, children, calls = _scheduler_with_children(tmp_path)
    for child in children:
        child.status = ItemStatus.SUCCEEDED
        child.result = RunResult(ok=True, dry_run=True, command="x")
        store.save(child)

    await scheduler._finalize_parent(children[0].id)
    saved = store.get(parent.id)
    assert saved.aggregated is True
    assert "dry-run" in saved.stage
    assert calls == []


def test_parse_remaps_dependencies_after_dropping_rows(tmp_path: Path):
    projects = _projects(tmp_path)
    raw = _reply([
        {"title": "非法仓", "repo": "repo-x"},
        {"title": "后端", "repo": "repo-a"},
        {"title": "前端", "repo": "repo-b", "depends_on": [1]},
    ])
    subtasks = parse(raw, projects.list())
    assert subtasks is not None
    assert [task.repo for task in subtasks] == ["repo-a", "repo-b"]
    # 原始下标 1（后端）重映射成 0（前端的前置），不能指向自己。
    assert subtasks[1].depends_on == [0]


def test_create_children_persists_dependency_refs(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks")
    projects = _projects(tmp_path)
    scheduler = SimpleNamespace(store=store, projects=projects)
    parent = _parent(tmp_path)
    store.save(parent)
    children = create_children(scheduler, parent, [
        SubTask(title="后端", repo="repo-a"),
        SubTask(title="前端", repo="repo-b", depends_on=[0]),
    ])
    assert children[1].depends_on == [children[0].source_ref]
    assert children[0].depends_on == []
    reloaded = store.get(children[1].id)
    assert reloaded.depends_on == [children[0].source_ref]


def test_dependency_block_requires_successful_prerequisite(tmp_path: Path):
    scheduler, store, _parent_item, children, _calls = _scheduler_with_children(tmp_path)
    child = store.get(children[1].id)
    assert "尚未完成" in scheduler._dependency_block(child)

    store.save(_finish(children[0], ok=True))
    assert scheduler._dependency_block(store.get(children[1].id)) == ""

    failed = store.get(children[0].id)
    failed.status = ItemStatus.FAILED
    failed.merge_state = ""
    store.save(failed)
    assert "未成功" in scheduler._dependency_block(store.get(children[1].id))


def test_dependency_block_handles_missing_ref(tmp_path: Path):
    scheduler, store, _parent_item, children, _calls = _scheduler_with_children(tmp_path)
    child = store.get(children[1].id)
    child.depends_on = ["tapd:story:42#sub-9"]
    store.save(child)
    assert "不存在" in scheduler._dependency_block(store.get(child.id))


def test_dependency_block_rejects_dry_run_prerequisite_for_apply(tmp_path: Path):
    scheduler, store, _parent_item, children, _calls = _scheduler_with_children(tmp_path)
    dep = store.get(children[0].id)
    dep.status = ItemStatus.SUCCEEDED
    dep.result = RunResult(ok=True, dry_run=True, command="x")
    store.save(dep)
    child = store.get(children[1].id)
    child.exec_mode = "apply"
    store.save(child)
    assert "dry-run" in scheduler._dependency_block(store.get(child.id))
