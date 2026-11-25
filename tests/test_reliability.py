"""长跑可靠性（P1）：重启重排、租约回收、CI 续等、基线快进。"""

from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from taskagent.baseline import sync_baseline
from taskagent.events import EventBus
from taskagent.executor import ExecutorRegistry
from taskagent.gitmap import GitProjectRegistry
from taskagent.models import ItemStatus, LocalRepo, WorkItem
from taskagent.scheduler import Scheduler, SchedulerSettings
from taskagent.store import TaskStore


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    )


def _scheduler(tmp_path: Path, **overrides) -> tuple[Scheduler, TaskStore]:
    store = TaskStore(tmp_path / "tasks")
    scheduler = Scheduler(
        store=store,
        gitmap=GitProjectRegistry([]),
        executors=ExecutorRegistry(),
        events=EventBus(),
        settings=SchedulerSettings(reports_dir=tmp_path / "reports", **overrides),
    )
    return scheduler, store


def _auto_item(tmp_path: Path, **fields) -> WorkItem:
    item = WorkItem.create(title="无人值守任务", description="x", kind="bug", source="tapd")
    item.repo = LocalRepo(path=str(tmp_path / "repo"), origin="", name="repo")
    item.auto = True
    item.exec_mode = "apply"
    item.auto_dispatched_at = "2026-01-01T00:00:00+00:00"
    for key, value in fields.items():
        setattr(item, key, value)
    return item


# ── 重启恢复 ─────────────────────────────────────────────────────────────────
def test_recover_requeues_auto_and_fails_manual(tmp_path: Path):
    scheduler, store = _scheduler(tmp_path)
    auto = _auto_item(tmp_path, status=ItemStatus.RUNNING)
    manual = _auto_item(tmp_path, auto=False, status=ItemStatus.RUNNING)
    store.save(auto)
    store.save(manual)

    recovered = scheduler.recover_interrupted()
    assert recovered["requeued"] == [auto.id]
    assert recovered["failed"] == 1

    saved = store.get(auto.id)
    assert saved is not None
    assert saved.status == ItemStatus.READY
    assert saved.recovered == 1
    assert saved.auto_dispatched_at == ""
    assert saved.auto is True
    assert "重新排队" in saved.stage

    failed = store.get(manual.id)
    assert failed is not None and failed.status == ItemStatus.FAILED
    assert "服务重启" in failed.error

    # 第二遍不再重复处理。
    assert scheduler.recover_interrupted()["total"] == 0


def test_recover_respects_max_recoveries(tmp_path: Path):
    scheduler, store = _scheduler(tmp_path, automation_policy=lambda: {"max_recoveries": 0})
    item = _auto_item(tmp_path, status=ItemStatus.RUNNING)
    store.save(item)

    recovered = scheduler.recover_interrupted()
    assert recovered["requeued"] == []
    assert recovered["failed"] == 1
    assert store.get(item.id).status == ItemStatus.FAILED


# ── 租约回收 ─────────────────────────────────────────────────────────────────
def test_sweep_expired_lease_requeues_auto_only(tmp_path: Path):
    scheduler, store = _scheduler(tmp_path)
    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat(timespec="seconds")
    future = (datetime.now(UTC) + timedelta(minutes=5)).isoformat(timespec="seconds")

    expired = _auto_item(tmp_path, status=ItemStatus.RUNNING, lease_owner="dead",
                         lease_until=past)
    alive = _auto_item(tmp_path, status=ItemStatus.RUNNING, lease_owner=scheduler.lease_owner,
                       lease_until=future)
    manual = _auto_item(tmp_path, auto=False, status=ItemStatus.RUNNING, lease_owner="dead",
                        lease_until=past)
    for item in (expired, alive, manual):
        store.save(item)

    requeued = scheduler.sweep_expired_leases()
    assert requeued == [expired.id]
    assert store.get(expired.id).status == ItemStatus.READY
    assert store.get(alive.id).status == ItemStatus.RUNNING
    assert store.get(manual.id).status == ItemStatus.FAILED


def test_crashed_execution_is_failed_not_stuck(tmp_path: Path):
    scheduler, store = _scheduler(tmp_path)
    item = _auto_item(tmp_path, status=ItemStatus.RUNNING)
    store.save(item)

    scheduler._mark_crashed(item.id, RuntimeError("boom"))

    saved = store.get(item.id)
    assert saved is not None
    assert saved.status == ItemStatus.FAILED
    assert "boom" in saved.error
    assert saved.lease_owner == ""


