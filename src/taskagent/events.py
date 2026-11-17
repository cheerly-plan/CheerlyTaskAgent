"""In-process event bus. SSE subscribers get a queue; schedulers publish here."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    type: str
    task_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._history: list[Event] = []
        self._max_history = 200
        self._closed = False

    def subscribe(self) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        self._subscribers.discard(queue)

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """Signal all SSE streams to stop and release their subscribers.

        Each subscriber gets a sentinel (None) pushed so a stream blocked on
        ``queue.get()`` wakes up immediately and exits, instead of hanging
        until its keepalive timeout during server shutdown.
        """
        self._closed = True
        for queue in list(self._subscribers):
            queue.put_nowait(None)  # type: ignore[arg-type]

    def publish(self, event: Event) -> None:
        if self._closed:
            return
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history.pop(0)
        for queue in list(self._subscribers):
            queue.put_nowait(event)

    def replay(self) -> list[Event]:
        return list(self._history)


def sse_frame(event: Event) -> str:
    payload = {"type": event.type, "task_id": event.task_id, **event.data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
