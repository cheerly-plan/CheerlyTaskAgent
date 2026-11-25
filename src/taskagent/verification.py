"""Post-execution verification gate.

Runs the repository's configured checks *inside the task's isolated worktree*
before the scheduler merges anything into the main working tree.

Hardening beyond the first MVP:

- environment whitelist (never inherit API keys / tokens)
- checks run the *worktree* code even when `.venv` is an editable install
- total verification budget in addition to per-check timeouts
- protected files (test config / CI / manifests / lockfiles) cannot be changed
- test/snapshot files may be added, or grown by pure insertions into an
  existing file; deleting/rewriting them blocks the merge.  The blocking test
  verdict is produced against the baseline (pre-agent) test files, so tests the
  agent wrote cannot grade themselves
- changed-file and diff-size caps
- one retry for flaky checks
- optional baseline run at the pre-agent commit: a check that fails on both
  sides is reported as pre-existing and does not block when ``baseline`` is
  ``advisory`` (``strict`` keeps the gate fail-closed, ``off`` disables it)
- output redaction + verification-log GC

Configuration lives in ``data/verify.json``::

    {
      "repos": {
        "CheerlyTaskAgent": {
          "checks": [
            {"id": "test", "category": "test", "label": "pytest",
             "command": [".venv/bin/python", "-m", "pytest", "-q"],
             "timeout_s": 300, "env": {"APP_ENV": "test"}}
          ],
          "link_dirs": [".venv", "web/node_modules"],
          "test_patterns": ["tests/*", "test_*.py"],
          "protected_patterns": ["pyproject.toml", ".github/workflows/*"],
          "max_changed_files": 30,
          "max_diff_lines": 2000,
          "total_timeout_s": 900,
          "retries": 1,
          "baseline": "advisory"
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
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .review import diff_stat

CATEGORIES: tuple[str, ...] = ("test", "lint", "typecheck", "build")
CATEGORY_LABELS = {"test": "test", "lint": "lint", "typecheck": "typecheck", "build": "build"}
DEFAULT_TIMEOUT_S = 300
DEFAULT_TOTAL_TIMEOUT_S = 900
DEFAULT_RETRIES = 1
DEFAULT_RETRY_DELAY_S = 1.0
DEFAULT_BASELINE = "strict"  # off | advisory | strict
DEFAULT_MAX_CHANGED_FILES = 30
DEFAULT_MAX_DIFF_LINES = 2_000
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
# Files that decide *how* the checks run. Letting the agent edit them (or CI)
# is equivalent to letting it weaken its own grader.
DEFAULT_PROTECTED_PATTERNS: tuple[str, ...] = (
    "pyproject.toml",
    "setup.cfg",
    "pytest.ini",
    "tox.ini",
    "conftest.py",
    "*/conftest.py",
    "package.json",
    "*/package.json",
    "pnpm-lock.yaml",
    "package-lock.json",
    "yarn.lock",
    "uv.lock",
    "poetry.lock",
    "Cargo.lock",
    "tsconfig*.json",
    "*/tsconfig*.json",
    ".github/workflows/*",
    "vitest.config.*",
    "jest.config.*",
    "playwright.config.*",
    "cypress.config.*",
    "Dockerfile",
    "docker-compose*",
    ".env*",
    "Makefile",
)

# Minimal environment; tests should not be able to read the server's secrets.
_ALLOWED_ENV = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE",
    "TMPDIR", "TZ", "SYSTEMROOT", "COMSPEC", "PATHEXT", "TERM",
)
_SECRET_NAME_RE = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|credential)", re.IGNORECASE
)
_SECRET_PAIR_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|passwd|credential)\b(\s*[:=]\s*)([^\s\"']+)"
)
_TAIL_CHARS = 4_000
_LOG_LIMIT = 2_000_000
_LOG_RETENTION_DAYS = 7
_LOG_MAX_FILES = 2_000

# Ruff/mypy/pyright emit diagnostics as ``file:line:col: CODE message`` or
# ``file:line:col: message``; the code token is uppercase/lower-case with
# digits and dashes (e.g. F401, ISC004, invalid-syntax).  Keeping these as
# structured lines matters: the raw ``tail`` is the *end* of the output, so
# for long lint runs the actual error locations get cut off and the feedback
# loop only sees help text / diff fragments it cannot act on.
_DIAGNOSTIC_RE = re.compile(
    r"^(?P<file>[^\s:]+):(?P<line>\d+):(?P<col>\d+):\s+"
    r"(?:(?P<code>[A-Za-z][A-Za-z0-9_-]*):\s+)?(?P<message>.+)$"
)
# Ruff's own format is two lines: ``CODE message`` then ``--> file:line:col``.
_RUFF_LOCATION_RE = re.compile(r"-->\s+(?P<file>[^\s:]+):(?P<line>\d+):(?P<col>\d+)")
_RUFF_RULE_RE = re.compile(
    r"^(?P<code>[A-Za-z][A-Za-z0-9_-]*)(?::\s+|\s+)(?P<message>\S.*)$"
)
_MAX_DIAGNOSTICS = 20
_MAX_DIAGNOSTIC_MSG = 240


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
        raw_env = item.get("env")
        env = (
            {str(key): str(value) for key, value in raw_env.items()}
            if isinstance(raw_env, dict) else {}
        )
        raw_limits = item.get("rlimits")
        rlimits = (
            {str(key): value for key, value in raw_limits.items()}
            if isinstance(raw_limits, dict) else {}
        )
        checks.append({
            "id": identifier or f"check-{index + 1}",
            "label": label,
            "category": category,
            "command": argv,
            "timeout_s": float(item.get("timeout_s") or default_timeout_s),
            "env": env,
            "rlimits": rlimits,
        })
    return checks


def parse_na(raw: Any, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """Normalise ``na`` waivers; supports both legacy strings and objects.

    An object may carry ``reason``, ``expires_at`` (ISO date/time) and audit
    fields.  Expired or malformed expiry dates no longer count as a waiver, so
    a temporary N/A cannot silently become permanent.
    """
    current = now or datetime.now(UTC)
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        category = str(key).strip().lower()
        if category not in CATEGORIES:
            continue
        if isinstance(value, str):
            reason = value.strip()
            meta: dict[str, Any] = {}
        elif isinstance(value, dict):
            reason = str(value.get("reason") or "").strip()
            meta = value
        else:
            continue
        if not reason:
            continue
        expires_at = str(meta.get("expires_at") or "").strip()
        expired = False
        if expires_at:
            try:
                # A plain YYYY-MM-DD means "valid through the end of that day".
                text = f"{expires_at}T23:59:59" if len(expires_at) == 10 else expires_at
                when = datetime.fromisoformat(text)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=UTC)
                expired = when < current
            except ValueError:
                expired = True
        out[category] = {
            "reason": reason,
            "expires_at": expires_at,
            "expired": expired,
            "approved_at": str(meta.get("approved_at") or ""),
            "approved_by": str(meta.get("approved_by") or ""),
        }
    return out


# ── guards ────────────────────────────────────────────────────────────────────
def _matches_patterns(path: str, patterns: list[str] | tuple[str, ...]) -> bool:
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


def is_test_file(path: str, patterns: list[str] | tuple[str, ...]) -> bool:
    """True when a changed path looks like a test/snapshot/fixture file."""
    return _matches_patterns(path, patterns)


def _protected_patterns(entry: dict[str, Any]) -> list[str]:
    raw = entry.get("protected_patterns")
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    return list(DEFAULT_PROTECTED_PATTERNS)


def _link_dirs(worktree: Path, repo_path: str, dirs: Any) -> list[str]:
    """Symlink dependency directories from the main checkout into a worktree."""
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
        if not source.is_dir():
            continue
        if target.is_symlink():
            # Reuse a link a previous gate run left behind, and report it so
            # the next cleanup removes it from the worktree's git status.
            try:
                if target.resolve() == source.resolve():
                    linked.append(rel)
            except OSError:
                pass
            continue
        if target.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source.resolve(), target_is_directory=True)
            linked.append(rel)
        except OSError:
            continue
    return linked


def _unlink_dirs(worktree: Path, linked: list[str]) -> None:
    """Remove the dependency symlinks the gate added to a live worktree.

    Leaving them behind made a later re-run's ``git status`` report ``.venv``
    and ``web/node_modules`` as agent changes — they must not be merged.
    """
    for rel in linked:
        target = worktree / rel
        try:
            if target.is_symlink():
                target.unlink()
        except OSError:
            continue


# ── environment / redaction ──────────────────────────────────────────────────
def _build_env(extra: dict[str, Any] | None) -> dict[str, str]:
    env = {key: os.environ[key] for key in _ALLOWED_ENV if key in os.environ}
    env.update({"CI": "1", "NO_COLOR": "1", "PYTHONUNBUFFERED": "1"})
    if isinstance(extra, dict):
        for key, value in extra.items():
            name = str(key).strip()
            if name:
                env[name] = str(value)
    return env


def _secret_values() -> list[str]:
    values: list[str] = []
    for key, value in os.environ.items():
        if value and len(value) >= 6 and _SECRET_NAME_RE.search(key):
            values.append(value)
    return values


def _redact(text: str, secrets: list[str]) -> str:
    output = text or ""
    for value in secrets:
        output = output.replace(value, "***")
    return _SECRET_PAIR_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}***", output)


# ── git helpers ──────────────────────────────────────────────────────────────
def _git(cwd: Path | str, *args: str, timeout: float = 15) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _is_git_worktree(path: Path) -> bool:
    proc = _git(path, "rev-parse", "--is-inside-work-tree")
    return bool(proc and proc.returncode == 0 and proc.stdout.strip() == "true")


# ── changed-file classification ──────────────────────────────────────────────
def _norm_path(value: str) -> str:
    text = str(value).replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text


def _expand_renames(changed: list[str]) -> list[str]:
    """Split ``old -> new`` porcelain entries into their two real paths.

    Older task records stored renames that way; treating them as one opaque
    path would let a renamed test file slip past the grader check.
    """
    expanded: list[str] = []
    for item in changed:
        text = str(item)
        if " -> " in text:
            old, new = text.split(" -> ", 1)
            expanded.extend(part.strip() for part in (old, new) if part.strip())
        elif text.strip():
            expanded.append(text)
    return expanded


def _kind_of(status: str) -> str:
    """Map a porcelain ``XY`` pair to added / modified / deleted."""
    pair = (status + "  ")[:2]
    if pair == "??" or pair[0] == "A":
        return "added"
    if "D" in pair:
        return "deleted"
    return "modified"


def _change_kinds(worktree: Path, changed: list[str]) -> dict[str, str]:
    """Classify each changed path as added / modified / deleted.

    Uses ``git status`` when the worktree is a checkout. Returns ``{}`` when
    that is impossible (no git, git error), which callers treat as "unknown"
    and therefore conservatively as modified.
    """
    if not _is_git_worktree(worktree):
        return {}
    proc = _git(
        worktree, "status", "--porcelain", "-z", "--no-renames", timeout=30,
    )
    if proc is None or proc.returncode != 0:
        return {}
    entries: dict[str, str] = {}
    for raw in proc.stdout.split("\0"):
        if len(raw) < 4:
            continue
        entries[_norm_path(raw[3:])] = raw[:2]
    kinds: dict[str, str] = {}
    for item in changed:
        key = _norm_path(item)
        if not key:
            continue
        status = entries.get(key)
        if status is None:
            # ``changed`` may carry a collapsed untracked directory (``dir/``).
            prefix = key if key.endswith("/") else key + "/"
            children = [
                value for path, value in entries.items() if path.startswith(prefix)
            ]
            if children:
                status = "??" if all(value == "??" for value in children) else "M "
        if status is None:
            # ... or a file below one: inherit the collapsed directory's state.
            parents = [
                path for path in entries if path.endswith("/") and key.startswith(path)
            ]
            if parents:
                status = entries[max(parents, key=len)]
        kinds[key] = _kind_of(status) if status is not None else "unknown"
    return kinds


def _path_kind(kinds: dict[str, str], path: str) -> str:
    return kinds.get(_norm_path(path), "unknown")


# Executable lines that can switch tests off.  A "pure insertion" diff is only
# allowed through when it does not add one of these: adding a skip marker on
# top of an existing test file is a one-line addition that weakens the suite.
_WEAKENING_ANY_RE = re.compile(r"\b(?:pytestmark\s*=|collect_ignore\s*=)")
_WEAKENING_TOP_RE = re.compile(
    r"^(?:pytest\.skip\s*\(|sys\.exit\s*\(|os\._exit\s*\(|raise\s+SystemExit\b)"
)
_WEAKENING_DECORATOR_RE = re.compile(r"^\s*@[\w.]*\b(?:skip|skipif|xfail)\b")


def _weakening_added_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    if _WEAKENING_DECORATOR_RE.match(line):
        return True
    if _WEAKENING_ANY_RE.search(line):
        return True
    return bool(not line[:1].isspace() and _WEAKENING_TOP_RE.match(stripped))


def _pure_addition(worktree: Path, rel: str) -> tuple[bool, str]:
    """True when a modified test file only inserted lines.

    This is a structural check (``git diff -U0``), not a semantic one: the
    merge verdict itself is still produced from the baseline tests, and the
    inserted cases are run afterwards as advisory output.  The added-line
    guard exists so a one-line ``skip``/``xfail``/``pytestmark`` cannot quietly
    disable existing tests.
    """
    diff = _git(
        worktree, "diff", "--no-color", "--no-renames", "-U0", "HEAD", "--", rel,
        timeout=30,
    )
    if diff is None or diff.returncode != 0:
        return False, "无法读取该测试文件的 diff"
    removed: list[str] = []
    added: list[str] = []
    for line in diff.stdout.splitlines():
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("+"):
            added.append(line[1:])
    if removed:
        return False, f"删改了 {len(removed)} 行已有测试代码"
    if not added:
        return False, "没有新增的测试内容"
    if any(_weakening_added_line(line) for line in added):
        return False, "新增内容含可能跳过/关闭测试的语句"
    return True, ""


# ── detached worktrees (baseline / grading) ──────────────────────────────────
def _add_detached_worktree(worktree: Path, target: Path) -> str:
    """Create ``target`` at the worktree's HEAD; return an error string or ''."""
    head = _git(worktree, "rev-parse", "HEAD")
    if head is None or head.returncode != 0 or not head.stdout.strip():
        return "无法确定基线提交"
    added = _git(
        worktree, "worktree", "add", "--detach", str(target), head.stdout.strip()
    )
    if added is None or added.returncode != 0:
        detail = (
            (added.stderr or added.stdout or "").strip()[:500]
            if added is not None else "git worktree 失败"
        )
        return f"创建基线 worktree 失败：{detail}"
    return ""


