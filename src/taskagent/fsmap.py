"""Read-only local folder browsing + on-demand project scanning.

The web UI cannot obtain an absolute path from a native folder picker
(browsers hide it), so the backend exposes a small, read-only directory
browser. The user clicks down from a safe root and each directory carries its
real absolute path; picking one yields the path stored in a project mapping.

Safety:
  - browsing is confined to ``root`` (default HOME) — no escaping via ``..``
    or absolute paths outside the boundary
  - only directories are ever listed/returned; nothing is read except
    ``.git/config`` to detect a repo origin
"""

from __future__ import annotations

from pathlib import Path

from .gitmap import GitProjectRegistry


class PathBoundaryError(ValueError):
    """Raised when a requested path escapes the allowed browse root."""


class FolderBrowser:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or Path.home()).expanduser().resolve()

    # -- internals ---------------------------------------------------------
    def _safe(self, raw: str) -> Path:
        """Resolve ``raw`` under root, rejecting anything outside it."""
        candidate = Path(raw).expanduser() if raw else self.root
        candidate = candidate.resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise PathBoundaryError(f"path is outside the allowed root: {self.root}")
        return candidate

    @staticmethod
    def _origin(path: Path) -> str:
        return GitProjectRegistry._origin_of(path) or ""

    # -- public API --------------------------------------------------------
    def browse(self, path: str = "") -> dict:
        """List sub-directories of ``path`` (default root), with real paths."""
        current = self._safe(path)
        if not current.is_dir():
            raise ValueError(f"not a directory: {current}")
        dirs = []
        try:
            children = sorted(
                (p for p in current.iterdir() if p.is_dir()),
                key=lambda p: p.name.lower(),
            )
        except OSError as error:
            raise ValueError(f"cannot list {current}: {error}") from error
        for child in children:
            if child.name.startswith("."):
                continue  # hide dot-directories
            dirs.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "is_git": bool(self._origin(child)),
                }
            )
        parent = None
        if current != self.root and self.root in current.parents:
            parent = str(current.parent)
        return {
            "current": str(current),
            "parent": parent,
            "at_root": current == self.root,
            "dirs": dirs,
        }

    def scan(self, path: str = "", depth: int = 2) -> dict:
        """Scan a folder for candidate projects (git repos first).

        Returns the picked folder itself plus its descendant directories up to
        ``depth`` levels. A candidate is any folder; ``is_git`` marks those with
        a readable origin so the UI can highlight them. ``candidate_name`` is a
        suggestion for the mapping name (repo name without .git suffix when a
        git origin exists, otherwise the folder name).
        """
        current = self._safe(path)
        if not current.is_dir():
            raise ValueError(f"not a directory: {current}")
        depth = max(0, min(depth, 4))

        found: dict[str, dict] = {}
        self._collect(current, found)
        self._walk(current, found, depth=depth, level=0)

        candidates = sorted(found.values(), key=lambda d: (not d["is_git"], d["name"].lower()))
        return {"root": str(current), "candidates": candidates}

    def _walk(self, folder: Path, found: dict[str, dict], *, depth: int, level: int) -> None:
        if level >= depth:
            return
        try:
            children = sorted(
                (p for p in folder.iterdir() if p.is_dir()),
                key=lambda p: p.name.lower(),
            )
        except OSError:
            return
        for child in children:
            if child.name.startswith(".") or child.name in {"node_modules", ".venv", "venv", "__pycache__"}:
                continue
            self._collect(child, found)
            self._walk(child, found, depth=depth, level=level + 1)

    def _collect(self, folder: Path, found: dict[str, dict]) -> None:
        origin = self._origin(folder)
        is_git = bool(origin)
        if is_git:
            suggested = origin.rstrip("/").removesuffix(".git").split("/")[-1]
        else:
            suggested = folder.name
        found[str(folder)] = {
            "name": folder.name,
            "path": str(folder),
            "origin": origin,
            "is_git": is_git,
            "candidate_name": suggested,
            "aliases": [folder.name] if folder.name else [],
        }
