"""交付：过滤提交、任务分支、push、PR/MR、按仓库策略。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from taskagent.commitfilter import CommitFileFilter
from taskagent.delivery import (
    DeliveryPolicy,
    ci_status,
    commit_changes,
    create_pr,
    finalize_ci,
    merge_pr,
    parse_remote,
    policy_for,
    push_branch,
    wait_for_ci,
)
from taskagent.executor import CodeBuddyExecutor
from taskagent.models import LocalRepo, ProjectMapping, WorkItem


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "T")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")
    return path


@pytest.fixture
def worktree(repo: Path, tmp_path: Path) -> Path:
    target = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "taskagent/demo-1", str(target))
    return target


def _item(repo: Path) -> WorkItem:
    item = WorkItem.create(title="修 bug", description="x", kind="bug", source="manual")
    item.repo = LocalRepo(path=str(repo), origin="", name=repo.name)
    return item


def test_commit_changes_commits_only_unfiltered_files(repo: Path, worktree: Path) -> None:
    (worktree / "app.py").write_text("def main():\n    return 2\n", encoding="utf-8")
    (worktree / ".env").write_text("SECRET=1\n", encoding="utf-8")
    backend = CodeBuddyExecutor()

    outcome = commit_changes(
        _item(repo),
        backend=backend,
        worktree=worktree,
        filter_obj=CommitFileFilter(),
        message="taskagent: fix",
    )

    assert outcome["ok"] is True, outcome
    assert outcome["branch"] == "taskagent/demo-1"
    assert outcome["commit"]
    assert "app.py" in outcome["kept"]
    assert any(".env" in name for name in outcome["skipped"])
    committed = _git(repo, "show", "--format=", "--name-only", "taskagent/demo-1").stdout
    assert "app.py" in committed
    assert ".env" not in committed
    # 主分支 HEAD 不动
    head = _git(repo, "log", "-1", "--format=%s", "HEAD").stdout.strip()
    assert head == "init"


def test_commit_changes_fails_when_everything_filtered(repo: Path, worktree: Path) -> None:
    (worktree / ".env").write_text("SECRET=1\n", encoding="utf-8")
    outcome = commit_changes(
        _item(repo),
        backend=CodeBuddyExecutor(),
        worktree=worktree,
        filter_obj=CommitFileFilter(),
    )
    assert outcome["ok"] is False
    assert "没有可提交的文件" in outcome["error"]


def test_push_branch_to_local_bare_remote(repo: Path, worktree: Path, tmp_path: Path) -> None:
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    _git(repo, "remote", "add", "origin", str(bare))

    (worktree / "app.py").write_text("def main():\n    return 2\n", encoding="utf-8")
    outcome = commit_changes(
        _item(repo), backend=CodeBuddyExecutor(), worktree=worktree, filter_obj=CommitFileFilter()
    )
    assert outcome["ok"] is True

    pushed = push_branch(repo, "taskagent/demo-1", "origin")
    assert pushed["ok"] is True, pushed
    refs = subprocess.run(
        ["git", "-C", str(bare), "show-ref", "--verify", "refs/heads/taskagent/demo-1"],
        capture_output=True, text=True, check=False,
    )
    assert refs.returncode == 0


def test_parse_remote_supports_https_ssh_and_gitlab_groups() -> None:
    assert parse_remote("git@github.com:acme/repo.git") == {
        "host": "github.com", "path": "acme/repo", "provider": "github",
    }
    assert parse_remote("https://github.com/acme/repo.git")["path"] == "acme/repo"
    assert parse_remote("ssh://git@gitlab.com/group/sub/repo.git") == {
        "host": "gitlab.com", "path": "group/sub/repo", "provider": "gitlab",
    }
    assert parse_remote("unknown")["provider"] == ""


def test_create_github_pr_uses_token_and_returns_url() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"html_url": "https://github.com/acme/repo/pull/7"})

    policy = DeliveryPolicy(
        mode="pr", base="main", provider="github", token="tkn",
    )
    outcome = create_pr(
        policy=policy,
        remote_url="git@github.com:acme/repo.git",
        branch="taskagent/demo-1",
        title="修 bug",
        body="body",
        transport=httpx.MockTransport(handler),
    )

    assert outcome["ok"] is True
    assert outcome["url"].endswith("/pull/7")
    assert seen["url"] == "https://api.github.com/repos/acme/repo/pulls"
    assert seen["auth"] == "Bearer tkn"
    assert seen["body"] == {
        "title": "修 bug", "head": "taskagent/demo-1", "base": "main", "body": "body",
    }


def test_create_gitlab_mr_encodes_project_and_uses_private_token() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["token"] = request.headers.get("private-token")
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"web_url": "https://gitlab.com/group/sub/repo/-/merge_requests/3"})

    policy = DeliveryPolicy(mode="pr", base="main", provider="gitlab", token="gl")
    outcome = create_pr(
        policy=policy,
        remote_url="git@gitlab.com:group/sub/repo.git",
        branch="taskagent/demo-1",
        title="修 bug",
        transport=httpx.MockTransport(handler),
    )

    assert outcome["ok"] is True
    assert outcome["url"].endswith("/merge_requests/3")
    assert "projects/group%2Fsub%2Frepo/merge_requests" in str(seen["url"])
    assert seen["token"] == "gl"
    assert seen["body"]["source_branch"] == "taskagent/demo-1"
    assert seen["body"]["target_branch"] == "main"


def test_create_pr_requires_token() -> None:
    policy = DeliveryPolicy(mode="pr", base="main", provider="github")
    outcome = create_pr(
        policy=policy,
        remote_url="git@github.com:acme/repo.git",
        branch="b",
        title="t",
        transport=httpx.MockTransport(lambda request: httpx.Response(201)),
    )
    assert outcome["ok"] is False
    assert "GITHUB_TOKEN" in outcome["error"]


def test_policy_for_uses_repo_override_and_global_default() -> None:
    item = _item(Path("/tmp/demo"))
    settings = SimpleNamespace(
        delivery_mode="off", delivery_remote="origin", delivery_base="",
        delivery_provider="", delivery_api_base="",
    )
    mapping = ProjectMapping(id="m", name="demo", path="/tmp/demo", delivery="commit")

    assert policy_for(item, settings=settings).mode == "off"
    assert policy_for(item, settings=settings, mapping=mapping).mode == "commit"
    assert policy_for(item, settings=settings, mapping=mapping).base == ""

    settings.delivery_mode = "push"
    empty = ProjectMapping(id="m2", name="demo2", path="/tmp/demo2")
    assert policy_for(item, settings=settings, mapping=empty).mode == "push"

# ── CI 状态 / 合并 ───────────────────────────────────────────────────────────
def _github_ci_transport(checks: list[dict], status_state: str = ""):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/check-runs"):
            return httpx.Response(200, json={"total_count": len(checks), "check_runs": checks})
        if path.endswith("/status"):
            return httpx.Response(200, json={"state": status_state})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def _pr_policy(**overrides) -> DeliveryPolicy:
    values = {"mode": "pr", "base": "main", "provider": "github", "token": "tkn"}
    values.update(overrides)
    return DeliveryPolicy(**values)


def test_ci_status_github_success_failure_pending_and_none() -> None:
    remote = "git@github.com:acme/repo.git"
    success = ci_status(_pr_policy(), remote, "taskagent/x", transport=_github_ci_transport([
        {"status": "completed", "conclusion": "success"},
        {"status": "completed", "conclusion": "neutral"},
    ]))
    assert success["state"] == "success"

    failure = ci_status(_pr_policy(), remote, "taskagent/x", transport=_github_ci_transport([
        {"status": "completed", "conclusion": "failure"},
    ]))
    assert failure["state"] == "failure"

    pending = ci_status(_pr_policy(), remote, "taskagent/x", transport=_github_ci_transport([
        {"status": "in_progress", "conclusion": None},
    ]))
    assert pending["state"] == "pending"

    none = ci_status(_pr_policy(), remote, "taskagent/x", transport=_github_ci_transport([]))
    assert none["state"] == "none"


def test_ci_status_falls_back_to_commit_status() -> None:
    remote = "git@github.com:acme/repo.git"
    transport = _github_ci_transport([], status_state="success")
    assert ci_status(_pr_policy(), remote, "taskagent/x", transport=transport)["state"] == "success"


def test_ci_status_gitlab_pipeline_states() -> None:
    def transport_for(status: str):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[{"id": 1, "status": status}])
        return httpx.MockTransport(handler)

    policy = _pr_policy(provider="gitlab", token="gl")
    remote = "git@gitlab.com:group/sub/repo.git"
    assert ci_status(policy, remote, "b", transport=transport_for("success"))["state"] == "success"
    assert ci_status(policy, remote, "b", transport=transport_for("failed"))["state"] == "failure"
    assert ci_status(policy, remote, "b", transport=transport_for("running"))["state"] == "pending"
    assert ci_status(policy, remote, "b", transport=transport_for("skipped"))["state"] == "none"


def test_wait_for_ci_times_out_with_fake_clock() -> None:
    state = {"now": 0.0, "polls": 0}

    def clock() -> float:
        return state["now"]

    def sleep(seconds: float) -> None:
        state["now"] += seconds

    def handler(request: httpx.Request) -> httpx.Response:
        state["polls"] += 1
        if request.url.path.endswith("/check-runs"):
            return httpx.Response(200, json={"check_runs": [{"status": "in_progress"}]})
        return httpx.Response(200, json={"state": "pending"})

    policy = _pr_policy(ci_timeout_s=10, ci_poll_interval_s=5)
    outcome = wait_for_ci(
        policy, "git@github.com:acme/repo.git", "branch",
        transport=httpx.MockTransport(handler), sleep=sleep, clock=clock,
    )
    assert outcome["state"] == "timeout"
    assert state["polls"] >= 2
    assert state["now"] >= 10


def test_finalize_ci_auto_merges_only_when_green_and_policy_auto() -> None:
    merged_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/check-runs"):
            return httpx.Response(200, json={"check_runs": [
                {"status": "completed", "conclusion": "success"},
            ]})
        if path.endswith("/pulls/7/merge"):
            merged_calls.append(request.method)
            return httpx.Response(200, json={"merged": True})
        return httpx.Response(200, json={"state": "success"})

    outcome = finalize_ci(
        _pr_policy(merge="auto"),
        "git@github.com:acme/repo.git",
        "branch",
        7,
        transport=httpx.MockTransport(handler),
    )
    assert outcome["ci"]["state"] == "success"
    assert outcome["merged"] is True
    assert merged_calls == ["PUT"]

    # manual 策略：CI 绿也不合并
    manual = finalize_ci(
        _pr_policy(merge="manual"),
        "git@github.com:acme/repo.git",
        "branch",
        7,
        transport=httpx.MockTransport(handler),
    )
    assert manual["merged"] is False
    assert merged_calls == ["PUT"]


def test_finalize_ci_does_not_merge_on_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/check-runs"):
            return httpx.Response(200, json={"check_runs": [
                {"status": "completed", "conclusion": "failure"},
            ]})
        return httpx.Response(200, json={"state": "failure"})

    outcome = finalize_ci(
        _pr_policy(merge="auto"),
        "git@github.com:acme/repo.git",
        "branch",
        7,
        transport=httpx.MockTransport(handler),
    )
    assert outcome["ci"]["state"] == "failure"
    assert outcome["merged"] is False


def test_merge_pr_github_and_gitlab() -> None:
    def github_handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert str(request.url).endswith("/repos/acme/repo/pulls/7/merge")
        return httpx.Response(200, json={"merged": True})

    assert merge_pr(
        _pr_policy(), "git@github.com:acme/repo.git", 7,
        transport=httpx.MockTransport(github_handler),
    )["ok"] is True

    def gitlab_handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/projects/group%2Fsub%2Frepo/merge_requests/3/merge")
        return httpx.Response(200, json={"state": "merged"})

    assert merge_pr(
        _pr_policy(provider="gitlab", token="gl"),
        "git@gitlab.com:group/sub/repo.git", 3,
        transport=httpx.MockTransport(gitlab_handler),
    )["ok"] is True


def test_merge_pr_requires_number_and_token() -> None:
    assert merge_pr(_pr_policy(), "git@github.com:acme/repo.git", 0)["ok"] is False
    no_token = DeliveryPolicy(mode="pr", base="main", provider="github")
    assert "GITHUB_TOKEN" in merge_pr(no_token, "git@github.com:acme/repo.git", 7)["error"]


def test_policy_merge_override() -> None:
    item = _item(Path("/tmp/demo"))
    settings = SimpleNamespace(
        delivery_mode="push", delivery_remote="origin", delivery_base="",
        delivery_provider="", delivery_api_base="", delivery_merge="auto",
        ci_timeout_s=60, ci_poll_interval_s=5,
    )
    mapping = ProjectMapping(id="m", name="demo", path="/tmp/demo", merge_policy="manual")
    assert policy_for(item, settings=settings).merge == "auto"
    assert policy_for(item, settings=settings, mapping=mapping).merge == "manual"


def test_scheduler_finish_delivery_records_ci_and_merge(tmp_path: Path, monkeypatch) -> None:
    import taskagent.delivery as delivery_mod
    from taskagent.events import EventBus
    from taskagent.executor import ExecutorRegistry
    from taskagent.gitmap import GitProjectRegistry
    from taskagent.scheduler import Scheduler, SchedulerSettings
    from taskagent.store import TaskStore

    scheduler = Scheduler(
        store=TaskStore(tmp_path / "tasks"),
        gitmap=GitProjectRegistry([]),
        executors=ExecutorRegistry(),
        events=EventBus(),
        settings=SchedulerSettings(
            reports_dir=tmp_path / "reports",
            delivery_mode="pr", delivery_merge="auto",
        ),
    )
    item = _item(tmp_path)
    item.delivery = {
        "mode": "pr", "commit": "abc", "branch": "taskagent/demo-1",
        "pushed": True, "pr_url": "https://github.com/acme/repo/pull/7", "pr_number": 7,
    }
    monkeypatch.setattr(
        scheduler, "_deliver_remote", lambda target, policy: target.delivery
    )
    monkeypatch.setattr(
        delivery_mod, "finalize_ci",
        lambda *args, **kwargs: {
            "ci": {"state": "success", "detail": "ok"},
            "merged": True, "merge_error": "",
        },
    )
    policy = scheduler._delivery_policy(item)
    suffix = scheduler._finish_delivery(item, policy, wait_ci=True)

    assert item.delivery["ci_state"] == "success"
    assert item.delivery["merged"] is True
    assert "自动合并" in suffix
