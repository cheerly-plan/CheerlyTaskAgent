"""LLM 路由配置的 API 集成测试：确认 UI 保存的规则能落盘并读回。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from taskagent.api import create_app
from taskagent.config import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        project_roots=(tmp_path / "projects",),
        project_map_file=tmp_path / "projects.json",
        commit_rules_file=tmp_path / "commit-rules.txt",
    )


def test_settings_round_trip_executor_routing(tmp_path: Path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        # 首次读取必须有默认结构（老配置兼容）
        data = client.get("/api/settings").json()
        assert data["executor_routing"] == {"enabled": False, "prompt": "", "timeout_s": 60.0}

        # 保存用户提示词 + 开关
        resp = client.put("/api/settings", json={
            "executor_routing": {
                "enabled": True,
                "prompt": "bug 用 codebuddy，feature 用 dsh",
                "timeout_s": 30,
            }
        })
        assert resp.status_code == 200, resp.text
        saved = resp.json()["executor_routing"]
        assert saved["enabled"] is True
        assert saved["prompt"] == "bug 用 codebuddy，feature 用 dsh"
        assert saved["timeout_s"] == 30.0

        # 重新读取确认落盘
        again = client.get("/api/settings").json()["executor_routing"]
        assert again["enabled"] is True
        assert again["prompt"] == "bug 用 codebuddy，feature 用 dsh"


def test_partial_update_keeps_prompt(tmp_path: Path):
    """只改开关不应清空用户写的提示词。"""
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        client.put("/api/settings", json={
            "executor_routing": {"enabled": False, "prompt": "我的规则", "timeout_s": 60}
        })
        client.put("/api/settings", json={"executor_routing": {"enabled": True}})
        cfg = client.get("/api/settings").json()["executor_routing"]
        assert cfg["enabled"] is True
        assert cfg["prompt"] == "我的规则"
