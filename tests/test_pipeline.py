from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from taskagent.executor import CodeBuddyExecutor, _no_execution_detected
from taskagent.gitmap import GitProjectRegistry, extract_git_urls, normalize_git_url
from taskagent.models import ItemKind, ItemStatus, RunResult, SourceType, WorkItem
from taskagent.sources import RawItem, parse_raw


# ── git mapping ────────────────────────────────────────────────────────────────
def test_normalize_git_url_forms():
    expected = "github.com/acme/app"
    for url in [
        "https://github.com/acme/app.git",
        "git@github.com:acme/app.git",
        "ssh://git@github.com/acme/app",
        "http://github.com/acme/app/",
    ]:
        assert normalize_git_url(url) == expected


def test_extract_git_urls_from_email_text():
    text = "见 https://github.com/acme/app.git 或 git@github.com:acme/other.git 或 github.com/acme/third"
    urls = extract_git_urls(text)
    assert any("app.git" in u for u in urls)
    assert any("other.git" in u for u in urls)
    assert any("github.com/acme/third" in u for u in urls)


def test_registry_resolves_https_and_scp(fake_repos):
    registry = GitProjectRegistry([fake_repos])
    repos = registry.scan()
    assert len(repos) == 2
    assert registry.resolve(git_url="https://github.com/acme/app-one.git").name == "app-one"
    assert registry.resolve(git_url="git@github.com:acme/app-two.git").name == "app-two"


# ── source parsing ─────────────────────────────────────────────────────────────
def test_parse_email_classifies_bug_and_extracts_git():
    raw = RawItem.from_email(
        subject="【BUG】登录接口 500, github.com/acme/app-one",
        body="偶发 500 错误，请修复。\n\n--\n签名",
        message_id="msg-1",
    )
    item = parse_raw(raw)
    assert item.kind == ItemKind.BUG
    assert item.git_url == "github.com/acme/app-one"
    assert item.source == SourceType.EMAIL
    assert "签名" not in item.description


def test_parse_feature_email():
    raw = RawItem.from_email(subject="【需求】新增导出功能", body="希望支持导出 CSV")
    assert parse_raw(raw).kind == ItemKind.FEATURE


# ── scheduler pipeline ────────────────────────────────────────────────────────
async def test_import_maps_and_dry_run(env):
    scheduler, store, _, events, data = env
    raw = RawItem.from_email(
        subject="【BUG】登录 500 https://github.com/acme/app-one.git",
        body="修复登录报错",
        message_id="msg-100",
    )
    item, created = scheduler.import_raw(raw)
    assert created
    assert item.status == ItemStatus.READY
    assert item.repo.name == "app-one"

    scheduler.run(item.id, exec_mode="dry-run")
    for _ in range(100):
        await asyncio.sleep(0.02)
        current = store.get(item.id)
        if current.is_terminal():
            break

    done = store.get(item.id)
    assert done.status == ItemStatus.SUCCEEDED
    assert done.result.dry_run
    assert (data / "reports" / f"{item.id}.md").is_file()
    assert any(e.type == "item_succeeded" for e in events.replay())


async def test_import_dedupe(env):
    scheduler, store, _, _, _ = env
    raw = RawItem.from_email(subject="重复需求", body="x", message_id="msg-dup")
    first, c1 = scheduler.import_raw(raw)
    second, c2 = scheduler.import_raw(raw)
    assert c1 and not c2
    assert first.id == second.id
    assert len(store.list()) == 1


async def test_unmapped_item_stays_discovered(env):
    scheduler, _, _, _, _ = env
    raw = RawItem.from_email(subject="【需求】无仓库的咨询", body="没有 repo")
    item, _ = scheduler.import_raw(raw)
    assert item.status == ItemStatus.DISCOVERED
    assert item.repo is None


async def test_apply_requires_approval(env):
    scheduler, _, _, _, _ = env
    raw = RawItem.from_email(subject="【BUG】x https://github.com/acme/app-one.git", body="y",
                             message_id="m-apply")
    item, _ = scheduler.import_raw(raw)
    with pytest.raises(ValueError, match="approval"):
        scheduler.run(item.id, exec_mode="apply", approved=False)


async def test_manual_map(env):
    scheduler, store, _, _, _ = env
    raw = RawItem.from_email(subject="【BUG】无 git 地址", body="修一下", message_id="m-map")
    item, _ = scheduler.import_raw(raw)
    assert item.status == ItemStatus.DISCOVERED
    scheduler.map_manually(item.id, "app-two")
    assert store.get(item.id).repo.name == "app-two"
    assert store.get(item.id).status == ItemStatus.READY


