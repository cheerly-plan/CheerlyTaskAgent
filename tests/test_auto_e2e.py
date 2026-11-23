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


def test_manual_task_auto_runs_and_merges(tmp_path: Path):
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

    # 合并只改工作区，不产生提交。
    count = subprocess.run(
        ["git", "-C", str(repo), "rev-list", "--count", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert count == "1"