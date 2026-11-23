from __future__ import annotations

import subprocess
import time
from pathlib import Path

from taskagent.config import Settings
from taskagent.executor import CodeBuddyExecutor
from taskagent.models import ItemStatus, LocalRepo, RunResult


def _stamp(offset_s: int = 0) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() + offset_s))


def _backup(ex: CodeBuddyExecutor, item_id: str, stamp: str) -> Path:
    folder = ex.backups_dir / item_id / stamp
    folder.mkdir(parents=True)
    (folder / "file.py").write_text("old\n")
    return folder


def _git_init_with_base(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "app.py").write_text("v1\n")
    subprocess.run(["git", "init", "-q"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", "base"],
        cwd=path, capture_output=True, check=True,
    )


# ── backup GC ─────────────────────────────────────────────────────────────────
def test_gc_backups_by_age_and_count(tmp_path):
    """Old snapshots and all but the newest N per task are removed."""
    ex = CodeBuddyExecutor(worktrees_dir=tmp_path / "wts", backups_dir=tmp_path / "backups")
    old = _stamp(-30 * 86400)
    s1, s2, s3 = _stamp(-3), _stamp(-2), _stamp(-1)
    _backup(ex, "task-a", old)
    _backup(ex, "task-a", s1)
    _backup(ex, "task-a", s2)
    _backup(ex, "task-a", s3)
    _backup(ex, "task-b", old)

    out = ex.gc_backups(retention_days=7, max_per_task=2)

    assert out["removed"] == 3
    assert not (ex.backups_dir / "task-a" / old).exists()
    assert not (ex.backups_dir / "task-a" / s1).exists()
    assert (ex.backups_dir / "task-a" / s2).exists()
    assert (ex.backups_dir / "task-a" / s3).exists()
    # task-b had only an expired snapshot, so its empty folder is gone too.
    assert not (ex.backups_dir / "task-b").exists()


def test_gc_backups_zero_limits_are_noop(tmp_path):
    ex = CodeBuddyExecutor(worktrees_dir=tmp_path / "wts", backups_dir=tmp_path / "backups")
    old = _backup(ex, "task-z", _stamp(-30 * 86400))
    assert ex.gc_backups(retention_days=0, max_per_task=0)["removed"] == 0
    assert (old / "file.py").is_file()


def test_gc_backups_missing_root_is_safe(tmp_path):
    ex = CodeBuddyExecutor(worktrees_dir=tmp_path / "wts", backups_dir=tmp_path / "absent")
    assert ex.gc_backups(retention_days=7, max_per_task=5) == {"removed": 0}


# ── worktree cleanup ──────────────────────────────────────────────────────────
def test_cleanup_worktree_removes_snapshot(tmp_path):
    ex = CodeBuddyExecutor(worktrees_dir=tmp_path / "wts")
    wt = ex.worktrees_dir / "repo-item-1"
    wt.mkdir(parents=True)
    (wt / "a.py").write_text("x\n")

    assert ex.cleanup_worktree(wt) is True
    assert not wt.exists()
    assert ex.cleanup_worktree(wt) is True  # idempotent


def test_cleanup_worktree_deregisters_git_worktree(tmp_path):
    repo = tmp_path / "repo"
    _git_init_with_base(repo)
    wt = tmp_path / "wts" / "repo-item"
    assert CodeBuddyExecutor._create_worktree(
        LocalRepo(path=str(repo), origin="", name="repo"), wt
    )

    ex = CodeBuddyExecutor(worktrees_dir=tmp_path / "wts")
    assert ex.cleanup_worktree(wt, str(repo)) is True
    assert not wt.exists()

    listed = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert str(wt) not in listed
    branches = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", "taskagent/*"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert branches.strip() == ""


# ── scheduler integration ─────────────────────────────────────────────────────
def _worktree_for(env, item) -> Path:
    _scheduler, store, _, _, data = env
    wt = data / "worktrees" / f"app-one-{item.id}"
    wt.mkdir(parents=True, exist_ok=True)
    (wt / "a.py").write_text("x\n")
    item.result = RunResult(ok=True, dry_run=False, command="x", worktree=str(wt))
    store.save(item)
    return wt


def test_scheduler_drops_worktree_after_success(env):
    scheduler, _store, _, _, _ = env
    item = scheduler.create_manual(title="gc", repo_name="app-one", exec_mode="apply")
    wt = _worktree_for(env, item)
    backend = scheduler.executors.resolve(item.executor)

    scheduler._gc_after_success(item, backend)

    assert not wt.exists()
    assert item.result.worktree is None


def test_gc_can_be_disabled(env):
    scheduler, _store, _, _, _ = env
    scheduler.settings.gc_enabled = False
    item = scheduler.create_manual(title="gc-off", repo_name="app-one", exec_mode="apply")
    wt = _worktree_for(env, item)
    backend = scheduler.executors.resolve(item.executor)

    scheduler._gc_after_success(item, backend)

    assert wt.exists()
    assert item.result.worktree == str(wt)


def test_cancel_drops_worktree(env):
    scheduler, store, _, _, _ = env
    item = scheduler.create_manual(title="gc-cancel", repo_name="app-one", exec_mode="apply")
    wt = _worktree_for(env, item)

    assert scheduler.cancel(item.id) == 1

    assert not wt.exists()
    assert store.get(item.id).result.worktree is None
    assert store.get(item.id).status == ItemStatus.CANCELLED


def test_gc_artifacts_sweeps_history_but_keeps_blocked(env):
    scheduler, store, _, _, _ = env

    merged = scheduler.create_manual(title="merged", repo_name="app-one", exec_mode="apply")
    merged_wt = _worktree_for(env, merged)
    merged.merge_state = "merged"
    store.save(merged)

    cancelled = scheduler.create_manual(title="cancelled", repo_name="app-one", exec_mode="apply")
    cancelled_wt = _worktree_for(env, cancelled)
    cancelled.status = ItemStatus.CANCELLED
    store.save(cancelled)

    blocked = scheduler.create_manual(title="blocked", repo_name="app-one", exec_mode="apply")
    blocked_wt = _worktree_for(env, blocked)
    blocked.merge_state = "blocked"
    store.save(blocked)

    stats = scheduler.gc_artifacts()

    assert stats["worktrees"] == 2
    assert not merged_wt.exists()
    assert not cancelled_wt.exists()
    assert blocked_wt.exists()  # still retryable via merge_item()
    assert store.get(blocked.id).result.worktree == str(blocked_wt)


# ── config ────────────────────────────────────────────────────────────────────
def test_gc_settings_from_env(monkeypatch):
    defaults = Settings.from_env()
    assert defaults.gc_enabled is True
    assert defaults.backup_retention_days == 7
    assert defaults.backup_max_per_task == 5

    monkeypatch.setenv("TASKAGENT_GC_ENABLED", "0")
    monkeypatch.setenv("TASKAGENT_BACKUP_RETENTION_DAYS", "3")
    monkeypatch.setenv("TASKAGENT_BACKUP_MAX_PER_TASK", "2")
    custom = Settings.from_env()
    assert custom.gc_enabled is False
    assert custom.backup_retention_days == 3
    assert custom.backup_max_per_task == 2
