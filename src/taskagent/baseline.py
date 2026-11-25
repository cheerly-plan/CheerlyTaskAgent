"""执行前基线同步：让 worktree 不要从过期代码开始。

无人值守跑一夜的前提是「今天改的是今天的代码」。执行器创建 worktree 时用的是
**本地 HEAD**（再加上主仓库未提交的改动），如果远端已经前进、本地一直没
fetch，agent 会基于旧代码修改，合并回主仓时才发现冲突——这是长时间自动运行
最容易积累的一类假失败。

这里做三件事，全部是 best-effort，网络失败不阻塞执行：

1. ``git fetch --prune <remote>``；
2. 计算本地与上游的 ahead/behind；
3. ``mode="ff"`` 时，仅当「有上游、仅落后、工作区干净」做一次
   ``git merge --ff-only``。有未提交改动或已分叉时**不动**本地分支，只把
   事实记录进结果，由报告/界面提示。

``mode="off"`` 完全跳过；``mode="fetch"`` 只 fetch + 记录，不快进。
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MODES = ("off", "fetch", "ff")
DEFAULT_REMOTE = "origin"


def _git(repo: str | Path, *args: str, timeout: float) -> subprocess.CompletedProcess[str]:
    # 无人值守进程没有 TTY：禁止 git 弹凭据提示，否则 fetch 会一直等到超时。
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    argv = ["git", "-C", str(repo), "--no-pager", *args]
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=env,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        # 保持“never raises”的契约，让调用方按普通失败处理。
        return subprocess.CompletedProcess(argv, 124, "", f"{type(exc).__name__}: {exc}")


def _tail(text: str, limit: int = 300) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else "…" + text[-limit:]


def _upstream(repo: str | Path, timeout: float) -> str:
    """The branch's **configured** upstream, or "".

    Deliberately no ``<remote>/HEAD`` fallback: fast-forwarding a branch that
    has no upstream to whatever the remote default branch is would silently
    move a user's ``release/1.0`` onto ``main``.
    """
    found = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", timeout=timeout)
    return found.stdout.strip() if found.returncode == 0 else ""


def sync_baseline(
    repo_path: str | Path,
    *,
    mode: str = "ff",
    remote: str = DEFAULT_REMOTE,
    timeout_s: float = 20.0,
) -> dict[str, Any]:
    """Fetch (and optionally fast-forward) the repo before a task starts.

    Never raises: every failure becomes a ``state`` in the returned dict so the
    caller can execute anyway and surface the warning in the report.
    """
    mode = mode if mode in MODES else "ff"
    result: dict[str, Any] = {
        "mode": mode,
        "remote": remote,
        "state": "off",
        "upstream": "",
        "ahead": 0,
        "behind": 0,
        "before": "",
        "after": "",
        "error": "",
    }
    if mode == "off":
        return result
    repo = Path(repo_path).expanduser()
    if not repo.is_dir():
        result.update(state="error", error=f"仓库目录不存在：{repo}")
        return result
    timeout = max(1.0, float(timeout_s))

    if _git(repo, "rev-parse", "--is-inside-work-tree", timeout=timeout).returncode != 0:
        result.update(state="not_git", error="不是 git 工作区")
        return result
    # 只处理“映射目录本身就是仓库根”的情况。映射到子目录时外层仓库与本任务
    # 无关，绝不能替用户 fetch/快进外层仓库。
    top = _git(repo, "rev-parse", "--show-toplevel", timeout=timeout)
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != repo.resolve():
        result.update(state="not_repo_root", error="映射目录不是 git 仓库根目录")
        return result

    before = _git(repo, "rev-parse", "--short", "HEAD", timeout=timeout)
    result["before"] = before.stdout.strip() if before.returncode == 0 else ""

    fetched = _git(repo, "fetch", "--prune", "--quiet", remote, timeout=timeout)
    if fetched.returncode != 0:
        result.update(
            state="fetch_failed",
            error=_tail(fetched.stderr or fetched.stdout or "git fetch 失败"),
        )
        logger.warning("基线 fetch 失败（%s）：%s", repo, result["error"])
        return result

    upstream = _upstream(repo, timeout)
    result["upstream"] = upstream
    if not upstream:
        result.update(state="no_upstream")
        return result

    counts = _git(repo, "rev-list", "--left-right", "--count", f"HEAD...{upstream}", timeout=timeout)
    if counts.returncode == 0:
        parts = counts.stdout.split()
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            result["ahead"] = int(parts[0])
            result["behind"] = int(parts[1])
    ahead, behind = int(result["ahead"]), int(result["behind"])

    if behind == 0:
        result["state"] = "ahead" if ahead else "up_to_date"
        return result
    if mode != "ff":
        result["state"] = "behind"
        return result

    if ahead:
        result.update(state="diverged", error="本地与上游已分叉，未快进")
        return result
    # 未跟踪文件不会阻止快进；若真的与上游文件冲突，git 会安全地拒绝。
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=no", timeout=timeout)
    if dirty.returncode != 0:
        result.update(state="dirty_check_failed", error=_tail(dirty.stderr))
        return result
    if dirty.stdout.strip():
        result.update(state="dirty", error="工作区有未提交改动，未快进")
        return result

    merged = _git(repo, "merge", "--ff-only", upstream, timeout=max(timeout, 60.0))
    if merged.returncode != 0:
        result.update(state="ff_failed", error=_tail(merged.stderr or merged.stdout))
        logger.warning("基线快进失败（%s）：%s", repo, result["error"])
        return result
    after = _git(repo, "rev-parse", "--short", "HEAD", timeout=timeout)
    result.update(
        state="fast_forward",
        after=after.stdout.strip() if after.returncode == 0 else "",
    )
    logger.info(
        "基线已快进：%s %s -> %s（落后 %d）",
        repo, result["before"] or "?", result["after"] or "?", behind,
    )
    return result


def describe(baseline: dict[str, Any] | None) -> str:
    """One-line, human-readable status for stage/report."""
    if not isinstance(baseline, dict) or not baseline:
        return ""
    state = str(baseline.get("state") or "")
    labels = {
        "off": "",
        "up_to_date": "基线已是最新",
        "fast_forward": (
            f"基线已快进 {baseline.get('before') or '?'}→{baseline.get('after') or '?'}"
        ),
        "ahead": "本地领先上游（未推送）",
        "behind": f"本地落后上游 {baseline.get('behind', 0)} 个提交（未快进）",
        "diverged": "本地与上游已分叉",
        "dirty": "工作区有未提交改动，跳过基线快进",
        "no_upstream": "仓库没有上游分支",
        "fetch_failed": f"基线 fetch 失败：{baseline.get('error') or ''}",
        "not_git": "不是 git 仓库",
        "ff_failed": f"基线快进失败：{baseline.get('error') or ''}",
        "dirty_check_failed": "无法检查工作区状态",
        "error": f"基线同步失败：{baseline.get('error') or ''}",
    }
    return labels.get(state, "")