from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from taskagent.events import EventBus
from taskagent.executor import CodeBuddyExecutor
from taskagent.gitmap import GitProjectRegistry
from taskagent.scheduler import Scheduler, SchedulerSettings
from taskagent.store import TaskStore


def pytest_configure(config: pytest.Config) -> None:
    """Give every test process its own basetemp.

    pytest's default parent (``$TMPDIR/pytest-of-<user>``) is shared, so two
    concurrent runs still race when creating it — in sandboxed environments the
    brokered ``mkdir`` raises ``FileExistsError`` even with ``exist_ok=True``.
    A per-PID directory keeps concurrent runs fully independent. An explicit
    ``--basetemp`` from the command line always wins.
    """
    if config.option.basetemp:
        return
    config.option.basetemp = str(
        Path(tempfile.gettempdir()) / f"pytest-taskagent-{os.getpid()}"
    )


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
