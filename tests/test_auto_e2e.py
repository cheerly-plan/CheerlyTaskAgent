"""端到端：手动任务 → 自动执行 → 假 CLI 改代码 → 原子合并进主仓工作区。

不调用真实 LLM：用一个 shell 脚本冒充执行后端，只验证链路接线（触发式出队、
隔离 worktree、无冲突自动合并、不提交、不产生多余归档）。
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from fastapi.testclient import TestClient

from taskagent.api import create_app
from taskagent.config import Settings

REVIEW = {
    "summary": "把返回值改成 2",
    "changes": ["app.py 返回 2", "新增 util.py"],
    "impact": {"level": "low", "areas": ["main"], "notes": "只影响 main 的返回值"},
    "can_merge": True,
    "merge_blockers": [],
    "confidence": 0.9,
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "e2e@example.com")
    _git(repo, "config", "user.name", "E2E")
    (repo / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _fake_cli(tmp_path: Path) -> Path:
    script = tmp_path / "fake-cli"
    payload = json.dumps({"type": "result", "result": "完成\n```json\n" + json.dumps(REVIEW, ensure_ascii=False) + "\n```"}, ensure_ascii=False)
    script.write_text(
        "#!/bin/sh\n"
        "set -e\n"
        "printf 'def main():\\n    return 2\\n' > app.py\n"
        "printf 'VALUE = 1\\n' > util.py\n"
        f"cat <<'JSON'\n{payload}\nJSON\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _write_verify(settings: Settings, command: tuple[str, ...] = ("true",)) -> None:
    """Four-category gate config; every check runs the same fake command."""
    path = settings.data_dir / "verify.json"
    checks = [
        {
            "id": category, "label": category, "category": category,
            "command": list(command), "timeout_s": 30,
        }
        for category in ("test", "lint", "typecheck", "build")
    ]
    path.write_text(
        json.dumps({"repos": {"demo": {"checks": checks, "baseline": "off"}}}),
        encoding="utf-8",
    )


def _setup(tmp_path: Path, command: tuple[str, ...] = ("true",)) -> tuple[Path, Settings]:
    repo = _make_repo(tmp_path)
    fake = _fake_cli(tmp_path)
    settings = Settings(data_dir=tmp_path / "data", project_roots=())
    ui = settings.load_ui_settings()
    ui["backends"] = {
        "codebuddy": {
            "label": "Fake", "enabled": True, "command": str(fake),
            "model": "", "timeout_s": 60, "auto_sync": True,
        }
    }
    ui["executor"] = "codebuddy"
    settings.save_ui_settings(ui)
    _write_verify(settings, command)
    return repo, settings


def test_manual_task_auto_runs_and_merges(tmp_path: Path):
    repo, settings = _setup(tmp_path)

    app = create_app(settings)
    with TestClient(app) as client:
        mapping = client.post("/api/mappings", json={"name": "demo", "path": str(repo)})
        assert mapping.status_code == 201, mapping.text

        created = client.post("/api/tasks/manual", json={
            "title": "把返回值改成 2", "description": "main 返回 2",
            "kind": "bug", "repo_name": "demo",
            "exec_mode": "apply", "run_now": False, "approved": False,
        })
        assert created.status_code == 201, created.text
        item_id = created.json()["id"]
        assert created.json()["status"] == "ready"

        # 任务中心手动勾选后触发：只跑选中的任务。
        started = client.post("/api/tasks/auto-run", json={"ids": [item_id]})
        assert started.status_code == 200, started.text
        assert started.json()["started"] == [item_id]

        item = None
        for _ in range(200):
            item = client.get(f"/api/items/{item_id}").json()
            if item["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.05)

        assert item is not None and item["status"] == "succeeded", item
        assert item["exec_mode"] == "apply"
        assert item["approved_by"] == "auto"
        # 无冲突 → 自动合并写回主仓工作区。
        assert item["merge_state"] == "merged", item
        assert "return 2" in (repo / "app.py").read_text(encoding="utf-8")
        assert (repo / "util.py").is_file()
        # 待办菜单已移除，接口也不存在。
        assert client.get("/api/todos").status_code == 404

        usage = client.get("/api/usage").json()
        assert usage["limits"] == {"task_tokens": 0, "daily_tokens": 0}
        assert "total_tokens" in usage

    # 合并只改工作区，不产生提交。
    count = subprocess.run(
        ["git", "-C", str(repo), "rev-list", "--count", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert count == "1"

def test_verification_failure_blocks_merge(tmp_path: Path):
    repo, settings = _setup(tmp_path, ("false",))
    app = create_app(settings)
    with TestClient(app) as client:
        mapping = client.post("/api/mappings", json={"name": "demo", "path": str(repo)})
        assert mapping.status_code == 201, mapping.text
        created = client.post("/api/tasks/manual", json={
            "title": "把返回值改成 2", "description": "main 返回 2",
            "kind": "bug", "repo_name": "demo",
            "exec_mode": "apply", "run_now": False, "approved": False,
        })
        assert created.status_code == 201, created.text
        item_id = created.json()["id"]

        started = client.post("/api/tasks/auto-run", json={"ids": [item_id]})
        assert started.status_code == 200, started.text

        item = None
        for _ in range(200):
            item = client.get(f"/api/items/{item_id}").json()
            if item["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.05)

        assert item is not None and item["status"] == "failed", item
        assert "验证未通过" in item["stage"]
        assert "验证未通过" in (item["error"] or "")
        # 门禁拦下：主仓工作区一个字节都没变，worktree 保留。
        assert item["merge_state"] == ""
        assert "return 1" in (repo / "app.py").read_text(encoding="utf-8")
        assert not (repo / "util.py").exists()
        assert item["result"]["verification"]["state"] == "failed"
        assert item["result"]["worktree"]
        # 自动链路会带着反馈再修一轮（TASKAGENT_MAX_ROUNDS 默认 2）。
        assert item["result"]["rounds"] == 2
        # 建单时就有规则版任务规格（目标/验收标准），不需要额外 LLM。
        assert item["spec"]["source"] == "rule"
        assert item["spec"]["acceptance"]

        report = client.get(f"/api/items/{item_id}/report").text
        assert "### 验证" in report
        assert "未通过" in report

        # 修好配置后只重跑验证（不重跑 agent）：通过则自动合并。
        _write_verify(settings, ("true",))
        verified = client.post(f"/api/items/{item_id}/verify")
        assert verified.status_code == 200, verified.text
        body = verified.json()
        assert body["status"] == "succeeded", body
        assert body["merge_state"] == "merged", body
        assert "return 2" in (repo / "app.py").read_text(encoding="utf-8")


def test_manual_apply_runs_verification_gate_and_merges(tmp_path: Path):
    repo, settings = _setup(tmp_path, ("true",))
    app = create_app(settings)
    with TestClient(app) as client:
        mapping = client.post("/api/mappings", json={"name": "demo", "path": str(repo)})
        assert mapping.status_code == 201, mapping.text
        created = client.post("/api/tasks/manual", json={
            "title": "把返回值改成 2", "description": "main 返回 2",
            "kind": "bug", "repo_name": "demo",
            "exec_mode": "apply", "run_now": False, "approved": False,
        })
        assert created.status_code == 201, created.text
        item_id = created.json()["id"]

        # 人工单条 apply（非自动执行）：approved=true 但仍需经过验证门禁。
        started = client.post(f"/api/items/{item_id}/run", json={
            "exec_mode": "apply", "approved": True,
        })
        assert started.status_code == 200, started.text
        assert started.json()["approved_by"] != "auto"

        item = None
        for _ in range(200):
            item = client.get(f"/api/items/{item_id}").json()
            if item["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.05)

        assert item is not None and item["status"] == "succeeded", item
        assert item["result"]["verification"]["ok"] is True
        assert item["merge_state"] == "merged", item
        assert "return 2" in (repo / "app.py").read_text(encoding="utf-8")
        assert (repo / "util.py").is_file()


def test_manual_apply_gate_failure_blocks_sync_and_can_reverify(tmp_path: Path):
    repo, settings = _setup(tmp_path, ("false",))
    app = create_app(settings)
    with TestClient(app) as client:
        client.post("/api/mappings", json={"name": "demo", "path": str(repo)})
        created = client.post("/api/tasks/manual", json={
            "title": "把返回值改成 2", "description": "main 返回 2",
            "kind": "bug", "repo_name": "demo",
            "exec_mode": "apply", "run_now": False, "approved": False,
        })
        item_id = created.json()["id"]
        client.post(f"/api/items/{item_id}/run", json={
            "exec_mode": "apply", "approved": True,
        })

        item = None
        for _ in range(200):
            item = client.get(f"/api/items/{item_id}").json()
            if item["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.05)

        assert item is not None and item["status"] == "failed", item
        assert "验证未通过" in item["stage"]
        assert "验证未通过" in (item["error"] or "")
        # 门禁拦下：主仓一个字节都没变，worktree 保留。
        assert item["merge_state"] == ""
        assert "return 1" in (repo / "app.py").read_text(encoding="utf-8")
        assert not (repo / "util.py").exists()
        assert item["result"]["verification"]["state"] == "failed"
        assert item["result"]["worktree"]

        # 修好配置后只重跑门禁：通过后人工 apply 同样会原子合并。
        _write_verify(settings, ("true",))
        verified = client.post(f"/api/items/{item_id}/verify")
        assert verified.status_code == 200, verified.text
        body = verified.json()
        assert body["status"] == "succeeded", body
        assert body["merge_state"] == "merged", body
        assert "return 2" in (repo / "app.py").read_text(encoding="utf-8")


def test_force_merge_skips_verification(tmp_path: Path):
    repo, settings = _setup(tmp_path, ("false",))
    app = create_app(settings)
    with TestClient(app) as client:
        mapping = client.post("/api/mappings", json={"name": "demo", "path": str(repo)})
        assert mapping.status_code == 201, mapping.text
        created = client.post("/api/tasks/manual", json={
            "title": "把返回值改成 2", "description": "main 返回 2",
            "kind": "bug", "repo_name": "demo",
            "exec_mode": "apply", "run_now": False, "approved": False,
        })
        item_id = created.json()["id"]
        client.post("/api/tasks/auto-run", json={"ids": [item_id]})

        item = None
        for _ in range(200):
            item = client.get(f"/api/items/{item_id}").json()
            if item["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.05)
        assert item is not None and item["status"] == "failed", item

        forced = client.post(f"/api/items/{item_id}/merge", json={"force": True})
        assert forced.status_code == 200, forced.text
        merged = forced.json()
        assert merged["status"] == "succeeded", merged
        assert merged["merge_state"] == "merged", merged
        assert merged["result"]["verification"]["override"] is True
        assert "return 2" in (repo / "app.py").read_text(encoding="utf-8")


def test_repo_delivery_commit_creates_task_branch(tmp_path: Path):
    repo, settings = _setup(tmp_path, ("true",))
    app = create_app(settings)
    with TestClient(app) as client:
        mapping = client.post("/api/mappings", json={
            "name": "demo", "path": str(repo), "delivery": "commit",
        })
        assert mapping.status_code == 201, mapping.text

        created = client.post("/api/tasks/manual", json={
            "title": "把返回值改成 2", "description": "main 返回 2",
            "kind": "bug", "repo_name": "demo",
            "exec_mode": "apply", "run_now": False, "approved": False,
        })
        item_id = created.json()["id"]
        client.post("/api/tasks/auto-run", json={"ids": [item_id]})

        item = None
        for _ in range(200):
            item = client.get(f"/api/items/{item_id}").json()
            if item["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.05)

        assert item is not None and item["status"] == "succeeded", item
        delivery = item["delivery"]
        assert delivery["mode"] == "commit"
        assert delivery["commit"]
        assert delivery["branch"].startswith("taskagent/")
        assert delivery["pushed"] is False
        assert "已提交" in item["stage"]

        # 主仓分支没有被提交，任务分支上有一个可评审的提交。
        main_count = subprocess.run(
            ["git", "-C", str(repo), "rev-list", "--count", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert main_count == "1"
        subject = subprocess.run(
            ["git", "-C", str(repo), "log", "-1", "--format=%s", delivery["branch"]],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert subject == "taskagent: 把返回值改成 2"
        on_branch = subprocess.run(
            ["git", "-C", str(repo), "show", f"{delivery['branch']}:app.py"],
            capture_output=True, text=True, check=True,
        ).stdout
        assert "return 2" in on_branch

        report = client.get(f"/api/items/{item_id}/report").text
        assert "### 交付" in report
        assert delivery["commit"] in report
