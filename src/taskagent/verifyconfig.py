"""Auto-generate ``data/verify.json`` from repository layout.

Like repo profiles, detection is read-only (it never executes repo scripts):
at startup we fill in an entry for every mapped repo that has none yet, and a
manual "rebuild" regenerates all entries when the repo's scripts/configuration
change.

Stored shape (``data/verify.json``)::

    {
      "version": 1,
      "generated_at": "2026-11-24T00:00:00+00:00",
      "repos": {
        "CheerlyTaskAgent": {
          "path": "/abs/path",
          "source": "auto",
          "generated_at": "...",
          "fingerprint": "…",           # detects repo-file changes between rebuilds
          "checks": [
            {"id": "python-test", "label": "pytest", "category": "test",
             "command": [".venv/bin/python", "-m", "pytest", "-q"], "timeout_s": 300}
          ],
          "link_dirs": [".venv", "web/node_modules"],
          "test_patterns": ["tests/*", …]
        }
      }
    }

Existing entries are preserved by :func:`ensure_verify_config`; only an
explicit rebuild (``force=True``) overwrites them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .verification import CATEGORIES, DEFAULT_TEST_PATTERNS, DEFAULT_TIMEOUT_S

VERSION = 1
_SKIP_DIRS = {
    ".git", ".venv", "node_modules", "dist", "build", "data", "coverage",
    ".next", ".nuxt", ".pytest_cache", ".ruff_cache", ".mypy_cache", "__pycache__",
}
_ROOT_FILES = (
    "pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg", "ruff.toml",
    ".ruff.toml", "mypy.ini", "Makefile", "go.mod", "Cargo.toml",
    "package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json", "bun.lockb",
)
_SCRIPT_SPECS = (
    ("test", "test", "单元测试"),
    ("lint", "lint", "Lint"),
    ("typecheck", "typecheck", "类型检查"),
    ("type-check", "typecheck", "类型检查"),
    ("build", "build", "构建"),
)
_CATEGORY_ORDER = {"lint": 0, "typecheck": 1, "test": 2, "build": 3}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def load_verify_config(path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": VERSION, "repos": {}}
    if not isinstance(data, dict):
        return {"version": VERSION, "repos": {}}
    if not isinstance(data.get("repos"), dict):
        data["repos"] = {}
    return data


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


# ── detection ────────────────────────────────────────────────────────────────
def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _python_prefix(repo: Path) -> list[str]:
    if (repo / ".venv" / "bin" / "python").is_file():
        return [".venv/bin/python"]
    if (repo / "uv.lock").is_file() and shutil.which("uv"):
        return ["uv", "run"]
    return [sys.executable]


def _python_checks(repo: Path) -> list[dict[str, Any]]:
    text = _read_text(repo / "pyproject.toml")
    has_pytest = (
        "[tool.pytest" in text
        or (repo / "pytest.ini").is_file()
        or (repo / "tox.ini").is_file()
        or ((repo / "tests").is_dir() and (repo / "pyproject.toml").is_file())
    )
    if not has_pytest:
        return []
    prefix = _python_prefix(repo)
    checks: list[dict[str, Any]] = [{
        "id": "python-test",
        "label": "pytest",
        "category": "test",
        "command": [*prefix, "-m", "pytest", "-q"],
        "timeout_s": DEFAULT_TIMEOUT_S,
    }]
    targets = [name for name in ("src", "tests") if (repo / name).is_dir()] or ["."]
    if "[tool.ruff]" in text or (repo / "ruff.toml").is_file() or (repo / ".ruff.toml").is_file():
        checks.append({
            "id": "python-lint",
            "label": "ruff",
            "category": "lint",
            "command": [*prefix, "-m", "ruff", "check", *targets],
            "timeout_s": DEFAULT_TIMEOUT_S,
        })
    if "[tool.mypy]" in text or (repo / "mypy.ini").is_file():
        checks.append({
            "id": "python-typecheck",
            "label": "mypy",
            "category": "typecheck",
            "command": [*prefix, "-m", "mypy", *targets],
            "timeout_s": DEFAULT_TIMEOUT_S,
        })
    return checks


def _package_dirs(repo: Path, max_depth: int = 2) -> list[Path]:
    if not repo.is_dir():
        return []
    out: list[Path] = []
    for current, subdirs, files in os.walk(repo):
        here = Path(current)
        try:
            depth = len(here.relative_to(repo).parts)
        except ValueError:
            continue
        if depth >= max_depth:
            subdirs[:] = []
        subdirs[:] = [
            name for name in subdirs
            if name not in _SKIP_DIRS and not name.startswith(".")
        ]
        if "package.json" in files:
            out.append(here)
    return out


def _package_manager(pkg_dir: Path, repo: Path) -> str:
    for name, manager in (
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("bun.lockb", "bun"),
        ("package-lock.json", "npm"),
    ):
        if (pkg_dir / name).is_file() or (repo / name).is_file():
            return manager
    return "npm"


def _node_command(manager: str, rel: str, script: str) -> list[str]:
    if manager == "pnpm":
        return ["pnpm", *(["--dir", rel] if rel else []), "run", script]
    if manager == "yarn":
        return ["yarn", *(["--cwd", rel] if rel else []), "run", script]
    if manager == "bun":
        return ["bun", *(["--cwd", rel] if rel else []), "run", script]
    return ["npm", *(["--prefix", rel] if rel else []), "run", script]


def _node_exec(manager: str, rel: str, binary: str, *args: str) -> list[str]:
    if manager == "pnpm":
        return ["pnpm", *(["--dir", rel] if rel else []), "exec", binary, *args]
    if manager == "yarn":
        return ["yarn", *(["--cwd", rel] if rel else []), "exec", binary, *args]
    if manager == "bun":
        return ["bun", *(["--cwd", rel] if rel else []), "x", binary, *args]
    return ["npx", *(["--prefix", rel] if rel else []), binary, *args]


def _node_checks(repo: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for pkg_dir in _package_dirs(repo):
        try:
            data = json.loads((pkg_dir / "package.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        scripts = data.get("scripts") if isinstance(data, dict) else None
        if not isinstance(scripts, dict):
            continue
        rel = pkg_dir.relative_to(repo).as_posix()
        rel = "" if rel == "." else rel
        manager = _package_manager(pkg_dir, repo)
        prefix = f"node-{rel.replace('/', '-')}" if rel else "node"
        seen: set[str] = set()
        for script, category, label in _SCRIPT_SPECS:
            if script not in scripts or category in seen:
                continue
            seen.add(category)
            checks.append({
                "id": f"{prefix}-{category}",
                "label": f"{rel} {label}" if rel else label,
                "category": category,
                "command": _node_command(manager, rel, script),
                "timeout_s": DEFAULT_TIMEOUT_S,
            })
        # No typecheck script, but a tsconfig means `tsc -b` is a real check.
        if "typecheck" not in seen and (pkg_dir / "tsconfig.json").is_file():
            checks.append({
                "id": f"{prefix}-typecheck",
                "label": f"{rel} 类型检查" if rel else "类型检查",
                "category": "typecheck",
                "command": _node_exec(manager, rel, "tsc", "-b"),
                "timeout_s": DEFAULT_TIMEOUT_S,
            })
    return checks


def _go_checks(repo: Path) -> list[dict[str, Any]]:
    if not (repo / "go.mod").is_file():
        return []
    return [
        {
            "id": "go-test", "label": "go test", "category": "test",
            "command": ["go", "test", "./..."], "timeout_s": DEFAULT_TIMEOUT_S,
        },
        {
            "id": "go-lint", "label": "go vet", "category": "lint",
            "command": ["go", "vet", "./..."], "timeout_s": DEFAULT_TIMEOUT_S,
        },
        {
            "id": "go-build", "label": "go build", "category": "build",
            "command": ["go", "build", "./..."], "timeout_s": DEFAULT_TIMEOUT_S,
        },
    ]


def _rust_checks(repo: Path) -> list[dict[str, Any]]:
    if not (repo / "Cargo.toml").is_file():
        return []
    return [
        {
            "id": "rust-test", "label": "cargo test", "category": "test",
            "command": ["cargo", "test"], "timeout_s": DEFAULT_TIMEOUT_S,
        },
        {
            "id": "rust-lint", "label": "cargo check", "category": "lint",
            "command": ["cargo", "check"], "timeout_s": DEFAULT_TIMEOUT_S,
        },
        {
            "id": "rust-build", "label": "cargo build", "category": "build",
            "command": ["cargo", "build"], "timeout_s": DEFAULT_TIMEOUT_S,
        },
    ]


def _make_checks(repo: Path, existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fallback for repos that only expose Makefile targets."""
    makefile = repo / "Makefile"
    if not makefile.is_file():
        return []
    text = _read_text(makefile)
    targets = set(re.findall(r"^([A-Za-z0-9_.-]+)\s*:", text, flags=re.MULTILINE))
    have = {str(check.get("category")) for check in existing}
    out: list[dict[str, Any]] = []
    for target, category, label in (
        ("test", "test", "make test"),
        ("lint", "lint", "make lint"),
        ("build", "build", "make build"),
    ):
        if target in targets and category not in have:
            out.append({
                "id": f"make-{category}", "label": label, "category": category,
                "command": ["make", target], "timeout_s": DEFAULT_TIMEOUT_S,
            })
    return out


