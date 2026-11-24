"""Regression tests for the P0 reliability fixes (cancellation + idempotency).

All tests are offline: they exercise the process registry and the pure helpers
without spawning a real CLI or touching network.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from taskagent.executor import CodeBuddyExecutor, ExecutorRegistry
from taskagent.gitmap import GitProjectRegistry
from taskagent.models import ItemKind, ItemStatus, LocalRepo, SourceType, WorkItem
from taskagent.scheduler import Scheduler, SchedulerSettings
from taskagent.store import TaskStore


class _FakeProc:
    """Minimal stand-in for asyncio.subprocess.Process used by the registry."""

    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True


def _executor(tmp_path: Path) -> CodeBuddyExecutor:
    return CodeBuddyExecutor(worktrees_dir=tmp_path / "worktrees")


# ── cancellation ───────────────────────────────────────────────────────────────
def test_cancel_process_signals_live_child(tmp_path, monkeypatch):
    ex = _executor(tmp_path)
    proc = _FakeProc()
    ex._bind_process("item-1", proc)  # type: ignore[arg-type]

    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr("os.name", "posix")
    monkeypatch.setattr(
        "os.killpg", lambda pid, sig: signalled.append((pid, sig)), raising=False
    )
    # No running loop in a sync test -> escalation scheduling is skipped.

    assert ex.cancel_process("item-1") is True
    assert signalled == [(4242, 15)]  # SIGTERM to the process group

    # A signalled (still-live) process is not signalled twice on a second call
    # from cancel_all — but it is still considered live until it exits.
    assert ex.cancel_process("missing-item") is False


def test_cancel_process_skips_already_exited_child(tmp_path):
    ex = _executor(tmp_path)
    proc = _FakeProc()
    proc.returncode = 0
    ex._bind_process("item-2", proc)  # type: ignore[arg-type]
    assert ex.cancel_process("item-2") is False
    ex._unbind_process("item-2")
    assert "item-2" not in ex._live_procs


@pytest.mark.asyncio
async def test_shutdown_terminates_all_live_processes(tmp_path, monkeypatch):
    """Shutdown signals SIGTERM then hard-kills survivors (no orphan window)."""
    from taskagent.events import EventBus

    monkeypatch.setattr("os.name", "posix")
    signalled: list[int] = []
    monkeypatch.setattr(
        "os.killpg", lambda pid, sig: signalled.append(sig), raising=False
    )

    ex = _executor(tmp_path)
    # Two children: one graceful (already exited), one stubborn (still live).
    graceful = _FakeProc()
    graceful.returncode = 0
    stubborn = _FakeProc(pid=4243)
    ex._bind_process("graceful", graceful)  # type: ignore[arg-type]
    ex._bind_process("stubborn", stubborn)  # type: ignore[arg-type]

    store = TaskStore(tmp_path / "data" / "tasks")
    scheduler = Scheduler(
        store=store,
        gitmap=GitProjectRegistry([]),
        executors=ExecutorRegistry({ex.backend_id: ex}),
        events=EventBus(),
        settings=SchedulerSettings(reports_dir=tmp_path / "data" / "reports"),
    )
    # Model a real in-flight task: cancellation runs _run_apply's finally,
    # which unbinds the process from _live_procs. The shutdown snapshot must
    # still hold the handle so the stubborn child is killed afterwards.
    async def _in_flight():
        try:
            await asyncio.sleep(3600)
        finally:
            ex._unbind_process("stubborn", stubborn)

    task = scheduler._spawn(_in_flight(), name="test-in-flight")
    assert not task.done()

    await scheduler.shutdown()
    # The stubborn child gets SIGTERM (15) then SIGKILL (9) even though its
    # registry entry was removed by the task's finally block. The exited one
    # is skipped entirely. This exercises the real code path.
    assert signalled == [15, 9]


@pytest.mark.asyncio
async def test_run_apply_honours_cancel_during_execution(tmp_path, monkeypatch):
    """A cancel flag flipped while the child runs stops the run promptly.

    The subprocess is replaced by a fake whose communicate() hangs until it is
    terminated, proving the polling loop actually checks should_cancel.
    """
    ex = _executor(tmp_path)
    monkeypatch.setattr(
        ex, "_create_worktree", lambda repo, target: True
    )
    monkeypatch.setattr(ex, "build_command", lambda item: ["fake-cli"])
    monkeypatch.setattr(ex, "_clean_env", dict)
    monkeypatch.setattr(
        ex, "_changed_files", staticmethod(lambda wt: [])
    )

    class _CommProc:
        pid = 7777
        returncode: int | None = None

        async def communicate(self):
            while self.returncode is None:
                await asyncio.sleep(0.01)
            return b"", b""

    fake = _CommProc()

    async def _fake_spawn(*args, **kwargs):
        return fake

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_spawn)

    def _cancel() -> None:
        fake.returncode = -15
        ex._unbind_process("live-item", fake)

    async def _flip():
        await asyncio.sleep(0.05)
        _cancel()

    item = WorkItem(
        id="live-item",
        title="t",
        description="d",
        source=SourceType.MANUAL,
        kind=ItemKind.TASK,
        status=ItemStatus.RUNNING,
        exec_mode="apply",
        repo=LocalRepo(path=str(tmp_path), origin="", name="app"),
        executor="codebuddy",
    )
    task = asyncio.create_task(_flip())
    result = await ex.run(
        item, progress=lambda *_: None, should_cancel=lambda: fake.returncode is not None
    )
    await task
    assert result.ok is False
    assert result.error == "cancelled"
    assert "live-item" not in ex._live_procs


# ── idempotency ────────────────────────────────────────────────────────────────
def test_seen_keys_cover_full_history(tmp_path):
    """Dedupe keys beyond the old 2000-item cap are still loaded at startup."""
    from taskagent.events import EventBus

    store = TaskStore(tmp_path / "tasks")
    total = 2005
    for i in range(total):
        item = WorkItem(
            id=f"work-{i:04d}",
            title=f"t{i}",
            description="d",
            source=SourceType.MANUAL,
            kind=ItemKind.TASK,
            status=ItemStatus.SUCCEEDED,
            dedupe_key=f"tapd:story:{i}",
        )
        store.save(item)
    scheduler = Scheduler(
        store=store,
        gitmap=GitProjectRegistry([]),
        events=EventBus(),
        settings=SchedulerSettings(reports_dir=tmp_path / "reports"),
    )
    # The oldest key (largest index) was previously dropped by limit=2000.
    assert len(scheduler._seen_keys) == total
    assert scheduler._seen_keys[f"tapd:story:{total - 1}"] == f"work-{total - 1:04d}"


def test_unbind_is_identity_checked(tmp_path):
    """A stale run's finally must not unbind a retried item's new process."""
    ex = _executor(tmp_path)
    old = _FakeProc(pid=1)
    new = _FakeProc(pid=2)
    ex._bind_process("retry", old)  # type: ignore[arg-type]
    ex._bind_process("retry", new)  # type: ignore[arg-type]
    ex._unbind_process("retry", old)
    assert ex._live_procs["retry"] is new


def test_broken_worktree_is_rebuilt(tmp_path):
    """A content-bearing dir with no git HEAD is not reused as a baseline."""
    import subprocess

    src = tmp_path / "repo"
    src.mkdir()
    (src / "app.py").write_text("v1\n")
    subprocess.run(["git", "init", "-q"], cwd=src, check=True)
    subprocess.run(["git", "add", "-A"], cwd=src, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"],
        cwd=src, check=True,
    )
    target = tmp_path / "wts" / "wt"
    target.mkdir(parents=True)
    (target / "stale.py").write_text("leftover from failed run\n")

    assert CodeBuddyExecutor._create_worktree(
        LocalRepo(path=str(src), origin="", name="repo"), target
    )
    # The stale file is gone; a fresh baseline from the real repo was created.
    assert not (target / "stale.py").exists()
    assert (target / "app.py").is_file()
    head = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--verify", "HEAD"],
        capture_output=True, check=False,
    )
    assert head.returncode == 0


# ── config preservation ────────────────────────────────────────────────────────
def test_cli_settings_keep_all_env_fields(monkeypatch):
    """CLI overrides host/port without discarding fs_root / gc_* fields."""
    from taskagent import cli

    captured: dict = {}

    def _fake_serve(settings):
        captured["settings"] = settings

    monkeypatch.setattr(cli, "serve", _fake_serve)
    monkeypatch.setattr(
        "sys.argv", ["taskagent", "serve", "--host", "0.0.0.0", "--port", "9000"]
    )
    monkeypatch.setenv("TASKAGENT_FS_ROOT", "/tmp/safe-root")
    monkeypatch.setenv("TASKAGENT_AUTO_SYNC", "0")

    cli.main()
    s = captured["settings"]
    assert s.host == "0.0.0.0"
    assert s.port == 9000
    assert str(s.fs_root) == "/tmp/safe-root"
    assert s.auto_sync is False
    assert s.gc_enabled is True
    assert s.backup_retention_days == 7
