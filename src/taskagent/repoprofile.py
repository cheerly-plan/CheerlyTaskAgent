"""Structured repository profiles for multi-repo routing.

One TAPD project can be bound to several local repos ("会员中心" 同时涉及
backend / frontend / mobile). Picking one automatically needs evidence about what
each repo *is* — not an LLM guess.

This module builds a cheap, deterministic profile per local repo:

- dependency fingerprints (react → 前端, fastapi → 后端, react-native → 移动端)
- project manifest (name / description / keywords from package.json, pyproject…)
- README title + first lines
- top-level directory names
- language mix by file extension (sampled, depth-limited)
- recent commit subject keywords

Profiles are cached in ``data/repo-profiles.json`` and rebuilt only when the
repo changes (HEAD sha or a coarse fingerprint). They are plain data: a human
can read them, diff them, and override them.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── 依赖指纹 → 角色 ────────────────────────────────────────────────────────
# 只收录判定性强、误伤率低的依赖；宁可判不出来，也不要判错。
_DEP_ROLES: dict[str, str] = {
    # 前端
    "react": "frontend",
    "react-dom": "frontend",
    "vue": "frontend",
    "@angular/core": "frontend",
    "svelte": "frontend",
    "next": "frontend",
    "nuxt": "frontend",
    "vite": "frontend",
    # 移动端
    "react-native": "mobile",
    "expo": "mobile",
    "flutter": "mobile",
    "@capacitor/core": "mobile",
    # 后端
    "fastapi": "backend",
    "django": "backend",
    "flask": "backend",
    "express": "backend",
    "koa": "backend",
    "nestjs": "backend",
    "@nestjs/core": "backend",
    "uvicorn": "backend",
    "gunicorn": "backend",
    # 桌面/客户端
    "electron": "desktop",
    "tauri": "desktop",
}

_LANG_BY_EXT: dict[str, str] = {
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript",
    ".py": "Python",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".swift": "Swift",
    ".dart": "Dart",
    ".vue": "Vue",
    ".rb": "Ruby",
    ".php": "PHP",
    ".css": "CSS", ".scss": "CSS", ".less": "CSS",
}

_IGNORE_DIRS = {
    "node_modules", ".git", "dist", "build", "out", ".next", ".nuxt",
    "target", "vendor", "__pycache__", ".venv", "venv", ".idea", ".vscode",
    "coverage", ".pytest_cache", ".ruff_cache", ".mypy_cache", "Pods",
    ".gradle", "DerivedData", "tmp", "logs",
}

_MANIFESTS = ("package.json", "pyproject.toml", "requirements.txt", "go.mod", "Cargo.toml", "pubspec.yaml")


@dataclass
class RepoProfile:
    """Deterministic description of one local repo."""

    mapping_id: str
    name: str
    path: str
    # 角色推断结果：frontend / backend / mobile / desktop（空 = 判不出来）
    roles: list[str] = field(default_factory=list)
    # 项目清单里声明的身份信息
    package_name: str = ""
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    # README 标题与前几行
    readme_title: str = ""
    readme_excerpt: str = ""
    # 顶层目录名
    top_dirs: list[str] = field(default_factory=list)
    # 语言 → 文件数（采样）
    languages: dict[str, int] = field(default_factory=dict)
    # 关键依赖（命中的指纹）
    fingerprints: list[str] = field(default_factory=list)
    # 最近提交标题里的高频词
    commit_keywords: list[str] = field(default_factory=list)
    # 用于判断是否需要重建
    head_sha: str = ""
    built_at: str = ""
    # 用户手工覆盖（非空则优先于推断）
    override_roles: list[str] = field(default_factory=list)
    override_keywords: list[str] = field(default_factory=list)

    def effective_roles(self) -> list[str]:
        return self.override_roles or self.roles

    def search_text(self) -> str:
        """All free text used for keyword matching, lower-cased."""
        parts = [
            self.name,
            self.package_name,
            self.description,
            " ".join(self.keywords),
            " ".join(self.override_keywords),
            self.readme_title,
            self.readme_excerpt,
            " ".join(self.top_dirs),
            " ".join(self.commit_keywords),
        ]
        return " ".join(p for p in parts if p).lower()

    def to_dict(self) -> dict[str, Any]:
        return {
            "mapping_id": self.mapping_id,
            "name": self.name,
            "path": self.path,
            "roles": list(self.roles),
            "package_name": self.package_name,
            "description": self.description,
            "keywords": list(self.keywords),
            "readme_title": self.readme_title,
            "readme_excerpt": self.readme_excerpt,
            "top_dirs": list(self.top_dirs),
            "languages": dict(self.languages),
            "fingerprints": list(self.fingerprints),
            "commit_keywords": list(self.commit_keywords),
            "head_sha": self.head_sha,
            "built_at": self.built_at,
            "override_roles": list(self.override_roles),
            "override_keywords": list(self.override_keywords),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepoProfile:
        return cls(
            mapping_id=str(data.get("mapping_id", "")),
            name=str(data.get("name", "")),
            path=str(data.get("path", "")),
            roles=[str(r) for r in data.get("roles") or []],
            package_name=str(data.get("package_name", "")),
            description=str(data.get("description", "")),
            keywords=[str(k) for k in data.get("keywords") or []],
            readme_title=str(data.get("readme_title", "")),
            readme_excerpt=str(data.get("readme_excerpt", "")),
            top_dirs=[str(d) for d in data.get("top_dirs") or []],
            languages={str(k): int(v) for k, v in (data.get("languages") or {}).items()},
            fingerprints=[str(f) for f in data.get("fingerprints") or []],
            commit_keywords=[str(k) for k in data.get("commit_keywords") or []],
            head_sha=str(data.get("head_sha", "")),
            built_at=str(data.get("built_at", "")),
            override_roles=[str(r) for r in data.get("override_roles") or []],
            override_keywords=[str(k) for k in data.get("override_keywords") or []],
        )


def _run_git(repo: Path, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _head_sha(repo: Path) -> str:
    return _run_git(repo, "rev-parse", "HEAD")[:40]


def _commit_keywords(repo: Path, limit: int = 80) -> list[str]:
    """高频提交词：来自 git log 标题，去噪后取前 8 个。"""
    log = _run_git(repo, "log", f"-{limit}", "--pretty=%s")
    if not log:
        return []
    counts: Counter[str] = Counter()
    for line in log.splitlines():
        for token in re.split(r"[\s:：,，\-_/()（）\[\]【】]+", line.lower()):
            token = token.strip()
            if len(token) < 2 or token.isdigit():
                continue
            if token in {"feat", "fix", "chore", "docs", "refactor", "style", "test", "merge", "revert"}:
                continue
            counts[token] += 1
    return [word for word, _ in counts.most_common(8)]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _manifest_info(repo: Path) -> tuple[str, str, list[str], list[str]]:
    """Return (package_name, description, keywords, dependencies)."""
    pkg = repo / "package.json"
    if pkg.is_file():
        data = _read_json(pkg)
        deps = list((data.get("dependencies") or {}).keys()) + list((data.get("devDependencies") or {}).keys())
        keywords = data.get("keywords") or []
        return (
            str(data.get("name") or ""),
            str(data.get("description") or ""),
            [str(k) for k in keywords][:12],
            deps,
        )

    pyproject = repo / "pyproject.toml"
    if pyproject.is_file():
        try:
            text = pyproject.read_text(encoding="utf-8")
        except OSError:
            text = ""
        name = re.search(r'^name\s*=\s*"([^"]+)"', text, re.MULTILINE)
        desc = re.search(r'^description\s*=\s*"([^"]+)"', text, re.MULTILINE)
        deps: list[str] = []
        block = re.search(r"dependencies\s*=\s*\[(.*?)\]", text, re.DOTALL)
        if block:
            deps = [d.lower() for d in re.findall(r'"([^"]+)"', block.group(1))]
        return (
            name.group(1) if name else "",
            desc.group(1) if desc else "",
            [],
            deps,
        )

    for req in ("requirements.txt", "go.mod", "Cargo.toml", "pubspec.yaml"):
        manifest = repo / req
        if manifest.is_file():
            try:
                text = manifest.read_text(encoding="utf-8")
            except OSError:
                continue
            deps = [line.strip().lower().split("=")[0].split(">")[0] for line in text.splitlines() if line.strip()]
            return "", "", [], deps[:200]
    return "", "", [], []


def _readme(repo: Path) -> tuple[str, str]:
    for candidate in ("README.md", "README.MD", "readme.md", "README.rst", "README.txt"):
        path = repo / candidate
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = [line.strip() for line in text.splitlines()]
        title = next((line.lstrip("#").strip() for line in lines if line), "")
        body = " ".join(line for line in lines[1:12] if line and not line.startswith("#"))
        return title[:120], re.sub(r"\s+", " ", body)[:240]
    return "", ""


def _top_dirs(repo: Path, limit: int = 12) -> list[str]:
    try:
        entries = sorted(
            (p.name for p in repo.iterdir() if p.is_dir() and p.name not in _IGNORE_DIRS and not p.name.startswith(".")),
        )
    except OSError:
        return []
    return entries[:limit]


def _language_mix(repo: Path, max_files: int = 3000, max_depth: int = 3) -> dict[str, int]:
    """按扩展名统计文件数（限深、限数量，避免大仓库卡住）。"""
    counts: Counter[str] = Counter()
    seen = 0
    for path in repo.rglob("*"):
        if seen >= max_files:
            break
        try:
            rel = path.relative_to(repo)
        except ValueError:
            continue
        if any(part in _IGNORE_DIRS or part.startswith(".") for part in rel.parts):
            continue
        if len(rel.parts) > max_depth:
            continue
        if not path.is_file():
            continue
        seen += 1
        lang = _LANG_BY_EXT.get(path.suffix.lower())
        if lang:
            counts[lang] += 1
    return dict(counts.most_common(6))


def build_profile(mapping_id: str, name: str, path: str) -> RepoProfile:
    """Build (or rebuild) the deterministic profile for one local repo."""
    repo = Path(path).expanduser()
    profile = RepoProfile(mapping_id=mapping_id, name=name, path=str(repo))
    if not repo.is_dir():
        return profile

    package_name, description, keywords, deps = _manifest_info(repo)
    profile.package_name = package_name
    profile.description = description
    profile.keywords = keywords

    # 依赖指纹 → 角色（按出现顺序去重，保持稳定）
    hits: list[str] = []
    roles: list[str] = []
    lowered = {d.lower() for d in deps}
    for dep, role in _DEP_ROLES.items():
        if dep.lower() in lowered:
            hits.append(dep)
            if role not in roles:
                roles.append(role)
    # 目录名也是强信号（没有 manifest 的老仓库）：有 ios/android 目录 → 移动端
    top = _top_dirs(repo)
    has_native_dirs = (repo / "ios").is_dir() or (repo / "android").is_dir()
    if has_native_dirs and "mobile" not in roles:
        roles.append("mobile")
        hits.append("ios/android 目录")
    profile.fingerprints = hits
    profile.roles = roles
    profile.top_dirs = top

    profile.readme_title, profile.readme_excerpt = _readme(repo)
    profile.languages = _language_mix(repo)
    profile.commit_keywords = _commit_keywords(repo)
    profile.head_sha = _head_sha(repo)
    from .models import _now

    profile.built_at = _now()
    return profile


@dataclass
class RepoProfileStore:
    """JSON-backed cache of repo profiles (``data/repo-profiles.json``)."""

    path: Path
    _items: dict[str, RepoProfile] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._items = self._load()

    def _load(self) -> dict[str, RepoProfile]:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        rows = data.get("profiles", []) if isinstance(data, dict) else data
        out: dict[str, RepoProfile] = {}
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            profile = RepoProfile.from_dict(row)
            if profile.mapping_id:
                out[profile.mapping_id] = profile
        return out

    def save(self) -> None:
        payload = {"profiles": [p.to_dict() for p in self._items.values()]}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, mapping_id: str) -> RepoProfile | None:
        return self._items.get(mapping_id)

    def all(self) -> list[RepoProfile]:
        return list(self._items.values())

    def put(self, profile: RepoProfile) -> None:
        self._items[profile.mapping_id] = profile

    def remove(self, mapping_id: str) -> None:
        if self._items.pop(mapping_id, None) is not None:
            self.save()

    def need_rebuild(self, mapping_id: str, path: str) -> bool:
        """True when the repo is unknown, moved, or its HEAD changed."""
        profile = self._items.get(mapping_id)
        if profile is None or profile.path != str(Path(path).expanduser()):
            return True
        head = _head_sha(Path(path).expanduser())
        return bool(head) and head != profile.head_sha

    def rebuild(self, mapping_id: str, name: str, path: str) -> RepoProfile:
        profile = build_profile(mapping_id, name, path)
        self.put(profile)
        self.save()
        return profile


def ensure_profiles(
    store: RepoProfileStore,
    mappings: list[Any],
    *,
    force: bool = False,
) -> dict[str, int]:
    """Build profiles for every enabled mapping; rebuild stale ones.

    Returns counts so callers can log/observe. Deleting mappings also prunes
    their profiles so the file does not grow forever.
    """
    created = rebuilt = pruned = 0
    alive = {m.id for m in mappings}
    for mapping_id in [p.mapping_id for p in store.all()]:
        if mapping_id not in alive:
            store.remove(mapping_id)
            pruned += 1
    for mapping in mappings:
        if not getattr(mapping, "enabled", True):
            continue
        if store.get(mapping.id) is None:
            store.rebuild(mapping.id, mapping.name, mapping.path)
            created += 1
        elif force or store.need_rebuild(mapping.id, mapping.path):
            store.rebuild(mapping.id, mapping.name, mapping.path)
            rebuilt += 1
    return {"created": created, "rebuilt": rebuilt, "pruned": pruned}