def detect_checks(repo_path: str | Path) -> list[dict[str, Any]]:
    """Infer test/lint/typecheck/build commands from files. Never executes."""
    repo = Path(repo_path).expanduser()
    if not str(repo_path).strip() or not repo.is_dir():
        return []
    checks: list[dict[str, Any]] = []
    checks.extend(_python_checks(repo))
    checks.extend(_node_checks(repo))
    checks.extend(_go_checks(repo))
    checks.extend(_rust_checks(repo))
    checks.extend(_make_checks(repo, checks))
    checks.sort(key=lambda check: (_CATEGORY_ORDER.get(str(check.get("category")), 9),
                                   str(check.get("id"))))
    return checks


def detect_link_dirs(repo_path: str | Path) -> list[str]:
    repo = Path(repo_path).expanduser()
    if not repo.is_dir():
        return []
    dirs: list[str] = []
    if (repo / ".venv").is_dir():
        dirs.append(".venv")
    for pkg_dir in _package_dirs(repo):
        if (pkg_dir / "node_modules").is_dir():
            rel = pkg_dir.relative_to(repo).as_posix()
            dirs.append(f"{rel}/node_modules" if rel != "." else "node_modules")
    return list(dict.fromkeys(dirs))


# ── fingerprints / staleness ─────────────────────────────────────────────────
def fingerprint(repo_path: str | Path) -> str:
    """Hash of the files that determine the generated commands."""
    repo = Path(repo_path).expanduser()
    if not repo.is_dir():
        return ""
    files: set[Path] = set()
    for name in _ROOT_FILES:
        candidate = repo / name
        if candidate.is_file():
            files.add(candidate)
    for pkg_dir in _package_dirs(repo):
        for name in (
            "package.json", "pnpm-lock.yaml", "yarn.lock",
            "package-lock.json", "bun.lockb",
        ):
            candidate = pkg_dir / name
            if candidate.is_file():
                files.add(candidate)
        for candidate in pkg_dir.glob("tsconfig*.json"):
            files.add(candidate)
    digest = hashlib.sha1()
    for path in sorted(files, key=str):
        try:
            stat = path.stat()
            rel = path.relative_to(repo).as_posix()
        except (OSError, ValueError):
            continue
        digest.update(f"{rel}:{stat.st_size}:{stat.st_mtime_ns}|".encode())
    return digest.hexdigest()[:16]