def _drop_detached_worktree(worktree: Path, target: Path) -> None:
    _git(worktree, "worktree", "remove", "--force", str(target))
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    _git(worktree, "worktree", "prune")


def _remove_path(path: Path) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()
    except OSError:
        pass


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    _remove_path(dst)
    if src.is_symlink():
        dst.symlink_to(os.path.realpath(src))
    else:
        shutil.copy2(src, dst)


def _copy_tree_entry(
    src: Path, dst: Path, test_patterns: list[str], rel: str
) -> None:
    """Replay one changed entry into the grading tree, skipping test files."""
    if src.is_symlink():
        _remove_path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.symlink_to(os.path.realpath(src))
        return
    if not src.exists():
        _remove_path(dst)
        return
    if src.is_dir():
        for child in src.rglob("*"):
            if child.is_dir() and not child.is_symlink():
                continue
            child_rel = f"{rel}/{child.relative_to(src).as_posix()}"
            if is_test_file(child_rel, test_patterns):
                continue
            _copy_file(child, dst / child.relative_to(src))
        return
    _copy_file(src, dst)


def _grading_worktree(
    *,
    worktree: Path,
    repo_path: str,
    changed: list[str],
    kinds: dict[str, str],
    test_patterns: list[str],
    item_id: str,
    data_dir: Path,
    link_dirs: Any,
) -> tuple[Path | None, str]:
    """Build the tree the *blocking* checks run in: agent source, baseline tests.

    Added test files are allowed to merge, but they must not take part in the
    verdict — otherwise an agent could weaken its own grader. This detached
    worktree starts at the pre-agent commit and replays only non-test changes.
    """
    root = (data_dir / "verification-grading").resolve()
    target = root / f"{_safe_name(item_id)}-{time.time_ns()}"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return None, f"创建评分目录失败：{error}"
    error = _add_detached_worktree(worktree, target)
    if error:
        return None, error
    try:
        _link_dirs(target, repo_path, link_dirs)
        for raw in changed:
            rel = _norm_path(raw)
            if not rel or rel.startswith("../") or Path(rel).is_absolute():
                continue
            if is_test_file(rel, test_patterns):
                continue
            if _path_kind(kinds, raw) == "deleted":
                _remove_path(target / rel)
            else:
                _copy_tree_entry(worktree / rel, target / rel, test_patterns, rel)
        return target, ""
    except OSError as error:
        _drop_detached_worktree(worktree, target)
        return None, f"复制改动到评分 worktree 失败：{error}"


