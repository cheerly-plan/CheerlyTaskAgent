"""Auto tasks on the same repo must be serialized; different repos stay parallel."""

from __future__ import annotations

import asyncio
from pathlib import Path

from taskagent.events import EventBus
from taskagent.executor import Executor
from taskagent.gitmap import GitProjectRegistry
from taskagent.models import RunResult, WorkItem
from taskagent.scheduler import Scheduler, SchedulerSettings
from taskagent.store import TaskStore


class RecordingExecutor(Executor):
    """Minimal backend that tracks the peak number of overlapping runs."""

    backend_id = "recording"
    label = "Recording"

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self._guard = asyncio.Lock()

    async def run(
        self, item: WorkItem, *, progress, should_cancel
    ) -> RunResult:
        async with self._guard:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.05)
            return RunResult(ok=True, dry_run=False, command="recording")
        finally:
            async with self._guard:
                self.active -= 1

    def build_command(self, item: WorkItem) -> list[str]:
        return ["recording"]

    @staticmethod
    def _changed_files(worktree: Path) -> list[str]:
        return []

    def commit_files(
        self, worktree: Path, *, kept: list[str], message: str, dry_run: bool = False
    ) -> dict[str, object]:
        return {"ok": True, "commit": "abc123", "branch": "main"}


def _scheduler(tmp_path: Path, fake_repos: Path, backend: RecordingExecutor) -> Scheduler:
    data = tmp_path / "data"
    gitmap = GitProjectRegistry([fake_repos])
    gitmap.scan()
    return Scheduler(
        store=TaskStore(data / "tasks"),
        gitmap=gitmap,
        executor=backend,
        events=EventBus(),
        settings=SchedulerSettings(max_concurrent=3, reports_dir=data / "reports"),
    )


async def _wait_done(scheduler: Scheduler, ids: list[str]) -> None:
    for _ in range(300):
        if all(scheduler.store.get(iid).is_terminal() for iid in ids):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("auto tasks did not finish")


async def test_same_repo_auto_tasks_are_serialized(tmp_path, fake_repos):
    backend = RecordingExecutor()
    scheduler = _scheduler(tmp_path, fake_repos, backend)
    ids = [
        scheduler.create_manual(
            title=f"同仓任务 {i}", repo_name="app-one", executor="recording"
        ).id
        for i in range(2)
    ]

    for iid in ids:
        scheduler.run_auto(iid)
    await _wait_done(scheduler, ids)

    assert backend.peak == 1, f"same-repo auto tasks overlapped: peak={backend.peak}"


async def test_cross_repo_auto_tasks_still_overlap(tmp_path, fake_repos):
    backend = RecordingExecutor()
    scheduler = _scheduler(tmp_path, fake_repos, backend)
    ids = [
        scheduler.create_manual(title="A", repo_name="app-one", executor="recording").id,
        scheduler.create_manual(title="B", repo_name="app-two", executor="recording").id,
    ]

    for iid in ids:
        scheduler.run_auto(iid)
    await _wait_done(scheduler, ids)

    assert backend.peak == 2, f"cross-repo auto tasks did not overlap: peak={backend.peak}"


async def test_same_parent_auto_tasks_are_serialized_across_repos(tmp_path, fake_repos):
    backend = RecordingExecutor()
    scheduler = _scheduler(tmp_path, fake_repos, backend)
    parent_ref = "tapd:story:900"
    ids = []
    for title, repo_name in [("子任务A", "app-one"), ("子任务B", "app-two")]:
        item = scheduler.create_manual(
            title=title, repo_name=repo_name, executor="recording"
        )
        item.parent_ref = parent_ref
        scheduler.store.save(item)
        ids.append(item.id)

    for iid in ids:
        scheduler.run_auto(iid)
    await _wait_done(scheduler, ids)

    assert backend.peak == 1, f"same-parent auto tasks overlapped: peak={backend.peak}"
