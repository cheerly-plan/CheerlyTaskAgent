"""跨任务记忆：成功经验召回、失败聚类、提示词注入。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from taskagent.events import EventBus
from taskagent.executor import CodeBuddyExecutor
from taskagent.gitmap import GitProjectRegistry
from taskagent.memory import TaskMemory, memory_text, tokens
from taskagent.models import ItemStatus, LocalRepo, RunResult, WorkItem
from taskagent.scheduler import Scheduler, SchedulerSettings
from taskagent.store import TaskStore


def _item(
    tmp_path: Path,
    *,
    title: str,
    description: str = "",
    repo: str = "repo-a",
    ok: bool = True,
) -> WorkItem:
    item = WorkItem.create(title=title, description=description, kind="bug", source="tapd")
    item.repo = LocalRepo(path=str(tmp_path / repo), origin="", name=repo)
    item.status = ItemStatus.SUCCEEDED if ok else ItemStatus.FAILED
    gate: dict = {"ok": ok, "state": "passed" if ok else "failed"}
    if not ok:
        gate["checks"] = [
            {"id": "lint", "category": "lint", "ok": False, "tail": "E501 line too long"},
            {"id": "test", "category": "test", "ok": False, "tail": "1 failed"},
        ]
        gate["review"] = {"state": "done", "verdict": "block", "risks": ["security"]}
    item.result = RunResult(
        ok=ok,
        dry_run=False,
        command="x",
        changed_files=["app.py", "util.py"],
        verification=gate,
        rounds=2,
    )
    item.error = "" if ok else "验证未通过：lint"
    item.spec = {"goal": "修复登录返回值", "source": "rule"}
    return item


def test_tokens_support_chinese_bigrams_and_latin_words():
    values = tokens("修复登录 login 超时 timeout")
    assert "login" in values and "timeout" in values
    assert "登录" in values and "修复" in values


def test_record_and_recall_similar_success(tmp_path: Path):
    memory = TaskMemory(tmp_path / "memory.json")
    done = _item(tmp_path, title="修复登录接口返回 500", description="登录时后端报错")
    memory.record(done)

    current = _item(tmp_path, title="修复登录页返回值", description="登录接口")
    recalled = memory.recall(current)
    assert recalled["similar"], recalled
    assert recalled["similar"][0]["title"] == "修复登录接口返回 500"
    assert recalled["similar"][0]["changed_files"] == ["app.py", "util.py"]
    assert recalled["stats"]["total"] == 1
    assert recalled["stats"]["succeeded"] == 1


def test_similar_is_scoped_to_same_repo(tmp_path: Path):
    memory = TaskMemory(tmp_path / "memory.json")
    memory.record(_item(tmp_path, title="修复登录接口", repo="repo-a"))
    other = _item(tmp_path, title="修复登录接口", repo="repo-b")
    assert memory.recall(other)["similar"] == []


def test_failure_clusters_group_and_count(tmp_path: Path):
    memory = TaskMemory(tmp_path / "memory.json")
    for _ in range(2):
        memory.record(_item(tmp_path, title="坏任务", ok=False))
    memory.record(_item(tmp_path, title="另一个坏任务", ok=False))

    clusters = memory.clusters("repo-a")
    assert len(clusters) == 1
    assert clusters[0]["count"] == 3
    assert "lint" in clusters[0]["signature"]
    assert "security" in clusters[0]["signature"]
    assert clusters[0]["summary"]
    assert memory.stats("repo-a")["failed"] == 3


def test_failure_clusters_are_scoped_by_repo(tmp_path: Path):
    memory = TaskMemory(tmp_path / "memory.json")
    memory.record(_item(tmp_path, title="a", repo="repo-a", ok=False))
    memory.record(_item(tmp_path, title="b", repo="repo-b", ok=False))
    assert {row["repo"] for row in memory.clusters()} == {"repo-a", "repo-b"}
    assert {row["repo"] for row in memory.clusters("repo-b")} == {"repo-b"}


def test_memory_persists_and_clears(tmp_path: Path):
    path = tmp_path / "memory.json"
    memory = TaskMemory(path)
    memory.record(_item(tmp_path, title="持久化任务"))
    reloaded = TaskMemory(path)
    assert reloaded.stats()["total"] == 1

    assert reloaded.clear("repo-a") == 1
    assert TaskMemory(path).stats()["total"] == 0


def test_clear_all(tmp_path: Path):
    memory = TaskMemory(tmp_path / "memory.json")
    memory.record(_item(tmp_path, title="a"))
    memory.record(_item(tmp_path, title="b", repo="repo-b"))
    assert memory.clear() == 2
    assert memory.entries() == []


def test_memory_text_renders_similar_and_failures():
    text = memory_text({
        "similar": [{
            "title": "修复登录", "goal": "登录成功", "changed_files": ["a.py"],
        }],
        "failures": [{
            "summary": "检查失败 / lint", "count": 3, "sample_error": "E501",
        }],
    })
    assert "历史同类成功任务" in text
    assert "修复登录" in text
    assert "高频失败" in text
    assert "lint" in text


def test_memory_text_empty_when_nothing():
    assert memory_text({}) == ""
    assert memory_text({"similar": [], "failures": []}) == ""


async def test_scheduler_injects_memory_into_prompt_and_records(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks")
    scheduler = Scheduler(
        store=store,
        gitmap=GitProjectRegistry([]),
        executor=CodeBuddyExecutor(worktrees_dir=tmp_path / "wt"),
        events=EventBus(),
        settings=SchedulerSettings(
            reports_dir=tmp_path / "reports",
            memory_provider=lambda item: {
                "similar": [{"title": "上次同类修复", "goal": "g", "changed_files": ["app.py"]}],
                "failures": [],
            },
        ),
    )
    recorded: list[str] = []
    scheduler.on_item_done = lambda item: recorded.append(item.id)

    repo = tmp_path / "repo"
    repo.mkdir()
    item = WorkItem.create(title="修复登录", description="x", kind="bug")
    item.repo = LocalRepo(path=str(repo), origin="", name="repo")
    item.status = ItemStatus.READY
    item.exec_mode = "dry-run"
    store.save(item)

    scheduler.run(item.id, exec_mode="dry-run", approved=False)
    for _ in range(200):
        current = store.get(item.id)
        if current is not None and current.status in (ItemStatus.SUCCEEDED, ItemStatus.FAILED):
            break
        await asyncio.sleep(0.02)

    saved = store.get(item.id)
    assert saved is not None and saved.status == ItemStatus.SUCCEEDED
    assert saved.memory["similar"][0]["title"] == "上次同类修复"
    assert "跨任务记忆" in (saved.result.command if saved.result else "")
    # dry-run 不进记忆库，但终态回调仍然发生。
    assert recorded == [item.id]

def test_scheduler_records_memory_through_hook(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks")
    memory = TaskMemory(tmp_path / "memory.json")
    scheduler = Scheduler(
        store=store,
        gitmap=GitProjectRegistry([]),
        events=EventBus(),
        settings=SchedulerSettings(reports_dir=tmp_path / "reports"),
        on_item_done=memory.record,
    )
    item = _item(tmp_path, title="修复登录", ok=True)
    store.save(item)

    scheduler._record_memory(item.id)

    stats = memory.stats("repo-a")
    assert stats["total"] == 1 and stats["succeeded"] == 1


def test_merge_blocked_signature_is_not_passed(tmp_path: Path):
    memory = TaskMemory(tmp_path / "memory.json")
    item = _item(tmp_path, title="冲突任务", ok=True)
    item.merge_state = "blocked"
    memory.record(item)
    clusters = memory.clusters("repo-a")
    assert len(clusters) == 1
    assert "merge_blocked" in clusters[0]["signature"]
    assert clusters[0]["signature"] != "passed"
    assert "合并冲突" in clusters[0]["summary"]


def test_environment_failure_not_recorded(tmp_path: Path):
    memory = TaskMemory(tmp_path / "memory.json")
    item = _item(tmp_path, title="CLI 没装", ok=False)
    item.result.failure_scope = "environment"
    memory.record(item)
    assert memory.entries() == []
    assert memory.stats()["total"] == 0


def test_budget_failure_not_recorded(tmp_path: Path):
    memory = TaskMemory(tmp_path / "memory.json")
    item = _item(tmp_path, title="预算超了", ok=False)
    item.result.failure_scope = "budget"
    memory.record(item)
    assert memory.entries() == []