def is_stale(entry: dict[str, Any], repo_path: str | Path) -> bool:
    if not entry:
        return False
    return str(entry.get("fingerprint") or "") != fingerprint(repo_path)


# ── build / ensure ───────────────────────────────────────────────────────────
def build_entry(name: str, repo_path: str) -> dict[str, Any]:
    raw = str(repo_path or "").strip()
    repo = Path(raw).expanduser() if raw else None
    checks = detect_checks(repo) if repo is not None else []
    return {
        "name": name,
        "path": str(repo.resolve()) if repo is not None else "",
        "source": "auto",
        "generated_at": _now(),
        "fingerprint": fingerprint(repo) if repo is not None else "",
        "checks": checks,
        "link_dirs": detect_link_dirs(repo) if repo is not None else [],
        "test_patterns": list(DEFAULT_TEST_PATTERNS),
        "detected": bool(checks),
    }


def ensure_verify_config(
    path: str | Path,
    mappings: list[Any],
    *,
    force: bool = False,
    only: set[str] | None = None,
    prune: bool = False,
) -> dict[str, Any]:
    """Add entries for repos without one; ``force`` regenerates all.

    ``prune=True`` drops entries whose mapping no longer exists. Startup uses
    the default (no prune) so a config written before mappings load is kept.
    """
    config_path = Path(path).expanduser()
    data = load_verify_config(config_path)
    repos = data.setdefault("repos", {})
    alive = {str(getattr(mapping, "name", "")) for mapping in mappings}
    created = rebuilt = pruned = 0
    if prune:
        for name in list(repos):
            if name not in alive:
                repos.pop(name, None)
                pruned += 1
    for mapping in mappings:
        if not getattr(mapping, "enabled", True):
            continue
        name = str(getattr(mapping, "name", "")).strip()
        if not name or (only is not None and name not in only):
            continue
        exists = name in repos
        if exists and not force:
            continue
        entry = build_entry(name, str(getattr(mapping, "path", "")))
        if not entry["checks"] and not force:
            continue
        old = repos.get(name)
        if isinstance(old, dict) and isinstance(old.get("na"), dict):
            # Keep explicit N/A waivers across rebuilds, but drop ones whose
            # category now has a real check.
            present = {str(check.get("category")) for check in entry["checks"]}
            entry["na"] = {
                str(key): str(value)
                for key, value in old["na"].items()
                if str(key) not in present and str(value).strip()
            }
        repos[name] = entry
        if exists:
            rebuilt += 1
        else:
            created += 1
    if created or rebuilt or pruned:
        data["version"] = VERSION
        data["generated_at"] = _now()
        _write(config_path, data)
    return {"created": created, "rebuilt": rebuilt, "pruned": pruned, "repos": repos}