# ── one-click manual execution ────────────────────────────────────────────────
def test_create_manual_with_repo_and_explicit_kind(env):
    scheduler, store, _, _, _ = env
    item = scheduler.create_manual(
        title="调整 ai_task 调度台",
        description="新增手动新建入口，支持选项目后直接执行",
        kind="feature",
        repo_name="app-one",
    )
    assert item.status == ItemStatus.READY
    assert item.repo.name == "app-one"
    assert item.kind == ItemKind.FEATURE
    assert item.title == "调整 ai_task 调度台"
    assert item.dedupe_key.startswith("manual:")
    assert store.get(item.id).id == item.id


def test_manual_items_are_not_deduped(env):
    scheduler, _, _, _, _ = env
    first = scheduler.create_manual(title="同样标题", repo_name="app-one")
    second = scheduler.create_manual(title="同样标题", repo_name="app-one")
    assert first.id != second.id
    assert first.dedupe_key != second.dedupe_key


def test_create_manual_rejects_unknown_repo_and_mode(env):
    scheduler, _, _, _, _ = env
    with pytest.raises(ValueError, match="unknown repo"):
        scheduler.create_manual(title="x", repo_name="not-a-repo")
    with pytest.raises(ValueError, match="exec_mode"):
        scheduler.create_manual(title="x", repo_name="app-one", exec_mode="oops")


async def test_create_manual_then_run(env):
    scheduler, store, _, events, data = env
    item = scheduler.create_manual(
        title="修 bug", kind="bug", repo_name="app-two", exec_mode="dry-run"
    )
    scheduler.run(item.id, exec_mode="dry-run")

    for _ in range(100):
        await asyncio.sleep(0.02)
        if store.get(item.id).is_terminal():
            break

    done = store.get(item.id)
    assert done.status == ItemStatus.SUCCEEDED
    assert done.result.dry_run
    assert (data / "reports" / f"{item.id}.md").is_file()
    assert any(e.type == "item_succeeded" for e in events.replay())


async def test_dry_run_success_can_be_re_run(env):
    """A dry-run success is a review checkpoint, not a final state."""
    scheduler, store, _, _, data = env
    item = scheduler.create_manual(title="两段式执行", repo_name="app-one")

    async def wait_done():
        for _ in range(100):
            await asyncio.sleep(0.02)
            current = store.get(item.id)
            if current.is_terminal():
                return current
        return store.get(item.id)

    scheduler.run(item.id, exec_mode="dry-run")
    first = await wait_done()
    assert first.status == ItemStatus.SUCCEEDED
    assert first.report_path

    # The UI keeps dry-run/apply buttons visible here; the backend must allow it.
    scheduler.run(item.id, exec_mode="dry-run")
    second = await wait_done()
    assert second.status == ItemStatus.SUCCEEDED
    assert second.attempt == 1
    assert second.report_path
    assert (data / "reports" / f"{item.id}.md").is_file()


async def test_failed_and_cancelled_can_be_re_tried(env):
    """Failed/cancelled items can be re-armed; skipped stays terminal."""
    scheduler, store, _, _, _ = env

    async def wait_done(iid):
        for _ in range(100):
            await asyncio.sleep(0.02)
            current = store.get(iid)
            if current.is_terminal():
                return current
        return store.get(iid)

    for terminal_status in (ItemStatus.FAILED, ItemStatus.CANCELLED):
        item = scheduler.create_manual(
            title=f"重试 {terminal_status.value}", repo_name="app-one", exec_mode="dry-run"
        )
        item.status = terminal_status
        item.result = RunResult(ok=False, dry_run=False, command="x", error="old")
        item.error = "old error"
        item.report_path = "data/reports/old.md"
        item.finished_at = "2026-01-01T00:00:00+00:00"
        store.save(item)

        # Backend must accept a re-run from a failed/cancelled terminal state.
        scheduler.run(item.id, exec_mode="dry-run")
        armed = store.get(item.id)
        assert armed.status == ItemStatus.QUEUED
        assert armed.result is None
        assert armed.error is None
        assert armed.report_path == ""
        assert "重新执行" in armed.stage

        done = await wait_done(item.id)
        assert done.status == ItemStatus.SUCCEEDED
        assert done.result.dry_run

    # skipped (duplicate/unmapped) remains a true terminal state.
    skipped = scheduler.create_manual(title="跳过项", repo_name="app-one")
    skipped.status = ItemStatus.SKIPPED
    store.save(skipped)
    with pytest.raises(ValueError, match="already skipped"):
        scheduler.run(skipped.id, exec_mode="dry-run")


