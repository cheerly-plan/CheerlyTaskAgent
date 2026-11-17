"""Commit-time file filtering.

After a CLI agent runs in an isolated worktree we collect its changed files.
Before anything is staged for a real git commit, this module splits that list
into files we are willing to commit and files we deliberately skip (build
output, dependency trees, secrets, local state, ...).

This is a safety net, not a substitute for .gitignore: it is applied at commit
time by TaskAgent itself, so even files an agent created outside the repo's
.gitignore are caught.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

# Patterns are matched against the POSIX-style path reported by git, relative
# to the worktree root. A trailing slash means "any path under this directory".
DEFAULT_IGNORE: tuple[str, ...] = (
    # dependency trees / build output
    "node_modules/",
    "vendor/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".pytest_tmp/",
    "dist/",
    "build/",
    "out/",
    "target/",
    ".next/",
    ".nuxt/",
    "coverage/",
    ".tox/",
    # lock files (regenerated, noisy in auto-commits)
    "*.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Cargo.lock",
    "poetry.lock",
    # secrets / local env
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "credentials*.json",
    # logs / caches / editor / OS
    "*.log",
    "*.tmp",
    "*.swp",
    "*.swo",
    ".DS_Store",
    "Thumbs.db",
    ".idea/",
    ".vscode/",
    ".cache/",
    # TaskAgent's own runtime state (never commit it into a target repo)
    "data/reports/",
    "data/worktrees/",
    "data/tasks/",
    ".taskagent/",
)


@dataclass
class CommitFilePlan:
    kept: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    skipped_reasons: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "kept": list(self.kept),
            "skipped": list(self.skipped),
            "skipped_reasons": dict(self.skipped_reasons),
        }


def _normalize(path: str) -> str:
    """Normalize a git-reported path to a forward-slash relative path."""
    value = path.strip().strip('"').replace("\\", "/")
    # git rename: "old -> new"; commit the new side.
    if " -> " in value:
        value = value.split(" -> ", 1)[1].strip()
    while value.startswith("./"):
        value = value[2:]
    return value.strip("/")


def _matches(path: str, pattern: str) -> bool:
    """Match one ignore pattern against a relative path."""
    pat = pattern.strip().replace("\\", "/")
    if not pat or pat.startswith("#"):
        return False
    if pat.endswith("/"):
        # directory pattern: match the dir itself or anything beneath it
        base = pat.rstrip("/")
        return path == base or path.startswith(base + "/")
    # match against the whole path and each path segment (basename patterns
    # like "*.log" should apply at any depth)
    if fnmatch.fnmatch(path, pat):
        return True
    segments = path.split("/")
    return any(fnmatch.fnmatch(seg, pat) for seg in segments)


class CommitFileFilter:
    def __init__(self, extra_patterns: list[str] | tuple[str, ...] = ()) -> None:
        self.extra_patterns = tuple(p for p in extra_patterns if p and p.strip())

    @property
    def patterns(self) -> tuple[str, ...]:
        return DEFAULT_IGNORE + self.extra_patterns

    def is_ignored(self, path: str) -> str | None:
        """Return the matching ignore pattern, or None if the path is allowed."""
        normalized = _normalize(path)
        if not normalized:
            return "empty"
        for pattern in self.patterns:
            if _matches(normalized, pattern):
                return pattern
        return None

    def classify(self, paths: list[str]) -> CommitFilePlan:
        plan = CommitFilePlan()
        for raw in paths:
            path = _normalize(raw)
            if not path:
                continue
            matched = self.is_ignored(path)
            if matched:
                plan.skipped.append(path)
                plan.skipped_reasons[path] = matched
            else:
                plan.kept.append(path)
        return plan


def load_extra_patterns(rules_file: Path | None) -> list[str]:
    """Read user-supplied ignore patterns (one per line, '#' comments)."""
    if not rules_file or not rules_file.is_file():
        return []
    try:
        lines = rules_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def save_extra_patterns(rules_file: Path, patterns: list[str]) -> None:
    """Atomically write user ignore patterns."""
    rules_file.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(
        p.strip() for p in patterns if p.strip() and not p.strip().startswith("#")
    )
    tmp = rules_file.with_suffix(rules_file.suffix + ".tmp")
    tmp.write_text(body + ("\n" if body else ""), encoding="utf-8")
    tmp.replace(rules_file)