# ── CI 等待续跑 ──────────────────────────────────────────────────────────────
async def test_resume_ci_watches_reattaches_pending(tmp_path: Path, monkeypatch):
    scheduler, store = _scheduler(tmp_path, delivery_mode="commit")
    item = _auto_item(tmp_path, status=ItemStatus.SUCCEEDED)
    item.delivery = {"ci_pending": True, "branch": "taskagent/x", "pr_number": 7}
    store.save(item)

    called: list[str] = []

    async def fake_watch(item_id: str, policy) -> None:
        called.append(item_id)

    monkeypatch.setattr(scheduler, "_watch_ci", fake_watch)
    resumed = scheduler.resume_ci_watches()
    assert resumed == [item.id]
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert called == [item.id]


def test_resume_ci_clears_pending_when_delivery_off(tmp_path: Path):
    scheduler, store = _scheduler(tmp_path, delivery_mode="off")
    item = _auto_item(tmp_path, status=ItemStatus.SUCCEEDED)
    item.delivery = {"ci_pending": True, "branch": "taskagent/x"}
    store.save(item)

    assert scheduler.resume_ci_watches() == []
    saved = store.get(item.id)
    assert saved.delivery["ci_pending"] is False
    assert "交付已关闭" in saved.delivery["error"]


# ── 基线同步 ─────────────────────────────────────────────────────────────────
def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q", str(path)], check=True)
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "T")
    (path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path


def _remote(tmp_path: Path) -> tuple[Path, Path, Path]:
    """bare origin + seed clone (pusher) + clone (executor's local repo)."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "--initial-branch=main", str(origin)], check=True)
    seed = _init_repo(tmp_path / "seed")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "-u", "origin", "main")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    _git(clone, "config", "user.email", "t@example.com")
    _git(clone, "config", "user.name", "T")
    return origin, seed, clone


def _push_change(seed: Path, content: str) -> str:
    (seed / "app.py").write_text(content, encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "change")
    _git(seed, "push", "-q", "origin", "main")
    return _git(seed, "rev-parse", "HEAD").stdout.strip()


def test_baseline_fast_forwards_clean_behind_repo(tmp_path: Path):
    _origin, seed, clone = _remote(tmp_path)
    new_head = _push_change(seed, "VALUE = 2\n")

    state = sync_baseline(clone, mode="ff")
    assert state["state"] == "fast_forward"
    assert state["before"] != state["after"]
    assert _git(clone, "rev-parse", "HEAD").stdout.strip() == new_head
    assert (clone / "app.py").read_text() == "VALUE = 2\n"


def test_baseline_fetch_only_records_behind(tmp_path: Path):
    _origin, seed, clone = _remote(tmp_path)
    before = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _push_change(seed, "VALUE = 3\n")

    state = sync_baseline(clone, mode="fetch")
    assert state["state"] == "behind"
    assert state["behind"] == 1
    assert _git(clone, "rev-parse", "HEAD").stdout.strip() == before


def test_baseline_skips_fast_forward_when_dirty(tmp_path: Path):
    _origin, seed, clone = _remote(tmp_path)
    (clone / "app.py").write_text("LOCAL = 1\n", encoding="utf-8")
    _push_change(seed, "VALUE = 4\n")

    state = sync_baseline(clone, mode="ff")
    assert state["state"] == "dirty"
    assert (clone / "app.py").read_text() == "LOCAL = 1\n"


def test_baseline_skips_fast_forward_when_diverged(tmp_path: Path):
    _origin, seed, clone = _remote(tmp_path)
    (clone / "local.txt").write_text("x\n", encoding="utf-8")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", "local")
    _push_change(seed, "VALUE = 5\n")

    state = sync_baseline(clone, mode="ff")
    assert state["state"] == "diverged"
    assert "分叉" in state["error"]


def test_baseline_off_does_nothing(tmp_path: Path):
    _origin, seed, clone = _remote(tmp_path)
    _push_change(seed, "VALUE = 6\n")
    before = _git(clone, "rev-parse", "HEAD").stdout.strip()

    state = sync_baseline(clone, mode="off")
    assert state["state"] == "off"
    assert _git(clone, "rev-parse", "HEAD").stdout.strip() == before

async def test_executor_baseline_fetch_for_manual_ff_for_auto(tmp_path: Path, monkeypatch):
    from taskagent.executor import CodeBuddyExecutor

    executor = CodeBuddyExecutor(worktrees_dir=tmp_path / "wt", baseline_sync="ff")
    repo = LocalRepo(path=str(tmp_path / "repo"), origin="", name="repo")
    seen: list[str] = []

    def fake_sync(repo_path, *, mode, remote, timeout_s):
        seen.append(mode)
        return {"mode": mode, "state": "off"}

    monkeypatch.setattr("taskagent.executor.baseline_sync_mod.sync_baseline", fake_sync)

    manual = _auto_item(tmp_path, auto=False)
    await executor._sync_baseline(manual, repo, progress=lambda *_args: None)
    auto = _auto_item(tmp_path, auto=True)
    await executor._sync_baseline(auto, repo, progress=lambda *_args: None)

    # 人工 apply 绝不移动用户当前分支；只有自动任务允许快进。
    assert seen == ["fetch", "ff"]


def test_reused_worktree_refreshes_to_new_base(tmp_path: Path):
    from taskagent.executor import CodeBuddyExecutor

    _origin, seed, clone = _remote(tmp_path)
    item = _auto_item(tmp_path)
    item.repo = LocalRepo(path=str(clone), origin="", name="clone")
    executor = CodeBuddyExecutor(worktrees_dir=tmp_path / "wts", baseline_sync="off")
    target = tmp_path / "wt"

    assert executor._create_worktree(item.repo, target) is True
    new_head = _push_change(seed, "VALUE = 7\n")
    _git(clone, "fetch", "-q", "origin")
    _git(clone, "merge", "-q", "--ff-only", "origin/main")

    # 恢复/重试复用 worktree：主仓基线已前进，worktree 必须跟到新 HEAD。
    executor._refresh_worktree(item.repo, target)
    assert _git(target, "rev-parse", "HEAD").stdout.strip() == new_head


def test_reused_worktree_with_local_changes_keeps_old_base(tmp_path: Path):
    from taskagent.executor import CodeBuddyExecutor

    _origin, seed, clone = _remote(tmp_path)
    item = _auto_item(tmp_path)
    item.repo = LocalRepo(path=str(clone), origin="", name="clone")
    executor = CodeBuddyExecutor(worktrees_dir=tmp_path / "wts", baseline_sync="off")
    target = tmp_path / "wt"

    assert executor._create_worktree(item.repo, target) is True
    (target / "partial.txt").write_text("work in progress\n", encoding="utf-8")
    before = _git(target, "rev-parse", "HEAD").stdout.strip()
    _push_change(seed, "VALUE = 8\n")
    _git(clone, "fetch", "-q", "origin")
    _git(clone, "merge", "-q", "--ff-only", "origin/main")

    executor._refresh_worktree(item.repo, target)
    # 有未提交的中间结果：宁可基于旧基线继续，也不 reset 掉它们。
    assert _git(target, "rev-parse", "HEAD").stdout.strip() == before
    assert (target / "partial.txt").exists()


def test_baseline_does_not_ff_branch_without_upstream(tmp_path: Path):
    _origin, seed, clone = _remote(tmp_path)
    _git(clone, "checkout", "-q", "-b", "release/1.0")
    before = _git(clone, "rev-parse", "HEAD").stdout.strip()
    _push_change(seed, "VALUE = 9\n")

    state = sync_baseline(clone, mode="ff")
    assert state["state"] == "no_upstream"
    # 不能把 release/1.0 悄悄快进到 origin/main。
    assert _git(clone, "rev-parse", "HEAD").stdout.strip() == before
    assert _git(clone, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "release/1.0"


def test_baseline_skips_subdirectory_of_outer_repo(tmp_path: Path):
    _origin, seed, clone = _remote(tmp_path)
    sub = clone / "sub"
    sub.mkdir()
    (sub / "file.txt").write_text("x\n", encoding="utf-8")
    _push_change(seed, "VALUE = 10\n")
    before = _git(clone, "rev-parse", "HEAD").stdout.strip()

    state = sync_baseline(sub, mode="ff")
    assert state["state"] == "not_repo_root"
    # 外层仓库不属于这个映射，不能替它快进。
    assert _git(clone, "rev-parse", "HEAD").stdout.strip() == before


async def test_heartbeat_keeps_polling_while_queued_then_renews(tmp_path: Path):
    """重试路径会先把任务置 READY；心跳必须继续等，RUNNING 后再续租。"""
    scheduler, store = _scheduler(tmp_path, lease_ttl_s=3)
    item = _auto_item(tmp_path, status=ItemStatus.READY, auto_dispatched_at="")
    store.save(item)
    task = asyncio.create_task(scheduler._lease_heartbeat(item.id))
    try:
        await asyncio.sleep(1.1)  # 第一次检查：仍是 READY，不能退出
        current = store.get(item.id)
        current.status = ItemStatus.RUNNING
        current.lease_until = (datetime.now(UTC) - timedelta(minutes=1)).isoformat(
            timespec="seconds"
        )
        store.save(current)
        await asyncio.sleep(1.2)  # 第二次检查：续租
        renewed = store.get(item.id)
        assert renewed.lease_until > datetime.now(UTC).isoformat(timespec="seconds")
        assert renewed.lease_heartbeat_at
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def test_sweep_never_reclaims_live_runs(tmp_path: Path):
    scheduler, store = _scheduler(tmp_path)
    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat(timespec="seconds")
    item = _auto_item(tmp_path, status=ItemStatus.RUNNING, lease_until=past)
    store.save(item)
    scheduler._live_runs.add(item.id)
    try:
        assert scheduler.sweep_expired_leases() == []
        assert store.get(item.id).status == ItemStatus.RUNNING
    finally:
        scheduler._live_runs.discard(item.id)