def _run_baseline(
    *,
    worktree: Path,
    repo_path: str,
    check: dict[str, Any],
    item_id: str,
    data_dir: Path,
    timeout_s: float,
    env_extra: dict[str, Any],
    rlimits: dict[str, Any] | None,
    link_dirs: Any,
) -> dict[str, Any]:
    """Run one check at the pre-agent commit (worktree HEAD)."""
    # Resolve before creating the worktree: a relative target would be resolved
    # by `git -C <worktree> worktree add` inside the worktree, while the check
    # cwd resolves against the server cwd — the check then ran in an empty
    # phantom directory and every baseline looked broken (E902).
    data_dir = Path(data_dir).expanduser().resolve()
    base_root = data_dir / "verification-baselines"
    base = base_root / f"{_safe_name(item_id)}-{time.time_ns()}"
    base_root.mkdir(parents=True, exist_ok=True)
    error = _add_detached_worktree(worktree, base)
    if error:
        return {"ok": False, "error": error}
    try:
        _link_dirs(base, repo_path, link_dirs)
        return _run_check(
            base, check,
            item_id=f"{item_id}-baseline",
            log_dir=data_dir / "verifications",
            timeout_s=timeout_s,
            env_extra=env_extra,
            rlimits=rlimits,
        )
    finally:
        _drop_detached_worktree(worktree, base)


