"""Explicit project-name/alias -> local-folder mapping.

Complements :mod:`gitmap` (which discovers repos by git origin). Here the user
maintains a list of ``ProjectMapping`` entries; any alias appearing in an
email/work-item text resolves straight to a local folder, no git needed.

The list lives in one JSON file (``data/projects.json``) with atomic writes and
is loaded at startup. A seed file can pre-populate initial entries. Because the
API and scheduler share the same registry instance, UI edits take effect
immediately.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .models import LocalRepo, ProjectMapping


class ProjectMappingStore:
    """Persistence for the mapping list: one JSON array, atomic replace."""

    def __init__(self, path: Path, seed: Path | None = None) -> None:
        self.path = Path(path)
        self.seed = Path(seed) if seed else None
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[ProjectMapping]:
        path = self.path
        if not path.is_file() and self.seed and Path(self.seed).is_file():
            # first run: copy the seed so the user has a starting point
            data = json.loads(Path(self.seed).read_text(encoding="utf-8"))
            entries = data.get("mappings", []) if isinstance(data, dict) else data
            mappings = [ProjectMapping.from_dict(item) for item in entries]
            self.save(mappings)
            return mappings
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        entries = data.get("mappings", []) if isinstance(data, dict) else data
        return [ProjectMapping.from_dict(item) for item in entries]

    def save(self, mappings: list[ProjectMapping]) -> None:
        payload = {"mappings": [m.to_dict() for m in mappings]}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)


class ProjectRegistry:
    """In-memory mapping list with alias-based text resolution."""

    def __init__(self, store: ProjectMappingStore) -> None:
        self.store = store
        self._items: list[ProjectMapping] = store.load()

    def list(self) -> list[ProjectMapping]:
        return list(self._items)

    def get(self, mapping_id: str) -> ProjectMapping | None:
        return next((m for m in self._items if m.id == mapping_id), None)

    def find_by_workspace(self, workspace_id: str) -> ProjectMapping | None:
        """找到绑定到该 TAPD workspace_id 的本地项目映射（双向关联）。"""
        wid = str(workspace_id or "").strip()
        if not wid:
            return None
        return next(
            (m for m in self._items if m.enabled and str(m.tapd_workspace_id).strip() == wid),
            None,
        )

    def add(self, data: dict) -> ProjectMapping:
        mapping = ProjectMapping.from_dict(data)
        if not mapping.name:
            raise ValueError("name is required")
        if not mapping.path:
            raise ValueError("path is required")
        if not Path(mapping.path).expanduser().is_dir():
            raise ValueError(f"local folder does not exist: {mapping.path}")
        if any(m.name.lower() == mapping.name.lower() for m in self._items):
            raise ValueError(f"duplicate project name: {mapping.name}")
        self._items.append(mapping)
        self.store.save(self._items)
        return mapping

    def update(self, mapping_id: str, data: dict) -> ProjectMapping:
        current = self.get(mapping_id)
        if current is None:
            raise ValueError(f"unknown mapping {mapping_id!r}")
        merged = current.to_dict()
        merged.update({k: v for k, v in data.items() if k != "id"})
        updated = ProjectMapping.from_dict(merged)
        if not updated.name:
            raise ValueError("name is required")
        if not updated.path:
            raise ValueError("path is required")
        if not Path(updated.path).expanduser().is_dir():
            raise ValueError(f"local folder does not exist: {updated.path}")
        if any(m.name.lower() == updated.name.lower() and m.id != mapping_id for m in self._items):
            raise ValueError(f"duplicate project name: {updated.name}")
        self._items = [updated if m.id == mapping_id else m for m in self._items]
        self.store.save(self._items)
        return updated

    def remove(self, mapping_id: str) -> bool:
        before = len(self._items)
        self._items = [m for m in self._items if m.id != mapping_id]
        if len(self._items) == before:
            return False
        self.store.save(self._items)
        return True

    def resolve_text(self, text: str) -> LocalRepo | None:
        """Return the folder for the first enabled alias found in ``text``.

        Matching rules:
          - case-insensitive
          - CJK aliases: plain substring
          - ASCII aliases: whole-word / delimiter-aware to avoid partial hits
          - longer aliases win (most specific), so 'CheerlyAgent' beats 'Agent'
        """
        if not text:
            return None
        candidates: list[tuple[int, str, ProjectMapping]] = []
        for mapping in self._items:
            if not mapping.enabled:
                continue
            # Always consider both the canonical name and the aliases.
            tokens = set(mapping.aliases) | ({mapping.name} if mapping.name else set())
            for alias in tokens:
                if self._alias_in_text(alias, text):
                    candidates.append((len(alias), alias.lower(), mapping))
                    break  # one hit per mapping is enough
        if not candidates:
            return None
        # most specific (longest alias) wins; stable tie-break by alias
        candidates.sort(key=lambda row: (-row[0], row[1]))
        return candidates[0][2].to_local_repo()

    def resolve_name(self, name: str) -> LocalRepo | None:
        """Exact name/alias lookup (used by the manual 'map' action)."""
        key = name.strip().lower()
        if not key:
            return None
        for mapping in self._items:
            if not mapping.enabled:
                continue
            if mapping.name.lower() == key or any(a.lower() == key for a in mapping.aliases):
                return mapping.to_local_repo()
        return None

    @staticmethod
    def _alias_in_text(alias: str, text: str) -> bool:
        alias = alias.strip()
        if not alias:
            return False
        haystack = text.lower()
        needle = alias.lower()
        if needle in haystack:
            # For ASCII tokens, guard against accidental substring matches.
            if re.fullmatch(r"[a-z0-9_.\-]+", needle):
                for match in re.finditer(re.escape(needle), haystack):
                    before = haystack[match.start() - 1] if match.start() else ""
                    after = haystack[match.end()] if match.end() < len(haystack) else ""
                    if (not before or not (before.isalnum() or before in "_-.")) and (
                        not after or not (after.isalnum() or after in "_-.")
                    ):
                        return True
                return False
            return True  # CJK / mixed aliases: plain substring is fine
        return False
