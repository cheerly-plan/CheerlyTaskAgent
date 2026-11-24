"""执行器把 worktree diff 留档到 RunResult —— 合并删目录后仍能用于评论。"""

from __future__ import annotations

import asyncio
import os
import stat
import subprocess
from pathlib import Path

import pytest

from taskagent.executor import CodeBuddyExecutor
from taskagent.models import LocalRepo, RunResult, WorkItem


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "tester")
    _git(path, "config", "user.email", "tester@example.com")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "a.py").write_text("print('old')\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "base")
    return path


@pytest.fixture
def fake_cli(tmp_path: Path) -> Path:
    """A stand-in CLI: edits a.py and emits a result envelope with token usage."""
    cli = tmp_path / "fakecli"
    cli.write_text(
        "#!/bin/sh\n"
        "printf \"print('fixed')\\n\" > a.py\n"
        'printf \'{"type":"result","result":"done",'
        '"usage":{"input_tokens":5,"output_tokens":3}}\'\n',
        encoding="utf-8",
    )
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    return cli


@pytest.mark.skipif(os.name != "posix", reason="fake CLI is a POSIX shell script")
def test_apply_keeps_diff_excerpt_on_result(tmp_path: Path, repo: Path, fake_cli: Path) -> None:
    item = WorkItem.create(
        title="修 a.py", description="把 old 改成 fixed", kind="bug", source="manual"
    )
    item.repo = LocalRepo(path=str(repo), origin="", name="repo")
    item.exec_mode = "apply"

    executor = CodeBuddyExecutor(
        command=str(fake_cli),
        worktrees_dir=tmp_path / "worktrees",
        auto_sync=False,
    )
    result = asyncio.run(
        executor.run(item, progress=lambda _value, _stage: None, should_cancel=lambda: False)
    )

    assert result.ok is True, result.error
    assert result.changed_files == ["a.py"]
    # The diff is captured from the worktree before any merge/GC removes it.
    assert "+print('fixed')" in result.diff
    # And it survives the JSON round-trip used by TaskStore.
    assert RunResult(**result.to_dict()).diff == result.diff
    # CLI 自报的 token 用量进入 RunResult.usage，供账本/预算使用。
    assert result.usage["total_tokens"] == 8
    assert result.usage["input_tokens"] == 5