# ── runner ────────────────────────────────────────────────────────────────────
def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "check"


def _with_rlimits(
    argv: list[str], limits: Any
) -> tuple[list[str], dict[str, str]]:
    """Wrap a command in a tiny Python pre-exec that applies POSIX rlimits.

    Using a wrapper (instead of ``preexec_fn``) keeps this safe when the gate
    itself runs in a worker thread.  Network isolation is *not* provided here.
    """
    if not isinstance(limits, dict):
        return argv, {}
    cpu = float(limits.get("cpu_s") or 0)
    memory = int(limits.get("memory_mb") or 0)
    file_size = int(limits.get("file_size_mb") or 0)
    spec: dict[str, int] = {}
    if cpu > 0:
        spec["RLIMIT_CPU"] = int(cpu)
    if memory > 0:
        spec["RLIMIT_AS"] = memory * 1024 * 1024
    if file_size > 0:
        spec["RLIMIT_FSIZE"] = file_size * 1024 * 1024
    if not spec:
        return argv, {}
    code = (
        "import json,os,resource,sys\n"
        "limits=json.loads(os.environ.get('TASKAGENT_RLIMITS','{}'))\n"
        "for name,value in limits.items():\n"
        "    try:\n"
        "        resource.setrlimit(getattr(resource,name),(int(value),int(value)))\n"
        "    except (AttributeError,ValueError,OSError):\n"
        "        pass\n"
        "os.environ.pop('TASKAGENT_RLIMITS',None)\n"
        "os.execvp(sys.argv[1],sys.argv[1:])\n"
    )
    return (
        [sys.executable, "-c", code, *argv],
        {"TASKAGENT_RLIMITS": json.dumps(spec)},
    )