def set_na(
    path: str | Path,
    repo_name: str,
    na: dict[str, Any],
) -> dict[str, Any]:
    """Replace a repo's explicit N/A waivers (manual, audited)."""
    config_path = Path(path).expanduser()
    data = load_verify_config(config_path)
    repos = data.setdefault("repos", {})
    entry = repos.get(repo_name)
    if entry is None:
        raise LookupError(f"仓库 {repo_name} 还没有验证配置")
    if not isinstance(entry, dict):
        raise TypeError(f"仓库 {repo_name} 的验证配置格式不正确")
    cleaned: dict[str, dict[str, str]] = {}
    for category, value in (na or {}).items():
        category = str(category).strip().lower()
        if category not in CATEGORIES or not isinstance(value, dict):
            continue
        reason = str(value.get("reason") or "").strip()
        if not reason:
            continue
        cleaned[category] = {
            "reason": reason,
            "expires_at": str(value.get("expires_at") or "").strip(),
            "approved_at": _now(),
            "approved_by": "user",
        }
    entry["na"] = cleaned
    _write(config_path, data)
    return entry


def prune_verify_config(path: str | Path, alive_names: set[str]) -> int:
    config_path = Path(path).expanduser()
    data = load_verify_config(config_path)
    repos = data.setdefault("repos", {})
    removed = [name for name in list(repos) if name not in alive_names]
    for name in removed:
        repos.pop(name, None)
    if removed:
        _write(config_path, data)
    return len(removed)