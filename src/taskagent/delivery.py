"""交付闭环：把验证通过的改动变成可评审的 git 交付物。

模式（按仓库覆盖，默认跟随全局，``off`` = 保持现状）：

- ``commit``：在隔离 worktree 的任务分支上做**过滤提交**（复用 commit 规则），
  不碰主仓库当前分支、不 push；
- ``push``：commit 后把任务分支推到远端；
- ``pr``：push 后按远端托管类型创建 GitHub PR / GitLab MR。

安全边界：
- 只提交 commit 规则允许的文件；被过滤的文件既不 stage 也不 commit；
- 不切主仓库分支、不动主仓库 HEAD；
- push/PR 失败不会回滚已经落地的本地合并，错误会记录在 ``item.delivery``；
- PR 需要 token：GitHub 读 ``GITHUB_TOKEN``，GitLab 读 ``GITLAB_TOKEN``
  （企业版可用 ``TASKAGENT_DELIVERY_API_BASE`` 覆盖 API 地址）。
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .commitfilter import CommitFileFilter
from .models import ProjectMapping, WorkItem

logger = logging.getLogger(__name__)

MODES = ("off", "commit", "push", "pr")
DEFAULT_REMOTE = "origin"
DEFAULT_TIMEOUT_S = 30


@dataclass
class DeliveryPolicy:
    mode: str = "off"
    remote: str = DEFAULT_REMOTE
    base: str = ""
    provider: str = ""      # "" = auto detect（github / gitlab）
    api_base: str = ""
    token: str = ""
    # CI 通过后：manual = 留 PR 给人工合并；auto = 自动合并 PR。
    merge: str = "manual"
    ci_timeout_s: int = 1800
    ci_poll_interval_s: int = 15

    @property
    def enabled(self) -> bool:
        return self.mode in {"commit", "push", "pr"}

    @property
    def auto_merge(self) -> bool:
        return self.merge == "auto"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "remote": self.remote,
            "base": self.base,
            "provider": self.provider,
            "merge": self.merge,
            # token 永远不出现在对外 JSON 里
        }


def _normalize_mode(value: str) -> str:
    mode = (value or "").strip().lower()
    return mode if mode in MODES else "off"


def _git(repo: str | Path, *args: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), "--no-pager", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def remote_url(repo_path: str | Path, remote: str = DEFAULT_REMOTE) -> str:
    proc = _git(repo_path, "remote", "get-url", remote)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def detect_base(repo_path: str | Path) -> str:
    """Default branch for a PR/MR target (origin/HEAD, else main/master)."""
    repo = Path(repo_path).expanduser()
    symbolic = _git(repo, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
    if symbolic.returncode == 0 and symbolic.stdout.strip():
        return symbolic.stdout.strip().rsplit("/", 1)[-1]
    for name in ("main", "master"):
        if _git(repo, "rev-parse", "--verify", "-q", name).returncode == 0:
            return name
    return ""


def policy_for(
    item: WorkItem,
    *,
    settings: Any,
    mapping: ProjectMapping | None = None,
) -> DeliveryPolicy:
    """Resolve the per-repo override against the global default."""
    override = (mapping.delivery if mapping is not None else "") or ""
    mode = _normalize_mode(override or getattr(settings, "delivery_mode", "off"))
    if mode == "off":
        return DeliveryPolicy(mode="off")
    repo = item.repo.path if item.repo else ""
    remote = getattr(settings, "delivery_remote", "") or DEFAULT_REMOTE
    base = getattr(settings, "delivery_base", "") or (detect_base(repo) if repo else "")
    provider = getattr(settings, "delivery_provider", "") or ""
    api_base = getattr(settings, "delivery_api_base", "") or ""
    merge = (mapping.merge_policy if mapping is not None else "") or getattr(
        settings, "delivery_merge", "manual"
    )
    merge = "auto" if str(merge).strip().lower() == "auto" else "manual"
    # token 由 create_pr 按实际托管方从环境变量读取，避免在策略里猜 provider。
    return DeliveryPolicy(
        mode=mode, remote=remote, base=base,
        provider=provider, api_base=api_base, merge=merge,
        ci_timeout_s=max(1, int(getattr(settings, "ci_timeout_s", 1800) or 1800)),
        ci_poll_interval_s=max(
            1, int(getattr(settings, "ci_poll_interval_s", 15) or 15)
        ),
    )


# ── local commit ─────────────────────────────────────────────────────────────
def commit_changes(
    item: WorkItem,
    *,
    backend: Any,
    worktree: str | Path,
    filter_obj: CommitFileFilter,
    message: str = "",
) -> dict[str, Any]:
    """Filtered commit of the worktree's changes onto its task branch."""
    path = Path(worktree).expanduser()
    if not path.is_dir():
        return {"ok": False, "error": f"worktree 不存在：{path}"}
    changed = backend.changed_files(path)
    plan = filter_obj.classify(changed)
    if not plan.kept:
        return {
            "ok": False,
            "error": "没有可提交的文件（全部被过滤规则排除）",
            "skipped": list(plan.skipped),
            "skipped_reasons": dict(plan.skipped_reasons),
        }
    outcome = backend.commit_files(
        path,
        kept=list(plan.kept),
        message=message.strip() or f"taskagent: {item.title}",
    )
    result = dict(outcome)
    result["skipped"] = list(plan.skipped)
    result["skipped_reasons"] = dict(plan.skipped_reasons)
    return result