# ── per-task model selection ──────────────────────────────────────────────────
def test_build_command_per_task_model_overrides_global():
    """An item's model wins over the executor default; None falls back."""
    ex = CodeBuddyExecutor(command="codebuddy", model="balanced-model")
    plain = WorkItem.create(title="t", description="d", kind="task")
    picked = WorkItem.create(title="t", description="d", kind="task", model="deepseek-v4-pro")

    def model_of(item):
        argv = ex.build_command(item)
        return argv[argv.index("--model") + 1] if "--model" in argv else None

    assert model_of(plain) == "balanced-model"       # fallback to global
    assert model_of(picked) == "deepseek-v4-pro"      # per-task overrides


def test_build_command_no_model_when_unset():
    ex = CodeBuddyExecutor(command="codebuddy")
    item = WorkItem.create(title="t", description="d", kind="task")
    assert "--model" not in ex.build_command(item)


def test_create_manual_persists_model(env):
    scheduler, store, _, _, _ = env
    item = scheduler.create_manual(
        title="指定模型", repo_name="app-one", model="glm-5.3"
    )
    assert store.get(item.id).model == "glm-5.3"
    assert item.summary()["model"] == "glm-5.3"


async def test_run_model_override_and_clear(env):
    """Explicit model on re-run overrides; supplying None clears to default."""
    scheduler, store, _, _, _ = env
    item = scheduler.create_manual(title="切模型", repo_name="app-one", exec_mode="dry-run")
    assert item.model is None

    scheduler.run(item.id, exec_mode="dry-run", model="deep-model")
    assert store.get(item.id).model == "deep-model"

    # Omit the model -> keep the stored choice.
    scheduler.run(item.id, exec_mode="dry-run", _model_supplied=False)
    assert store.get(item.id).model == "deep-model"

    # Explicitly supply None -> clear back to global default.
    scheduler.run(item.id, exec_mode="dry-run", model=None, _model_supplied=True)
    assert store.get(item.id).model is None


def test_model_roundtrip_backward_compatible():
    data = {
        "id": "x", "title": "t", "description": "d", "kind": "task",
        "source": "manual", "status": "ready", "exec_mode": "dry-run",
    }
    assert WorkItem.from_dict(data).model is None  # old data without model
    data["model"] = "hy4flash"
    assert WorkItem.from_dict(data).model == "hy4flash"


def test_model_choices_from_config():
    from taskagent.config import Settings

    s = Settings()
    choices = {c["id"]: c["label"] for c in s.model_choices}
    assert len(choices) >= 10
    assert "deepseek-v4-pro" in choices
    assert choices["fast-model"]  # has a friendly label

    # TASKAGENT_MODELS restricts the offered list (env read happens in from_env;
    # here we verify the property honors available_models directly).
    s2 = Settings(available_models=("deepseek-v4-pro",))
    assert [c["id"] for c in s2.model_choices] == ["deepseek-v4-pro"]


def test_build_command_keeps_prompt_positional(env):
    scheduler, _, _, _, _ = env
    item = scheduler.create_manual(title="x", repo_name="app-one")
    argv = CodeBuddyExecutor().build_command(item)
    assert "--add-dir" not in argv
    assert argv[-1].startswith("你是一名资深工程师")


def test_no_execution_detection():
    empty = json.dumps({"result": "", "is_error": False, "usage": {"input_tokens": 0, "output_tokens": 0}})
    real = json.dumps({"result": "done", "is_error": False, "usage": {"input_tokens": 10, "output_tokens": 5}})
    assert _no_execution_detected("")
    assert _no_execution_detected("/x/y not found\n" + empty)
    assert not _no_execution_detected(real)


def test_streamed_event_array_not_mistaken_for_no_execution():
    """codebuddy's streamed format has token counts nested with camelCase keys.

    Regression: an apply run really executed (30k tokens, 18 tool calls) but
    produced a pretty-printed event array whose last JSON fragment had no
    top-level result. The old brace-matching logic reported "0 token空结果".
    """
    streamed = json.dumps([
        {"type": "message", "role": "user", "content": []},
        {"type": "function_call", "name": "Read", "arguments": "{}"},
        {
            "type": "reasoning",
            "providerData": {"usage": {"inputTokens": 30010, "outputTokens": 47}},
        },
    ], ensure_ascii=False)
    assert not _no_execution_detected(streamed)


