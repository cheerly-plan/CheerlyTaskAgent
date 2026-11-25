"""需求拆解：把一个跨仓/多步骤需求拆成可独立执行的子任务。

调度台之前只有「一单一个仓库」：TAPD 项目绑了多个仓库时，只能让 LLM 挑
**一个**，剩下的靠人再建单。这里补上拆解这一层：

    父需求（变成容器，不执行）
      ├── 子任务 1（repo A，可先跑）
      ├── 子任务 2（repo B，依赖 1）
      └── 子任务 3（repo A，依赖 2）

判定完全在本地：候选仓库来自 TAPD 绑定或项目映射；LLM 只负责「拆成哪几
步、每步在哪个仓、谁依赖谁」。返回结果要过确定性校验（仓库必须在候选里、
依赖只能指向更早的步骤、最多 ``max_subtasks`` 条），不合法就整份丢弃，绝不
拿半个方案去建单。

子任务与父需求的关系沿用现有 parent_ref/children_ids：调度器的父锁会让同一
父需求下的自动任务串行，因此按拓扑顺序创建即可满足依赖。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .models import CONTAINER_STAGE, ItemKind, ItemStatus, LocalRepo, ProjectMapping, WorkItem
from .planner import extract_json_object, repo_brief

logger = logging.getLogger(__name__)

MAX_SUBTASKS = 5
MIN_SUBTASKS = 2
DECOMPOSE_TIMEOUT_S = 120
VALID_KINDS = {"bug", "feature", "task"}

_KIND_LABEL = {
    ItemKind.BUG: "缺陷修复",
    ItemKind.FEATURE: "新需求",
    ItemKind.TASK: "开发任务",
}


@dataclass
class SubTask:
    """One independently executable slice of a larger requirement."""

    title: str
    description: str = ""
    repo: str = ""
    kind: str = "feature"
    depends_on: list[int] = field(default_factory=list)


def candidates_for(scheduler, item: WorkItem) -> list[ProjectMapping]:
    """Repos this item could legitimately touch.

    TAPD binding wins; then the parent's own mapping; finally all enabled
    mappings for a manual item with no binding (bounded).
    """
    projects = getattr(scheduler, "projects", None)
    if projects is None:
        return []
    if item.tapd_workspace_id:
        bound = projects.find_all_by_workspace(item.tapd_workspace_id)
        if bound:
            return bound
    if item.repo is not None:
        own = [m for m in projects.list() if m.enabled and m.name == item.repo.name]
        if own:
            return own
    return [m for m in projects.list() if m.enabled][:6]


def build_prompt(item: WorkItem, candidates: list[ProjectMapping], *, max_subtasks: int) -> str:
    repos = "\n".join(repo_brief(c) for c in candidates)
    return (
        "你是需求拆解助手。把下面这条开发需求拆成可以依次落地、每步都能独立"
        "验证的子任务。\n"
        f"要求：拆成 {MIN_SUBTASKS}-{max_subtasks} 个子任务；每个子任务必须落在"
        "下面某个候选仓库里，repo 写候选仓库名称且必须完全一致；不要拆出"
        "「调研」「写文档」这类不可验证的任务；子任务之间不要重复。\n"
        "只输出一个 ```json 代码块，不要输出其他内容：\n"
        '{"subtasks":[{"title":"子任务标题","description":"这一步做什么、验收什么",'
        '"repo":"候选仓库名称","kind":"bug|feature|task","depends_on":[0]}]}\n'
        "depends_on 里放必须**先完成**的子任务下标（从 0 开始，只能依赖更早的）。\n\n"
        "--- 父需求 ---\n"
        f"类型：{_KIND_LABEL.get(item.kind, item.kind.value)}\n"
        f"标题：{item.title}\n"
        f"描述：{item.description[:6000]}\n"
        "--- 候选仓库 ---\n"
        f"{repos}\n"
        "--- 结束 ---\n"
    )


def parse(
    raw: str,
    candidates: list[ProjectMapping],
    *,
    max_subtasks: int = MAX_SUBTASKS,
) -> list[SubTask] | None:
    """Validate a model reply into an ordered, dependency-safe task list."""
    if not raw:
        return None
    data = extract_json_object(raw)
    if not data:
        return None
    rows = data.get("subtasks")
    if not isinstance(rows, list):
        return None
    by_name = {c.name.strip().lower(): c for c in candidates}
    limit = max(MIN_SUBTASKS, min(int(max_subtasks or MAX_SUBTASKS), MAX_SUBTASKS))
    kept: list[tuple[int, SubTask]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        repo = str(row.get("repo") or "").strip()
        if not title or repo.lower() not in by_name:
            continue
        kind = str(row.get("kind") or "feature").strip().lower()
        deps: list[int] = []
        raw_deps = row.get("depends_on")
        if isinstance(raw_deps, int):
            raw_deps = [raw_deps]
        if isinstance(raw_deps, list):
            for dep in raw_deps:
                try:
                    value = int(dep)
                except (TypeError, ValueError):
                    continue
                if 0 <= value < index and value not in deps:
                    deps.append(value)
        kept.append((index, SubTask(
            title=title[:200],
            description=str(row.get("description") or "").strip()[:4000],
            repo=by_name[repo.lower()].name,
            kind=kind if kind in VALID_KINDS else "feature",
            depends_on=sorted(deps),
        )))
        if len(kept) >= limit:
            break
    if len(kept) < MIN_SUBTASKS:
        return None
    # 丢弃非法行后，原始下标会错位：建立 old_index -> new_index 的映射，
    # 依赖两端都保留才重映射，避免指向自己/未来的任务。
    index_map = {original: new for new, (original, _task) in enumerate(kept)}
    result: list[SubTask] = []
    for new_index, (_original, task) in enumerate(kept):
        task.depends_on = [
            index_map[dep]
            for dep in task.depends_on
            if dep in index_map and index_map[dep] < new_index
        ]
        result.append(task)
    return result


async def plan(
    item: WorkItem,
    candidates: list[ProjectMapping],
    *,
    llm: Any,
    timeout_s: int = DECOMPOSE_TIMEOUT_S,
    max_subtasks: int = MAX_SUBTASKS,
    errors: list[str] | None = None,
) -> list[SubTask] | None:
    """Ask the backend agent model to decompose; None on any failure.

    Planning must never break the dispatcher: a failed/empty decomposition
    falls back to the existing single-repo planning path.
    """
    if llm is None or len(candidates) == 0:
        return None
    prompt = build_prompt(item, candidates, max_subtasks=max_subtasks)
    try:
        raw = await llm.complete(prompt, timeout_s=timeout_s)
    except Exception as error:  # noqa: BLE001 - planner failure is non-fatal
        logger.warning("需求拆解失败（%s）：%s", item.id, error)
        if errors is not None:
            errors.append(f"模型调用失败：{error}")
        return None
    subtasks = parse(raw, candidates, max_subtasks=max_subtasks)
    if subtasks is None:
        logger.info("需求拆解未产生可用结果（%s），回退单任务", item.id)
        if errors is not None:
            errors.append("模型输出无法解析，或合法子任务不足 2 个")
    return subtasks


def create_children(
    scheduler,
    parent: WorkItem,
    subtasks: list[SubTask],
) -> list[WorkItem]:
    """Materialise subtasks as child WorkItems; the parent becomes a container."""
    store = scheduler.store
    parent_ref = (parent.source_ref or "").strip()
    if not parent_ref:
        parent_ref = f"local:{parent.id}"
        parent.source_ref = parent_ref
    mappings = {
        m.name.strip().lower(): m
        for m in (scheduler.projects.list() if getattr(scheduler, "projects", None) else [])
    }
    refs = [f"{parent_ref}#sub-{index + 1}" for index in range(len(subtasks))]
    children: list[WorkItem] = []
    for index, subtask in enumerate(subtasks):
        child = WorkItem.create(
            title=subtask.title,
            description=subtask.description,
            kind=subtask.kind or parent.kind.value,
            source=parent.source.value,
            source_ref=refs[index],
            exec_mode=parent.exec_mode,
            executor=parent.executor,
            model=parent.model,
            parent_ref=parent_ref,
            parent_title=parent.title,
        )
        child.depends_on = [
            refs[dep] for dep in subtask.depends_on if 0 <= dep < len(refs)
        ]
        child.tapd_workspace_id = parent.tapd_workspace_id
        child.git_url = parent.git_url
        child.auto = parent.auto
        child.approved_by = parent.approved_by
        mapping = mappings.get(subtask.repo.strip().lower())
        if mapping is not None:
            child.repo = LocalRepo(path=mapping.path, origin="", name=mapping.name)
            child.status = ItemStatus.READY
            child.stage = f"已拆解到 {mapping.name}"
        elif parent.repo is not None and (not subtask.repo or subtask.repo == parent.repo.name):
            child.repo = parent.repo
            child.status = ItemStatus.READY
            child.stage = f"已拆解到 {parent.repo.name}"
        else:
            child.status = ItemStatus.DISCOVERED
            child.stage = f"已拆解，待映射仓库（{subtask.repo or '未指定'}）"
        child.plan = ""
        store.save(child)
        children.append(child)

    parent.is_container = True
    parent.repo = None
    parent.status = ItemStatus.SKIPPED
    parent.stage = CONTAINER_STAGE
    parent.children_ids = [child.source_ref for child in children]
    store.save(parent)
    logger.info("需求 %s 拆成 %d 个子任务", parent.id, len(children))
    return children


def dump_prompt_preview(item: WorkItem, candidates: list[ProjectMapping], *, max_subtasks: int) -> str:
    """Exposed for tests/debugging."""
    return build_prompt(item, candidates, max_subtasks=max_subtasks)


def to_json(subtasks: list[SubTask]) -> str:
    return json.dumps(
        {"subtasks": [
            {
                "title": task.title,
                "description": task.description,
                "repo": task.repo,
                "kind": task.kind,
                "depends_on": task.depends_on,
            }
            for task in subtasks
        ]},
        ensure_ascii=False,
    )