# ── push / PR ────────────────────────────────────────────────────────────────
def push_branch(
    repo_path: str | Path,
    branch: str,
    remote: str = DEFAULT_REMOTE,
    *,
    timeout: float = 120,
) -> dict[str, Any]:
    """Push the task branch to its remote (uses the user's git credentials)."""
    if not branch:
        return {"ok": False, "error": "缺少分支名"}
    proc = _git(repo_path, "push", "--set-upstream", remote, branch, timeout=timeout)
    if proc.returncode != 0:
        detail = " ".join((proc.stderr or proc.stdout or "").split())[:500]
        return {"ok": False, "error": f"git push 失败：{detail}"}
    return {"ok": True, "branch": branch, "remote": remote}


_SCP_RE = re.compile(r"^(?:[\w.-]+@)?([\w.-]+):(.+?)(?:\.git)?$")
_HTTP_RE = re.compile(r"^https?://(?:[^@/]+@)?([^/]+)/(.+?)(?:\.git)?/?$")
_SSH_RE = re.compile(r"^ssh://(?:[^@/]+@)?([^/]+)/(.+?)(?:\.git)?/?$")


def parse_remote(remote_url: str) -> dict[str, str]:
    """Split a git remote into host / owner / repo / provider."""
    text = (remote_url or "").strip()
    match = _HTTP_RE.match(text) or _SSH_RE.match(text) or _SCP_RE.match(text)
    if not match:
        return {"host": "", "path": "", "provider": ""}
    host, path = match.group(1), match.group(2).strip("/")
    provider = "github" if "github" in host else ("gitlab" if "gitlab" in host else "")
    return {"host": host, "path": path, "provider": provider}


def _api_base(policy: DeliveryPolicy, host: str, provider: str) -> str:
    if policy.api_base:
        return policy.api_base.rstrip("/")
    if provider == "github":
        return "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"
    if provider == "gitlab":
        base = "https://gitlab.com" if host == "gitlab.com" else f"https://{host}"
        return f"{base}/api/v4"
    return ""


def _token_for(policy: DeliveryPolicy, provider: str) -> str:
    if policy.token:
        return policy.token
    env = "GITHUB_TOKEN" if provider == "github" else "GITLAB_TOKEN"
    return os.environ.get(env, "")


