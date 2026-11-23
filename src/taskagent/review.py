"""改动 diff 工具：任务中心查看改动 / 排查合并冲突。

自动执行的结果留在隔离 worktree；合并冲突时主仓没有被写入，这里直接把
worktree 相对基线的 diff 展示出来，方便人工判断。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# ── diff：给待办页与人工评审看 ────────────────────────────────────────────────

def _git(worktree: Path, *args: str, limit: int = 8_000_000) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(worktree), "--no-pager", *args],
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    out = proc.stdout.decode("utf-8", errors="replace")
    return out[:limit]


def diff_stat(worktree: str | Path, changed: list[str]) -> dict[str, int]:
    """Added/deleted line counts for the agent's changes (HEAD..worktree)."""
    path = Path(worktree).expanduser()
    if not path.is_dir():
        return {"files": len(changed), "added": 0, "removed": 0}
    added = removed = 0
    numstat = _git(path, "diff", "--numstat", "HEAD")
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        a, d = parts[0], parts[1]
        if a.isdigit():
            added += int(a)
        if d.isdigit():
            removed += int(d)
    # Untracked files never appear in `git diff HEAD`; count their lines.
    tracked = {line.split("\t")[2] for line in numstat.splitlines() if len(line.split("\t")) >= 3}
    for rel in changed:
        if rel in tracked:
            continue
        target = path / rel
        if target.is_file():
            try:
                added += sum(1 for _ in target.open("rb"))
            except OSError:
                pass
    return {"files": len(changed), "added": added, "removed": removed}


def build_diff(worktree: str | Path, changed: list[str], limit: int = 200_000) -> str:
    """Unified diff for the todo detail view; truncated by bytes, not lines."""
    path = Path(worktree).expanduser()
    if not path.is_dir():
        return ""
    parts: list[str] = []
    tracked_diff = _git(path, "diff", "HEAD", "--no-color", "--", *changed) if changed else ""
    if tracked_diff:
        parts.append(tracked_diff)
    # New files are invisible to `git diff HEAD` until intent-to-add; show them
    # explicitly instead of mutating the worktree.
    for rel in changed:
        if f"+++ b/{rel}" in tracked_diff:
            continue
        target = path / rel
        if not target.is_file():
            continue
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = content.splitlines()
        body = "\n".join(f"+{line}" for line in lines[:400])
        parts.append(f"diff --git a/{rel} b/{rel}\nnew file\n--- /dev/null\n+++ b/{rel}\n{body}\n")
    text = "\n".join(parts)
    if len(text) > limit:
        text = text[:limit] + f"\n…（diff 共 {len(text)} 字符，已截断）\n"
    return text
