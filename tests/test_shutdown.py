from __future__ import annotations

import asyncio

import pytest

from taskagent.events import Event, EventBus


@pytest.mark.asyncio
async def test_event_bus_close_wakes_subscribers():
    bus = EventBus()
    q = bus.subscribe()
    bus.publish(Event("ping"))
    assert (await q.get()).type == "ping"

    bus.close()
    assert bus.closed
    # close() pushes a sentinel so a blocked queue.get() returns immediately
    assert await q.get() is None
    # publishing after close is a no-op
    bus.publish(Event("ignored"))
    assert q.empty()


@pytest.mark.asyncio
async def test_scheduler_shutdown_cancels_background_tasks():
    # Build a minimal scheduler directly (avoid depending on fixture plumbing).
    from pathlib import Path

    from taskagent.executor import CodeBuddyExecutor
    from taskagent.gitmap import GitProjectRegistry
    from taskagent.scheduler import Scheduler, SchedulerSettings
    from taskagent.store import TaskStore

    tmp = Path("/tmp/_shutdown_test")
    data = tmp / "data"
    store = TaskStore(data / "tasks")
    bus = EventBus()
    scheduler = Scheduler(
        store=store,
        gitmap=GitProjectRegistry([]),
        executor=CodeBuddyExecutor(worktrees_dir=data / "worktrees"),
        events=bus,
        settings=SchedulerSettings(reports_dir=data / "reports"),
    )

    started = asyncio.Event()

    async def forever():
        started.set()
        await asyncio.sleep(3600)

    task = scheduler._spawn(forever(), name="test-forever")
    await started.wait()
    assert task in scheduler._tasks
    assert not task.done()

    await scheduler.shutdown()

    assert task.cancelled() or task.done()
    assert not scheduler._tasks