def create_pr(
    *,
    policy: DeliveryPolicy,
    remote_url: str,
    branch: str,
    title: str,
    body: str = "",
    transport: httpx.BaseTransport | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Create a GitHub PR / GitLab MR for ``branch`` (best effort)."""
    parsed = parse_remote(remote_url)
    provider = policy.provider or parsed["provider"]
    if provider not in {"github", "gitlab"}:
        return {"ok": False, "error": f"无法识别远端托管类型：{remote_url}"}
    if not parsed["path"] or not policy.base:
        return {"ok": False, "error": "缺少远端仓库路径或目标分支"}
    token = _token_for(policy, provider)
    if not token:
        env = "GITHUB_TOKEN" if provider == "github" else "GITLAB_TOKEN"
        return {"ok": False, "error": f"未配置 {env}，无法创建 PR/MR"}
    api = _api_base(policy, parsed["host"], provider)
    try:
        with httpx.Client(timeout=timeout_s, transport=transport) as client:
            if provider == "github":
                url = f"{api}/repos/{parsed['path']}/pulls"
                response = client.post(
                    url,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                    json={"title": title, "head": branch, "base": policy.base, "body": body},
                )
            else:
                project = urllib.parse.quote(parsed["path"], safe="")
                url = f"{api}/projects/{project}/merge_requests"
                response = client.post(
                    url,
                    headers={"PRIVATE-TOKEN": token},
                    json={
                        "source_branch": branch,
                        "target_branch": policy.base,
                        "title": title,
                        "description": body,
                    },
                )
    except httpx.HTTPError as error:
        return {"ok": False, "error": f"请求 {provider} 失败：{error}"}
    if response.status_code in {200, 201}:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        url = str(payload.get("html_url") or payload.get("web_url") or "")
        number = payload.get("number") or payload.get("iid") or 0
        return {
            "ok": True, "url": url, "provider": provider,
            "number": int(number or 0),
        }
    detail = " ".join(response.text.split())[:300]
    return {
        "ok": False,
        "error": f"{provider} 返回 HTTP {response.status_code}：{detail}",
        "provider": provider,
    }


# ── CI 状态 / 合并 PR ────────────────────────────────────────────────────────
def _request(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    **kwargs: Any,
) -> httpx.Response | None:
    try:
        return client.request(method, url, headers=headers, **kwargs)
    except httpx.HTTPError:
        return None


def _github_ci(client: httpx.Client, api: str, path: str, ref: str, token: str) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    checks = _request(
        client, "GET", f"{api}/repos/{path}/commits/{ref}/check-runs",
        headers=headers, params={"per_page": 100},
    )
    status = _request(
        client, "GET", f"{api}/repos/{path}/commits/{ref}/status", headers=headers
    )
    runs: list[dict[str, Any]] = []
    if checks is not None and checks.status_code < 400:
        try:
            runs = checks.json().get("check_runs") or []
        except ValueError:
            runs = []
    failed_conclusions = {
        "failure", "cancelled", "timed_out", "action_required", "stale",
    }
    conclusions = [
        str(run.get("conclusion") or "") for run in runs if run.get("status") == "completed"
    ]
    if any(value in failed_conclusions for value in conclusions):
        return {"state": "failure", "detail": "GitHub check 失败", "url": ""}
    if any(run.get("status") != "completed" for run in runs):
        return {"state": "pending", "detail": "GitHub check 运行中", "url": ""}
    if runs:
        return {"state": "success", "detail": f"{len(runs)} 个 check 通过", "url": ""}
    # 没有 check-runs 时回退 commit status（老式 CI）。
    state = ""
    if status is not None and status.status_code < 400:
        try:
            state = str(status.json().get("state") or "")
        except ValueError:
            state = ""
    if state in {"failure", "error"}:
        return {"state": "failure", "detail": f"commit status={state}", "url": ""}
    if state == "pending":
        return {"state": "pending", "detail": "commit status pending", "url": ""}
    if state == "success":
        return {"state": "success", "detail": "commit status success", "url": ""}
    return {"state": "none", "detail": "未发现 CI 检查", "url": ""}


def _gitlab_ci(client: httpx.Client, api: str, path: str, ref: str, token: str) -> dict[str, Any]:
    project = urllib.parse.quote(path, safe="")
    response = _request(
        client, "GET", f"{api}/projects/{project}/pipelines",
        headers={"PRIVATE-TOKEN": token},
        params={"ref": ref, "per_page": 1},
    )
    if response is None or response.status_code >= 400:
        return {"state": "none", "detail": "未发现 CI pipeline", "url": ""}
    try:
        pipelines = response.json()
    except ValueError:
        pipelines = []
    if not isinstance(pipelines, list) or not pipelines:
        return {"state": "none", "detail": "未发现 CI pipeline", "url": ""}
    pipeline = pipelines[0] if isinstance(pipelines[0], dict) else {}
    status = str(pipeline.get("status") or "").lower()
    if status == "success":
        return {"state": "success", "detail": "GitLab pipeline 通过", "url": ""}
    if status in {"failed", "canceled"}:
        return {"state": "failure", "detail": f"GitLab pipeline {status}", "url": ""}
    if status in {"running", "pending", "created", "preparing", "waiting_for_resource"}:
        return {"state": "pending", "detail": f"GitLab pipeline {status}", "url": ""}
    return {"state": "none", "detail": f"GitLab pipeline {status or 'unknown'}", "url": ""}


def ci_status(
    policy: DeliveryPolicy,
    remote_url: str,
    ref: str,
    *,
    transport: httpx.BaseTransport | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """One CI snapshot for ``ref`` (branch or commit)."""
    parsed = parse_remote(remote_url)
    provider = policy.provider or parsed["provider"]
    if provider not in {"github", "gitlab"}:
        return {"state": "none", "detail": f"无法识别托管类型：{remote_url}", "url": ""}
    token = _token_for(policy, provider)
    if not token:
        env = "GITHUB_TOKEN" if provider == "github" else "GITLAB_TOKEN"
        return {"state": "error", "detail": f"未配置 {env}，无法查询 CI", "url": ""}
    api = _api_base(policy, parsed["host"], provider)
    try:
        with httpx.Client(timeout=timeout_s, transport=transport) as client:
            if provider == "github":
                return _github_ci(client, api, parsed["path"], ref, token)
            return _gitlab_ci(client, api, parsed["path"], ref, token)
    except httpx.HTTPError as error:
        return {"state": "error", "detail": f"查询 CI 失败：{error}", "url": ""}


def wait_for_ci(
    policy: DeliveryPolicy,
    remote_url: str,
    ref: str,
    *,
    transport: httpx.BaseTransport | None = None,
    timeout_s: int | None = None,
    interval_s: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Poll until CI reaches a terminal state or the timeout elapses."""
    deadline = clock() + float(timeout_s or policy.ci_timeout_s)
    interval = float(interval_s or policy.ci_poll_interval_s)
    last: dict[str, Any] = {"state": "pending", "detail": "等待 CI", "url": ""}
    while True:
        last = ci_status(policy, remote_url, ref, transport=transport)
        if last["state"] in {"success", "failure", "none", "error"}:
            return last
        remaining = deadline - clock()
        if remaining <= 0:
            return {
                **last,
                "state": "timeout",
                "detail": f"等待 CI 超时（{int(timeout_s or policy.ci_timeout_s)}s）",
            }
        sleep(min(interval, max(0.0, remaining)))


def merge_pr(
    policy: DeliveryPolicy,
    remote_url: str,
    number: int,
    *,
    transport: httpx.BaseTransport | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Merge an existing PR/MR (only called when policy says auto + CI green)."""
    if not number:
        return {"ok": False, "error": "缺少 PR/MR 编号"}
    parsed = parse_remote(remote_url)
    provider = policy.provider or parsed["provider"]
    token = _token_for(policy, provider)
    if token == "":
        env = "GITHUB_TOKEN" if provider == "github" else "GITLAB_TOKEN"
        return {"ok": False, "error": f"未配置 {env}，无法合并"}
    api = _api_base(policy, parsed["host"], provider)
    try:
        with httpx.Client(timeout=timeout_s, transport=transport) as client:
            if provider == "github":
                response = client.put(
                    f"{api}/repos/{parsed['path']}/pulls/{number}/merge",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                    },
                    json={"merge_method": "squash"},
                )
            else:
                project = urllib.parse.quote(parsed["path"], safe="")
                response = client.put(
                    f"{api}/projects/{project}/merge_requests/{number}/merge",
                    headers={"PRIVATE-TOKEN": token},
                )
    except httpx.HTTPError as error:
        return {"ok": False, "error": f"合并请求失败：{error}"}
    if response.status_code in {200, 201, 204}:
        return {"ok": True, "provider": provider}
    detail = " ".join(response.text.split())[:300]
    return {"ok": False, "error": f"合并失败 HTTP {response.status_code}：{detail}"}


def finalize_ci(
    policy: DeliveryPolicy,
    remote_url: str,
    ref: str,
    number: int,
    *,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Wait for CI, then merge the PR iff policy=auto and CI is green."""
    ci = wait_for_ci(
        policy, remote_url, ref, transport=transport, sleep=sleep, clock=clock
    )
    outcome: dict[str, Any] = {"ci": ci, "merged": False, "merge_error": ""}
    if ci["state"] == "success" and policy.auto_merge and number:
        merged = merge_pr(policy, remote_url, number, transport=transport)
        outcome["merged"] = bool(merged.get("ok"))
        outcome["merge_error"] = str(merged.get("error") or "")
    return outcome
