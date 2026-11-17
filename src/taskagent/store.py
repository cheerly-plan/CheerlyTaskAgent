"""Persistent work-item store.

One JSON file per item under ``data/tasks``. Atomic writes via temp + replace,
mirroring CheerlyHarness's SessionStore so a crash never leaves half a file.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import WorkItem


class TaskStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, item: WorkItem) -> None:
        item.touch()
        path = self.root / f"{item.id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(item.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(path)

    def get(self, item_id: str) -> WorkItem | None:
        path = self.root / f"{item_id}.json"
        if not path.is_file():
            return None
        try:
            return WorkItem.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError):
            return None

    def list(self, limit: int = 200) -> list[WorkItem]:
        items: list[WorkItem] = []
        for path in self.root.glob("work-*.json"):
            try:
                items.append(WorkItem.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError, KeyError):
                continue
        items.sort(key=lambda item: item.created_at, reverse=True)
        return items[: max(1, limit)]