def test_tool_events_alone_prove_execution():
    # Even without a token field, real tool-call events mean the prompt ran.
    streamed = '[{"type":"function_call","name":"Glob"},{"type":"reasoning"}]'
    assert not _no_execution_detected(streamed)


def test_snapshot_repo_for_non_git_directory(tmp_path):
    """A mapped folder with no own git repo gets a populated git sandbox."""
    from taskagent.executor import CodeBuddyExecutor
    from taskagent.models import LocalRepo

    src = tmp_path / "plain-project"
    src.mkdir()
    (src / "app.py").write_text("print('hi')\n")
    (src / ".venv").mkdir()
    (src / ".venv" / "ignore-me").write_text("noise")
    target = tmp_path / "sandbox" / "wt"

    assert CodeBuddyExecutor._create_worktree(
        LocalRepo(path=str(src), origin="", name="plain"), target
    )
    assert (target / "app.py").is_file()
    assert not (target / ".venv").exists()
    assert (target / ".git").is_dir()


def test_create_worktree_overlays_uncommitted_working_tree(tmp_path):
    """New worktree sees the repo's uncommitted edits, not just stale HEAD."""
    import subprocess

    from taskagent.models import LocalRepo

    repo = tmp_path / "repo"
    _git_init_with_base(repo, {"app.py": "v1\n", "gone.py": "bye\n", "keep.py": "keep\n"})
    # Simulate a prior apply synced in but not committed: modify, add, delete.
    (repo / "app.py").write_text("v2 with new feature\n")
    (repo / "gone.py").unlink()
    (repo / "fresh.py").write_text("brand new\n")

    target = tmp_path / "wts" / "task-1"
    assert CodeBuddyExecutor._create_worktree(
        LocalRepo(path=str(repo), origin="", name="repo"), target
    )

    # Uncommitted state is visible in the worktree.
    assert (target / "app.py").read_text() == "v2 with new feature\n"
    assert (target / "fresh.py").read_text() == "brand new\n"
    assert not (target / "gone.py").exists()
    # And it is committed as the new baseline, so git status is clean.
    status = subprocess.run(
        ["git", "-C", str(target), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    )
    assert status.stdout.strip() == "", status.stdout


def test_create_worktree_clean_repo_baseline_unchanged(tmp_path):
    """A clean repo still yields a clean worktree with no extra baseline commit."""
    import subprocess

    from taskagent.models import LocalRepo

    repo = tmp_path / "repo"
    _git_init_with_base(repo, {"app.py": "v1\n"})
    head_before = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    target = tmp_path / "wts" / "task-2"
    assert CodeBuddyExecutor._create_worktree(
        LocalRepo(path=str(repo), origin="", name="repo"), target
    )
    assert (target / "app.py").read_text() == "v1\n"
    status = subprocess.run(
        ["git", "-C", str(target), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    )
    assert status.stdout.strip() == ""
    # No overlay commit happened; worktree points at the same base commit.
    head_after = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert head_after == head_before


# ── auto-sync apply changes back into the main repo ───────────────────────────
def test_sync_to_repo_copies_new_and_modified(tmp_path):
    """New/modified files land in the real repo; existing files are backed up."""
    worktree = tmp_path / "worktree"
    repo = tmp_path / "repo"
    worktree.mkdir()
    repo.mkdir()
    (worktree / "app.py").write_text("new content\n")
    (repo / "app.py").write_text("old content\n")

    ex = CodeBuddyExecutor(worktrees_dir=tmp_path / "wt-root", backups_dir=tmp_path / "backups")
    out = ex.sync_to_repo(
        worktree, str(repo), item_id="item-1",
        changed=["app.py", "new.py"],
    )

    assert out["ok"] is True
    assert (repo / "app.py").read_text() == "new content\n"
    # new.py wasn't created in the worktree in this test, so only app.py copies.
    assert "app.py" in out["synced_files"]
    assert "new.py" not in out["synced_files"]
    assert out["sync_backup_dir"]  # an existing file was overwritten -> backup
    backup_file = Path(out["sync_backup_dir"]) / "app.py"
    assert backup_file.is_file()
    assert backup_file.read_text() == "old content\n"


def test_sync_to_repo_new_file_and_nested_dir(tmp_path):
    """A brand-new nested file is created, no backup dir needed."""
    worktree = tmp_path / "worktree"
    repo = tmp_path / "repo"
    (worktree / "pkg").mkdir(parents=True)
    repo.mkdir()
    (worktree / "pkg" / "mod.py").write_text("x = 1\n")

    ex = CodeBuddyExecutor(worktrees_dir=tmp_path / "wt-root", backups_dir=tmp_path / "backups")
    out = ex.sync_to_repo(
        worktree, str(repo), item_id="item-2", changed=["pkg/mod.py"]
    )

    assert (repo / "pkg" / "mod.py").read_text() == "x = 1\n"
    assert "pkg/mod.py" in out["synced_files"]
    assert out["sync_backup_dir"] is None  # nothing overwritten


def test_sync_to_repo_mirrors_deletion_with_backup(tmp_path):
    """A file deleted in the worktree is removed from the repo after backup."""
    worktree = tmp_path / "worktree"
    repo = tmp_path / "repo"
    worktree.mkdir()
    repo.mkdir()
    (repo / "gone.py").write_text("bye\n")

    ex = CodeBuddyExecutor(worktrees_dir=tmp_path / "wt-root", backups_dir=tmp_path / "backups")
    out = ex.sync_to_repo(
        worktree, str(repo), item_id="item-3", changed=["gone.py"]
    )

    assert not (repo / "gone.py").exists()
    assert "gone.py" in out["sync_deleted"]
    assert (Path(out["sync_backup_dir"]) / "gone.py").read_text() == "bye\n"


def test_sync_to_repo_rejects_unsafe_paths(tmp_path):
    """Absolute paths and ../ traversal never escape the repo root."""
    worktree = tmp_path / "worktree"
    repo = tmp_path / "repo"
    worktree.mkdir()
    repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("should not touch\n")

    ex = CodeBuddyExecutor(worktrees_dir=tmp_path / "wt-root", backups_dir=tmp_path / "backups")
    out = ex.sync_to_repo(
        worktree, str(repo), item_id="item-4",
        changed=["../outside.py", str(outside), "ok.py/../../escape.py"],
    )

    assert out["synced_files"] == []
    assert len(out["sync_skipped"]) == 3
    assert outside.read_text() == "should not touch\n"
    assert not (tmp_path / "escape.py").exists()


def test_sync_to_repo_respects_ignore_rules(tmp_path):
    """Secrets/runtime state are skipped even if codebuddy touched them."""
    worktree = tmp_path / "worktree"
    repo = tmp_path / "repo"
    worktree.mkdir()
    (worktree / ".env").write_text("SECRET=1")
    (worktree / "app.py").write_text("ok\n")
    repo.mkdir()

    ex = CodeBuddyExecutor(worktrees_dir=tmp_path / "wt-root", backups_dir=tmp_path / "backups")
    out = ex.sync_to_repo(
        worktree, str(repo), item_id="item-5", changed=[".env", "app.py"]
    )

    assert not (repo / ".env").exists()
    assert (repo / "app.py").read_text() == "ok\n"
    assert any(".env" in s for s in out["sync_skipped"])


def test_sync_to_repo_missing_repo_reports_error(tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    ex = CodeBuddyExecutor(worktrees_dir=tmp_path / "wt-root", backups_dir=tmp_path / "backups")
    out = ex.sync_to_repo(
        worktree, str(tmp_path / "no-such-repo"), item_id="item-6", changed=["x.py"]
    )
    assert out["ok"] is False
    assert out["sync_error"]


def _git_init_with_base(path: Path, files: dict[str, str]) -> None:
    """Create a standalone git repo with one baseline commit."""
    import subprocess

    path.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    subprocess.run(["git", "init", "-q"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", "base"],
        cwd=path, capture_output=True, check=True,
    )


def test_sync_conflict_does_not_clobber_repo_change(tmp_path):
    """When repo diverged after base, codebuddy's version must not overwrite it.

    Reproduces the real incident: a stale worktree's index.html would wipe
    changes made in the main repo after the worktree's base commit.
    """
    base = "line1\nline2\nline3\n"
    # Worktree's HEAD == base; codebuddy rewrote the whole file from base.
    wt = tmp_path / "wt"
    _git_init_with_base(wt, {"index.html": base})
    (wt / "index.html").write_text("codebuddy rewrite\n")
    # Main repo also has base committed, then a NEW edit landed after base.
    repo = tmp_path / "repo"
    _git_init_with_base(repo, {"index.html": base})
    (repo / "index.html").write_text("line1\nline2\nline3\nNEW MODEL UI\n")

    ex = CodeBuddyExecutor(worktrees_dir=tmp_path / "wt-root", backups_dir=tmp_path / "backups")
    out = ex.sync_to_repo(wt, str(repo), item_id="conflict-1", changed=["index.html"])

    # Repo change preserved — never silently clobbered.
    assert "NEW MODEL UI" in (repo / "index.html").read_text()
    assert out["synced_files"] == []
    assert any("index.html" in c for c in out["sync_conflicts"])
    backup = Path(out["sync_backup_dir"])
    assert (backup / "index.html.main").read_text() == "line1\nline2\nline3\nNEW MODEL UI\n"
    assert (backup / "index.html.codebuddy").read_text() == "codebuddy rewrite\n"


def test_sync_clean_three_way_merge_applies(tmp_path):
    """Non-overlapping edits in repo and codebuddy merge cleanly into the repo."""
    base = "a\nb\nc\n"
    wt = tmp_path / "wt"
    _git_init_with_base(wt, {"f.py": base})
    (wt / "f.py").write_text("a\nb\nc\nfrom_codebuddy\n")
    repo = tmp_path / "repo"
    _git_init_with_base(repo, {"f.py": base})
    (repo / "f.py").write_text("from_repo\na\nb\nc\n")

    ex = CodeBuddyExecutor(worktrees_dir=tmp_path / "wt-root", backups_dir=tmp_path / "backups")
    out = ex.sync_to_repo(wt, str(repo), item_id="merge-1", changed=["f.py"])

    content = (repo / "f.py").read_text()
    assert "from_repo" in content and "from_codebuddy" in content
    assert any("三方合并" in s for s in out["synced_files"])
    assert out["sync_conflicts"] == []


def test_sync_unchanged_repo_overwrites_cleanly(tmp_path):
    """When repo still matches base, codebuddy's version is applied (normal path)."""
    base = "old\n"
    wt = tmp_path / "wt"
    _git_init_with_base(wt, {"app.py": base})
    (wt / "app.py").write_text("new\n")
    repo = tmp_path / "repo"
    _git_init_with_base(repo, {"app.py": base})

    ex = CodeBuddyExecutor(worktrees_dir=tmp_path / "wt-root", backups_dir=tmp_path / "backups")
    out = ex.sync_to_repo(wt, str(repo), item_id="clean-1", changed=["app.py"])

    assert (repo / "app.py").read_text() == "new\n"
    assert "app.py" in out["synced_files"]
    assert out["sync_conflicts"] == []


def test_sync_deletion_guard_when_repo_diverged(tmp_path):
    """codebuddy deletes a file that repo edited after base -> do not delete."""
    base = "original\n"
    wt = tmp_path / "wt"
    _git_init_with_base(wt, {"gone.py": base})
    (wt / "gone.py").unlink()
    repo = tmp_path / "repo"
    _git_init_with_base(repo, {"gone.py": base})
    (repo / "gone.py").write_text("original\nrepo added lines\n")

    ex = CodeBuddyExecutor(worktrees_dir=tmp_path / "wt-root", backups_dir=tmp_path / "backups")
    out = ex.sync_to_repo(wt, str(repo), item_id="delconf-1", changed=["gone.py"])

    assert (repo / "gone.py").exists()
    assert "repo added lines" in (repo / "gone.py").read_text()
    assert any("gone.py" in c for c in out["sync_conflicts"])


def test_recover_interrupted_items_on_restart(env):
    """queued/running items left by a dead process are reaped on startup."""
    scheduler, store, _, events, _ = env
    running = scheduler.create_manual(title="卡住的", repo_name="app-one", exec_mode="apply")
    running.status = ItemStatus.RUNNING
    running.started_at = "2026-01-01T00:00:00+00:00"
    store.save(running)
    queued = scheduler.create_manual(title="排队的", repo_name="app-one")
    queued.status = ItemStatus.QUEUED
    store.save(queued)

    recovered = scheduler.recover_interrupted()
    assert recovered == 2
    for iid in (running.id, queued.id):
        item = store.get(iid)
        assert item.status == ItemStatus.FAILED
        assert "服务重启" in item.error
        assert item.finished_at
    assert any(e.type == "item_failed" and e.data.get("recovered") for e in events.replay())

    # A second pass is a no-op.
    assert scheduler.recover_interrupted() == 0
