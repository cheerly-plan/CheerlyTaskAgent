"""Post-execution verification gate (MVP).

Runs the repository's configured checks *inside the task's isolated worktree*
before the scheduler merges anything into the main working tree.  The gate is
deliberately small and fail-closed:

- no config / no checks / no worktree  -> block
- empty diff                           -> block
- agent touched a test file            -> block
- any check fails / times out / cannot start -> block

Configuration lives in ``data/verify.json`` (path configurable through
``SchedulerSettings.verify_config_file``) so this layer stays out of the UI for
now::

    {
      "repos": {
        "ChelyTaskAgent": {
          "checks": [
            {"id": "test", "label": "pytest",
             "command": [".venv/bin/python", "-m", "pytest", "-q"],
             "timeout_s": 300}
          ],
          "link_dirs": [".venv", "web/node_modules"],
          "test_patterns": ["tests/*", "test_*.py"]
        }
      }
    }

``TASKAGENT_VERIFY=0`` is the emergency off-switch; it lets auto tasks merge
without verification (recorded as ``state="disabled"``).
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

CATEGORIES: tuple[str, ...] = ("test", "lint", "typecheck", "build")
CATEGORY_LABELS = {"test": "test", "lint": "lint", "typecheck": "typecheck", "build": "build"}
DEFAULT_TIMEOUT_S = 300
DEFAULT_TEST_PATTERNS: tuple[str, ...] = (
    "tests/*",
    "*/tests/*",
    "__tests__/*",
    "*/__tests__/*",
    "test_*.py",
    "*_test.py",
    "*.test.*",
    "*.spec.*",
    "*.snap",
    "conftest.py",
    "fixtures/*",
    "*/fixtures/*",
)

_TAIL_CHARS = 4_000
_LOG_LIMIT = 2_000_000


# ── config ────────────────────────────────────────────────────────────────────
def load_config(path: str | Path) -> dict[str, Any]:
    """Read verify.json; a missing/corrupt file simply means "unconfigured"."""
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def repo_config(
    config: dict[str, Any], repo_name: str, repo_path: str = ""
) -> dict[str, Any]:
    """Look a repo up by name, then absolute path, then the ``*`` fallback."""
    repos = config.get("repos")
    if not isinstance(repos, dict):
        return {}
    for key in (repo_name, str(repo_path), "*"):
        if not key:
            continue
        entry = repos.get(key)
        if isinstance(entry, dict):
            return entry
    return {}


def _as_argv(command: Any) -> list[str]:
    if isinstance(command, str):
        return shlex.split(command)
    if isinstance(command, (list, tuple)):
        return [str(part) for part in command if str(part).strip()]
    return []


def _infer_category(identifier: str, label: str) -> str:
    text = f"{identifier} {label}".lower()
    if any(word in text for word in ("typecheck", "type-check", "tsc", "mypy", "pyright")):
        return "typecheck"
    if any(word in text for word in ("lint", "ruff", "eslint")):
        return "lint"
    if any(word in text for word in ("build", "vite", "compile")):
        return "build"
    if any(word in text for word in ("test", "pytest", "vitest", "jest")):
        return "test"
    return ""


def normalise_checks(
    raw: Any, default_timeout_s: float = DEFAULT_TIMEOUT_S
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return checks
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        argv = _as_argv(item.get("command"))
        if not argv:
            continue
        identifier = str(item.get("id") or f"check-{index + 1}").strip()
        label = str(item.get("label") or identifier or f"check-{index + 1}").strip()
        category = str(item.get("category") or "").strip().lower()
        if category not in CATEGORIES:
            category = _infer_category(identifier, label)
        checks.append({
            "id": identifier or f"check-{index + 1}",
            "label": label,
            "category": category,
            "command": argv,
            "timeout_s": float(item.get("timeout_s") or default_timeout_s),
        })
    return checks


# ── guards ────────────────────────────────────────────────────────────────────
def is_test_file(path: str, patterns: list[str] | tuple[str, ...]) -> bool:
    """True when a changed path looks like a test/snapshot/fixture file."""
    normalised = str(path).replace("\\", "/").lstrip("./")
    base = normalised.rsplit("/", 1)[-1]
    for raw in patterns or ():
        pattern = str(raw).strip()
        if not pattern:
            continue
        if "/" in pattern:
            if fnmatch.fnmatch(normalised, pattern):
                return True
            if fnmatch.fnmatch(normalised, pattern.rstrip("/") + "/*"):
                return True
        elif fnmatch.fnmatch(base, pattern):
            return True
    return False


def _link_dirs(worktree: Path, repo_path: str, dirs: Any) -> list[str]:
    """Symlink dependency directories from the main checkout into the worktree.

    The agent worktree is created from git, so ignored directories such as
    ``.venv`` / ``node_modules`` are absent.  Sharing them is an explicit
    trade-off (tests may write into them) but it is what makes verification
    runnable locally without reinstalling everything per task.
    """
    linked: list[str] = []
    if isinstance(dirs, str):
        dirs = [part.strip() for part in dirs.replace("，", ",").split(",") if part.strip()]
    if not isinstance(dirs, list):
        return linked
    for raw in dirs:
        rel = str(raw).strip().strip("/")
        if not rel or rel.startswith("..") or Path(rel).is_absolute():
            continue
        source = Path(repo_path).expanduser() / rel
        target = worktree / rel
        if not source.is_dir() or target.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source.resolve(), target_is_directory=True)
            linked.append(rel)
        except OSError:
            continue
    return linked


# ── runner ────────────────────────────────────────────────────────────────────
def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "check"


def _run_check(
    worktree: Path,
    check: dict[str, Any],
    *,
    item_id: str,
    log_dir: Path,
) -> dict[str, Any]:
    started = time.monotonic()
    stdout = ""
    timed_out = False
    spawn_error = ""
    env = os.environ.copy()
    env.update({"CI": "1", "NO_COLOR": "1", "PYTHONUNBUFFERED": "1"})
    try:
        proc = subprocess.Popen(
            check["command"],
            cwd=str(worktree),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            start_new_session=True,
        )
    except (OSError, ValueError) as error:
        spawn_error = f"{type(error).__name__}: {error}"
        proc = None
    if proc is not None:
        try:
            stdout, _ = proc.communicate(timeout=check["timeout_s"])
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except OSError:
                try:
                    proc.kill()
                except OSError:
                    pass
            try:
                stdout, _ = proc.communicate()
            except (OSError, ValueError):
                stdout = ""
    output = stdout or ""
    exit_code = proc.returncode if proc is not None else None
    ok = (not spawn_error) and (not timed_out) and exit_code == 0

    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{_safe_name(item_id)}-{_safe_name(str(check['id']))}.log"
    try:
        log_path.write_text(output[-_LOG_LIMIT:], encoding="utf-8", errors="replace")
    except OSError:
        log_path = None

    return {
        "id": check["id"],
        "label": check["label"],
        "command": list(check["command"]),
        "ok": ok,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_s": round(time.monotonic() - started, 2),
        "error": spawn_error,
        "tail": output[-_TAIL_CHARS:],
        "log_path": str(log_path) if log_path else "",
    }


def run_gate(
    *,
    repo_name: str,
    repo_path: str,
    worktree: str | None,
    changed: list[str],
    item_id: str,
    config_path: str | Path,
    data_dir: str | Path,
    default_timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Run the gate and return a JSON-able verdict.

    The return value is persisted on ``RunResult.verification`` and rendered in
    the Markdown report.  ``ok=False`` means "do not merge".
    """
    started = time.monotonic()
    results: list[dict[str, Any]] = []

    def verdict(state: str, ok: bool, reason: str = "", **extra: Any) -> dict[str, Any]:
        return {
            "ok": ok,
            "state": state,
            "reason": reason,
            "checks": results,
            "duration_s": round(time.monotonic() - started, 2),
            **extra,
        }

    if os.environ.get("TASKAGENT_VERIFY", "").strip() == "0":
        return verdict("disabled", True, "验证已禁用（TASKAGENT_VERIFY=0）")

    config = load_config(config_path)
    entry = repo_config(config, repo_name, repo_path)
    checks = normalise_checks(entry.get("checks"), default_timeout_s)
    if not checks:
        return verdict(
            "unconfigured",
            False,
            f"仓库 {repo_name or repo_path} 未配置验证命令（{config_path}）",
        )
    if not changed:
        return verdict("no_changes", False, "执行结果没有改动任何文件")
    raw_na = entry.get("na")
    na = {
        str(key).strip().lower(): str(value).strip()
        for key, value in (raw_na.items() if isinstance(raw_na, dict) else [])
        if str(value).strip()
    }
    present = {str(check.get("category")) for check in checks}
    missing = [
        category for category in CATEGORIES
        if category not in present and category not in na
    ]
    if missing:
        labels = "、".join(CATEGORY_LABELS[category] for category in missing)
        return verdict(
            "incomplete",
            False,
            f"缺少验证类别：{labels}（未配置且未标 N/A）",
            missing_categories=missing,
            na=na,
        )
    raw_patterns = entry.get("test_patterns")
    if isinstance(raw_patterns, str):
        raw_patterns = [
            part.strip() for part in raw_patterns.replace("，", ",").split(",") if part.strip()
        ]
    patterns = raw_patterns or list(DEFAULT_TEST_PATTERNS)
    touched = [path for path in changed if is_test_file(path, patterns)]
    if touched:
        return verdict(
            "test_files_changed",
            False,
            f"改动包含 {len(touched)} 个测试/快照文件，自动链路不合并",
            test_files=touched,
        )

    path = Path(worktree).expanduser() if worktree else None
    if path is None or not path.is_dir():
        return verdict("error", False, "worktree 不存在，无法验证")

    linked = _link_dirs(path, repo_path, entry.get("link_dirs"))
    log_dir = Path(data_dir).expanduser() / "verifications"
    passed = True
    for check in checks:
        outcome = _run_check(path, check, item_id=item_id, log_dir=log_dir)
        results.append(outcome)
        if not outcome["ok"]:
            passed = False
            break  # fail fast: the first failing required check is enough

    if passed:
        return verdict("passed", True, "全部验证通过", linked_dirs=linked, na=na)
    failed = results[-1]
    reason = (
        f"{failed['label']} 超时（{failed['duration_s']}s）"
        if failed["timed_out"]
        else f"{failed['label']} 未通过（退出码 {failed['exit_code']}）"
        if not failed["error"]
        else f"{failed['label']} 无法执行：{failed['error']}"
    )
    return verdict("failed", False, reason, linked_dirs=linked, na=na)