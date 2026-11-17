from __future__ import annotations

from pathlib import Path

import pytest

from taskagent.events import EventBus
from taskagent.executor import CodeBuddyExecutor
from taskagent.gitmap import GitProjectRegistry
from taskagent.scheduler import Scheduler, SchedulerSettings
from taskagent.store import TaskStore


@pytest.fixture
def fake_repos(tmp_path: Path) -> Path:
    """Create two fake local repos with origin remotes for mapping tests."""
    root = tmp_path / "projects"
    for name, origin in [
        ("app-one", "https://github.com/acme/app-one.git"),
        ("app-two", "git@github.com:acme/app-two.git"),
    ]:
        gitdir = root / name / ".git"
        gitdir.mkdir(parents=True)
        (gitdir / "config").write_text(
            f'[remote "origin"]\n\turl = {origin}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n',
            encoding="utf-8",
        )
    return root


@pytest.fixture
def env(tmp_path: Path, fake_repos: Path):
    data = tmp_path / "data"
    store = TaskStore(data / "tasks")
    gitmap = GitProjectRegistry([fake_repos])
    gitmap.scan()
    events = EventBus()
    executor = CodeBuddyExecutor(worktrees_dir=data / "worktrees")
    scheduler = Scheduler(
        store=store,
        gitmap=gitmap,
        executor=executor,
        events=events,
        settings=SchedulerSettings(max_concurrent=2, reports_dir=data / "reports"),
    )
    return scheduler, store, gitmap, events, data