def extract_diagnostics(output: str) -> list[str]:
    """Pull ``file:line:col: CODE message`` lines out of a failed check.

    Lint/type-check output grows from the top: the actionable errors come
    first, while ``tail`` (the last N chars) mostly holds help text and diff
    suggestions.  The feedback loop needs the former.  Each returned line is
    self-contained and capped in both count and length so it stays within the
    feedback budget even for a noisy run.
    """
    found: list[str] = []
    seen: set[str] = set()

    def add(path: str, line_no: str, col: str, code: str, message: str) -> bool:
        message = _clip_diagnostic(message)
        if not message:
            return False
        entry = (
            f"{path}:{line_no}:{col}: "
            f"{code + ': ' if code else ''}{message}"
        )
        if entry in seen:
            return False
        seen.add(entry)
        found.append(entry)
        return len(found) >= _MAX_DIAGNOSTICS

    lines = (output or "").splitlines()
    # Ruff: rule text on one line, ``--> file:line:col`` on the next.  Pair
    # each location line with the nearest preceding non-decorative line.
    for index, raw in enumerate(lines):
        loc = _RUFF_LOCATION_RE.search(raw)
        if not loc:
            continue
        rule = ""
        for previous in reversed(lines[max(0, index - 4):index]):
            candidate = previous.strip()
            if not candidate or candidate.startswith(("|", "~", "^", "--")):
                continue
            rule = candidate
            break
        match = _RUFF_RULE_RE.match(rule)
        code = match.group("code") if match else ""
        message = match.group("message") if match else rule
        if add(loc.group("file"), loc.group("line"), loc.group("col"), code, message):
            break
    if found:
        return found

    # Generic single-line form: mypy / pyright / flake8 style.
    for raw in lines:
        match = _DIAGNOSTIC_RE.match(raw.strip())
        if not match:
            continue
        if add(
            match.group("file"), match.group("line"), match.group("col"),
            match.group("code") or "", match.group("message") or "",
        ):
            break
    return found


