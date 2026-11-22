from __future__ import annotations

import asyncio
import json

import pytest

from taskagent.events import Event, EventBus, sse_frame


def test_sse_frame_marks_replayed_events_only():
    """Replayed frames carry a flag the UI uses to skip redundant reloads."""
    event = Event("item_updated", "w1", {"title": "x"})
    live = json.loads(sse_frame(event).removeprefix("data: ").strip())
    replayed = json.loads(sse_frame(event, replay=True).removeprefix("data: ").strip())
    assert "replay" not in live
    assert replayed["replay"] is True
    assert replayed["type"] == "item_updated"
    assert replayed["title"] == "x"


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
