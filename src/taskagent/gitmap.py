"""Git URL <-> local checkout mapping.

Scans configured roots for git repos, reads each ``origin``, normalizes URLs so
HTTPS/SSH/SCP forms compare equal, and resolves a git URL mentioned in an email
to the local checkout. No LLM needed — just string canonicalization.
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import LocalRepo

_SCP_RE = re.compile(r"^[^@\s]+@([^:]+):(.+)$")


def normalize_git_url(url: str) -> str:
    """Return a canonical key: 'github.com/owner/repo' (no scheme/.git/suffix).

    Examples that collapse to the same key:
      https://github.com/cheerly-plan/CheerlyTaskAgent.git
      git@github.com:cheerly-plan/CheerlyTaskAgent.git
      ssh://git@github.com/cheerly-plan/CheerlyTaskAgent
    """
    value = url.strip()
    if not value:
        return ""
    # SCP-like: git@host:owner/repo.git
    scp = _SCP_RE.match(value)
    if scp:
        value = f"{scp.group(1)}/{scp.group(2)}"
    else:
        # strip scheme and optional credentials
        value = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", value)
        value = re.sub(r"^[^@/]+@", "", value)
    value = value.split("?", 1)[0].split("#", 1)[0]
    value = value.rstrip("/")
    value = value.removesuffix(".git")
    parts = value.split("/")
    # keep host + path (drop nothing else); lower-case host only
    if parts:
        parts[0] = parts[0].lower()
    return "/".join(parts)


def extract_git_urls(text: str) -> list[str]:
    """Pull git URLs / repo shorthand out of free text (email body)."""
    found: list[str] = []
    patterns = [
        r"https?://[^\s<>\)\]]+?\.git",
        r"https?://(?:github|gitlab|git\.code\.tencent|das-git)[^\s<>\)\]]+",
        r"git@[^\s<>\)\]]+?\.git",
        # bare host/owner/repo, e.g. github.com/owner/repo (very common in email)
        r"(?:github|gitlab)\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            url = match.group(0).rstrip(".,;:!?")
            if url not in found:
                found.append(url)
    return found


class GitProjectRegistry:
    def __init__(self, roots: list[Path]) -> None:
        self.roots = [Path(root).expanduser() for root in roots]
        self._by_key: dict[str, LocalRepo] = {}
        self._by_name: dict[str, LocalRepo] = {}

    def scan(self) -> list[LocalRepo]:
        self._by_key.clear()
        self._by_name.clear()
        seen: set[Path] = set()
        for root in self.roots:
            if not root.is_dir():
                continue
            for child in sorted(root.iterdir()):
                if not child.is_dir() or child in seen:
                    continue
                seen.add(child)
                origin = self._origin_of(child)
                if not origin:
                    continue
                repo = LocalRepo(path=str(child.resolve()), origin=origin, name=child.name)
                key = normalize_git_url(origin)
                if key:
                    self._by_key[key] = repo
                self._by_name[child.name.lower()] = repo
        return self.list()

    @staticmethod
    def _origin_of(path: Path) -> str | None:
        # Read .git/config directly to avoid invoking git in tests/sandboxes.
        config = path / ".git" / "config"
        if not config.is_file():
            return None
        try:
            text = config.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        in_origin = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                in_origin = stripped == '[remote "origin"]'
                continue
            if in_origin and stripped.startswith("url"):
                parts = stripped.split("=", 1)
                if len(parts) == 2:
                    return parts[1].strip()
        return None

    def list(self) -> list[LocalRepo]:
        return sorted(self._by_name.values(), key=lambda item: item.name.lower())

    def resolve(self, git_url: str = "", name: str = "") -> LocalRepo | None:
        if git_url:
            key = normalize_git_url(git_url)
            if key in self._by_key:
                return self._by_key[key]
            # try suffix match (host omitted in the email)
            for stored_key, repo in self._by_key.items():
                if stored_key.endswith(key) or key.endswith(stored_key):
                    return repo
        if name:
            return self._by_name.get(name.strip().lower())
        return None