def _clip_diagnostic(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text[:_MAX_DIAGNOSTIC_MSG] + "…" if len(text) > _MAX_DIAGNOSTIC_MSG else text


def _run_check(
    worktree: Path,
    check: dict[str, Any],
    *,
    item_id: str,
    log_dir: Path,
    timeout_s: float | None = None,
    env_extra: dict[str, Any] | None = None,
    rlimits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    timeout = float(timeout_s if timeout_s is not None else check["timeout_s"])
    timeout = max(0.2, timeout)
    stdout = ""
    timed_out = False
    spawn_error = ""
    limits = rlimits if rlimits is not None else check.get("rlimits")
    argv, limit_env = _with_rlimits(list(check["command"]), limits)
    env = _build_env({**(env_extra or {}), **(check.get("env") or {}), **limit_env})
    if "PYTHONPATH" not in env:
        # A `.venv` editable install points at the *main* checkout, so a plain
        # `python -m pytest` would grade the main code and stay blind to the
        # worktree. Put this checkout (src/ layout + root) first instead.
        # Resolve it: relative entries in PYTHONPATH would be resolved against
        # the child's cwd and silently duplicate the path prefix.
        root = worktree.resolve()
        env["PYTHONPATH"] = os.pathsep.join([str(root / "src"), str(root)])
    try:
        proc = subprocess.Popen(
            argv,
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
            stdout, _ = proc.communicate(timeout=timeout)
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
    output = _redact(stdout or "", _secret_values())
    exit_code = proc.returncode if proc is not None else None
    ok = (not spawn_error) and (not timed_out) and exit_code == 0
    diagnostics = extract_diagnostics(output) if not ok else []

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
        "timeout_s": round(timeout, 2),
        "duration_s": round(time.monotonic() - started, 2),
        "error": spawn_error,
        "tail": output[-_TAIL_CHARS:],
        "diagnostics": diagnostics,
        "log_path": str(log_path) if log_path else "",
    }


def gc_verification_logs(
    data_dir: str | Path,
    *,
    retention_days: int = _LOG_RETENTION_DAYS,
    max_files: int = _LOG_MAX_FILES,
) -> int:
    """Best-effort GC for ``data/verifications``; returns removed file count."""
    root = Path(data_dir).expanduser() / "verifications"
    if not root.is_dir():
        return 0
    removed = 0
    now = time.time()
    files = [path for path in root.rglob("*") if path.is_file()]
    for path in files:
        try:
            if now - path.stat().st_mtime > retention_days * 86_400:
                path.unlink()
                removed += 1
        except OSError:
            continue
    files = [path for path in root.rglob("*") if path.is_file()]
    if len(files) > max_files:
        files.sort(key=lambda item: item.stat().st_mtime)
        for path in files[: len(files) - max_files]:
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
    return removed


def _failed_reason(outcome: dict[str, Any]) -> str:
    if outcome.get("timed_out"):
        return f"{outcome['label']} 超时（{outcome['duration_s']}s）"
    if outcome.get("error"):
        return f"{outcome['label']} 无法执行：{outcome['error']}"
    return f"{outcome['label']} 未通过（退出码 {outcome['exit_code']}）"


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
    total_timeout_s: float = DEFAULT_TOTAL_TIMEOUT_S,
) -> dict[str, Any]:
    """Run the gate and return a JSON-able verdict.

    ``ok=False`` means "do not merge".  ``state`` is one of: ``passed``,
    ``passed_with_warnings``, ``failed``, ``incomplete``, ``unconfigured``,
    ``no_changes``, ``test_files_changed``, ``protected_files_changed``,
    ``too_many_files``, ``diff_too_large``, ``error``, ``disabled``.

    Test/snapshot files may only be *added*: modifying or deleting one blocks
    the merge (a human can still force it).  Blocking test checks run against a
    detached worktree that keeps the pre-agent test files and replays only the
    agent's non-test changes, so tests written by the agent cannot grade their
    own work.  Those new tests are then run as advisory output.
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
    changed = _expand_renames(changed)

    na_meta = parse_na(entry.get("na"))
    na = {
        category: str(meta["reason"])
        for category, meta in na_meta.items()
        if not meta.get("expired")
    }
    expired_na = [category for category, meta in na_meta.items() if meta.get("expired")]
    present = {str(check.get("category")) for check in checks}
    missing = [
        category for category in CATEGORIES
        if category not in present and category not in na
    ]

    raw_patterns = entry.get("test_patterns")
    if isinstance(raw_patterns, str):
        raw_patterns = [
            part.strip() for part in raw_patterns.replace("，", ",").split(",") if part.strip()
        ]
    test_patterns = raw_patterns or list(DEFAULT_TEST_PATTERNS)

    path = Path(worktree).expanduser() if worktree else None
    if path is not None and not path.is_dir():
        path = None

    # New test files and pure *insertions* into existing test files are
    # allowed; rewrites, deletions and renames still need a human review.
    kinds = _change_kinds(path, changed) if path is not None else {}
    touched = [item for item in changed if is_test_file(item, test_patterns)]
    new_tests: list[str] = []
    augmented_tests: list[str] = []
    blocked_tests: list[str] = []
    block_reasons: dict[str, str] = {}
    for item in touched:
        kind = _path_kind(kinds, item)
        if kind == "added":
            new_tests.append(item)
        elif kind == "modified" and path is not None:
            allowed, why = _pure_addition(path, item)
            if allowed:
                augmented_tests.append(item)
            else:
                blocked_tests.append(item)
                block_reasons[item] = why
        else:
            blocked_tests.append(item)
            block_reasons[item] = (
                "删除或重命名了测试文件" if kind == "deleted" else "无法确认只新增用例"
            )
    if blocked_tests:
        details = "；".join(
            f"{name}（{block_reasons.get(name, '无法确认只新增用例')}）"
            for name in blocked_tests[:3]
        )
        return verdict(
            "test_files_changed",
            False,
            f"改动修改/删除了 {len(blocked_tests)} 个测试/快照文件（{details}），"
            "需要人工复核后才能合并",
            test_files=blocked_tests,
            test_file_reasons=block_reasons,
            test_files_added=new_tests,
            test_files_augmented=augmented_tests,
            na=na,
            na_meta=na_meta,
            expired_na=expired_na,
        )

    protected = [name for name in changed if _matches_patterns(name, _protected_patterns(entry))]
    if protected:
        return verdict(
            "protected_files_changed",
            False,
            f"改动包含 {len(protected)} 个受保护文件（测试配置/CI/清单/锁文件），自动链路不合并",
            protected_files=protected,
            na=na,
            na_meta=na_meta,
            expired_na=expired_na,
        )

    max_files = int(entry.get("max_changed_files", DEFAULT_MAX_CHANGED_FILES) or 0)
    if max_files > 0 and len(changed) > max_files:
        return verdict(
            "too_many_files",
            False,
            f"改动文件 {len(changed)} 个，超过上限 {max_files}",
            na=na,
            na_meta=na_meta,
            expired_na=expired_na,
        )

    if missing:
        labels = "、".join(CATEGORY_LABELS[category] for category in missing)
        return verdict(
            "incomplete",
            False,
            f"缺少验证类别：{labels}（未配置且未标 N/A）",
            missing_categories=missing,
            na=na,
            na_meta=na_meta,
            expired_na=expired_na,
        )

    if path is None:
        return verdict(
            "error", False, "worktree 不存在，无法验证",
            na=na, na_meta=na_meta, expired_na=expired_na,
        )

    max_lines = int(entry.get("max_diff_lines", DEFAULT_MAX_DIFF_LINES) or 0)
    if max_lines > 0:
        stat = diff_stat(path, changed)
        total_lines = int(stat.get("added", 0)) + int(stat.get("removed", 0))
        if total_lines > max_lines:
            return verdict(
                "diff_too_large",
                False,
                f"改动行数 {total_lines} 超过上限 {max_lines}",
                diff_lines=total_lines,
                na=na,
                na_meta=na_meta,
                expired_na=expired_na,
            )

    retries = max(0, int(entry.get("retries", DEFAULT_RETRIES) or 0))
    retry_delay = max(0.0, float(entry.get("retry_delay_s", DEFAULT_RETRY_DELAY_S) or 0))
    baseline_mode = str(entry.get("baseline") or DEFAULT_BASELINE).strip().lower()
    if baseline_mode not in {"off", "advisory", "strict"}:
        baseline_mode = DEFAULT_BASELINE
    total = float(entry.get("total_timeout_s") or total_timeout_s)
    deadline = started + max(0.1, total)
    base_env = entry.get("env") if isinstance(entry.get("env"), dict) else {}
    base_limits = entry.get("rlimits") if isinstance(entry.get("rlimits"), dict) else {}
    link_dirs = entry.get("link_dirs")

    linked = _link_dirs(path, repo_path, link_dirs)
    grading: Path | None = None
    test_additions = new_tests + augmented_tests
    has_test_check = any(str(check.get("category")) == "test" for check in checks)
    try:
        if test_additions and has_test_check:
            grading, grading_error = _grading_worktree(
                worktree=path,
                repo_path=repo_path,
                changed=changed,
                kinds=kinds,
                test_patterns=test_patterns,
                item_id=item_id,
                data_dir=Path(data_dir).expanduser(),
                link_dirs=link_dirs,
            )
            if grading is None:
                return verdict(
                    "error", False, f"无法准备基线测试评分 worktree：{grading_error}",
                    linked_dirs=linked, na=na, na_meta=na_meta, expired_na=expired_na,
                )
        return _run_checks(
            started=started,
            path=path,
            grading=grading,
            checks=checks,
            repo_path=repo_path,
            item_id=item_id,
            data_dir=Path(data_dir).expanduser(),
            total=total,
            deadline=deadline,
            base_env=base_env,
            base_limits=base_limits,
            link_dirs=link_dirs,
            retries=retries,
            retry_delay=retry_delay,
            baseline_mode=baseline_mode,
            na=na,
            na_meta=na_meta,
            expired_na=expired_na,
            added_tests=new_tests,
            augmented_tests=augmented_tests,
            linked=linked,
        )
    finally:
        _unlink_dirs(path, linked)
        if grading is not None:
            _drop_detached_worktree(path, grading)


def _run_checks(
    *,
    started: float,
    path: Path,
    grading: Path | None,
    checks: list[dict[str, Any]],
    repo_path: str,
    item_id: str,
    data_dir: Path,
    total: float,
    deadline: float,
    base_env: dict[str, Any],
    base_limits: dict[str, Any],
    link_dirs: Any,
    retries: int,
    retry_delay: float,
    baseline_mode: str,
    na: dict[str, str],
    na_meta: dict[str, Any],
    expired_na: list[str],
    added_tests: list[str],
    augmented_tests: list[str],
    linked: list[str],
) -> dict[str, Any]:
    """Execute the configured checks; test checks use the baseline grader."""
    results: list[dict[str, Any]] = []
    warnings: list[str] = []
    timeout_reason = ""
    passed = True

    def verdict(state: str, ok: bool, reason: str = "", **extra: Any) -> dict[str, Any]:
        return {
            "ok": ok,
            "state": state,
            "reason": reason,
            "checks": results,
            "duration_s": round(time.monotonic() - started, 2),
            **extra,
        }

    log_dir = data_dir / "verifications"
    for check in checks:
        # Test checks are graded with the agent's own test edits removed.
        check_path = (
            grading
            if grading is not None and str(check.get("category")) == "test"
            else path
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0.5:
            timeout_reason = f"总验证超时（{total:g}s）"
            passed = False
            break
        attempts = 0
        while True:
            attempts += 1
            remaining = deadline - time.monotonic()
            if remaining <= 0.5:
                timeout_reason = f"总验证超时（{total:g}s）"
                passed = False
                break
            check_limits = {**base_limits, **(check.get("rlimits") or {})}
            outcome = _run_check(
                check_path, check,
                item_id=item_id,
                log_dir=log_dir,
                timeout_s=min(float(check["timeout_s"]), remaining),
                env_extra=base_env,
                rlimits=check_limits,
            )
            outcome["attempts"] = attempts
            if outcome["ok"] or attempts > retries or outcome["error"]:
                break
            if deadline - time.monotonic() <= retry_delay + 0.2:
                break
            time.sleep(retry_delay)
        if not passed:
            break
        results.append(outcome)
        if outcome["ok"]:
            if attempts > 1:
                warnings.append(f"{check['label']} 第一次失败，重试通过（疑似 flaky）")
            continue

        if baseline_mode != "off" and _is_git_worktree(path):
            remaining = deadline - time.monotonic()
            baseline = _run_baseline(
                worktree=path,
                repo_path=repo_path,
                check=check,
                item_id=item_id,
                data_dir=data_dir,
                timeout_s=max(0.2, min(float(check["timeout_s"]), remaining)),
                env_extra=base_env,
                rlimits={**base_limits, **(check.get("rlimits") or {})},
                link_dirs=link_dirs,
            )
            outcome["baseline"] = baseline
            if baseline.get("ok"):
                passed = False
                break
            if (
                baseline_mode == "advisory"
                and not baseline.get("error")
                and not baseline.get("timed_out")
            ):
                outcome["pre_existing"] = True
                warnings.append(f"{check['label']} 在基线上同样失败（既有问题，放行）")
                continue
        passed = False
        break

    if passed:
        new_tests: list[dict[str, Any]] = []
        if added_tests or augmented_tests:
            for check in checks:
                if str(check.get("category")) != "test":
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0.5:
                    warnings.append("新增测试未运行（验证预算用尽）")
                    break
                outcome = _run_check(
                    path, check,
                    item_id=f"{item_id}-new-tests",
                    log_dir=log_dir,
                    timeout_s=min(float(check["timeout_s"]), remaining),
                    env_extra=base_env,
                    rlimits={**base_limits, **(check.get("rlimits") or {})},
                )
                outcome["attempts"] = 1
                outcome["advisory"] = True
                new_tests.append(outcome)
                if not outcome["ok"]:
                    warnings.append(
                        f"含新增测试的 {check['label']} 未通过（仅告警，不阻塞合并）"
                    )
        state = "passed_with_warnings" if warnings else "passed"
        reason = "全部验证通过" if not warnings else "通过（含既有失败/重试警告）"
        return verdict(
            state, True, reason,
            linked_dirs=linked, na=na, na_meta=na_meta,
            expired_na=expired_na, warnings=warnings,
            test_files_added=added_tests,
            test_files_augmented=augmented_tests,
            new_tests=new_tests,
        )

    if timeout_reason:
        return verdict(
            "failed", False, timeout_reason,
            linked_dirs=linked, na=na, na_meta=na_meta,
            expired_na=expired_na, warnings=warnings,
        )
    if not results:
        return verdict(
            "failed", False, "验证未能执行",
            linked_dirs=linked, na=na, na_meta=na_meta, expired_na=expired_na,
        )
    failed = results[-1]
    reason = _failed_reason(failed)
    baseline = failed.get("baseline") or {}
    if baseline and not baseline.get("ok"):
        reason = f"{reason}；基线同样失败" if baseline_mode != "advisory" else reason
    return verdict(
        "failed", False, reason,
        linked_dirs=linked, na=na, na_meta=na_meta,
        expired_na=expired_na, warnings=warnings,
    )
