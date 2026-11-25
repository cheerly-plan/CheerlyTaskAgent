from __future__ import annotations

from fastapi.testclient import TestClient

from taskagent.api import create_app
from taskagent.config import Settings


def test_ui_settings_defaults_and_roundtrip(tmp_path):
    s = Settings(data_dir=tmp_path)
    initial = s.load_ui_settings()
    assert initial["codebuddy_command"] == "codebuddy"

    # dsh 的路径以 backends 为权威：改 backends.dsh.command 后扁平字段 dsh_path 应镜像它。
    initial["backends"]["dsh"]["command"] = "/opt/dsh"
    s.save_ui_settings(initial)

    loaded = s.load_ui_settings()
    assert loaded["backends"]["dsh"]["command"] == "/opt/dsh"
    assert loaded["dsh_path"] == "/opt/dsh"  # 扁平字段从 backends 派生，保持一致


def test_ui_settings_blank_password_keeps_existing(tmp_path):
    s = Settings(data_dir=tmp_path)
    data = s.load_ui_settings()
    data["tapd"]["api_password"] = "secret"
    s.save_ui_settings(data)

    # The UI masks the secret and submits an empty value on unrelated edits.
    s.save_ui_settings({"tapd": {"api_password": ""}})
    assert s.load_ui_settings()["tapd"]["api_password"] == "secret"


def test_data_source_migrate_and_roundtrip(tmp_path):
    s = Settings(data_dir=tmp_path)
    # 初始无实质 data_source，也无 tapd 旧单例，默认源为「未启用的 TAPD」。
    loaded = s.load_ui_settings()
    assert loaded["data_source"]["type"] == "tapd"
    assert loaded["data_source"]["enabled"] is False
    assert loaded["data_source"]["tapd"] == {}

    # 先写旧 tapd 单例（模拟历史文件），load 时应迁移进 data_source。
    s.save_ui_settings({"tapd": {"enabled": True, "workspace_id": "101", "api_user": "app"}})
    loaded = s.load_ui_settings()
    ds = loaded["data_source"]
    assert ds["type"] == "tapd" and ds["enabled"] is True
    assert ds["tapd"]["workspace_id"] == "101"

    # 显式保存 data_source，legacy tapd 单例应同步为该源的 tapd 载荷。
    s.save_ui_settings({"data_source": {
        "type": "tapd", "name": "TAPD", "enabled": True,
        "tapd": {"workspace_id": "1,2", "api_user": "u1", "api_password": "p1"},
    }})
    loaded = s.load_ui_settings()
    assert loaded["data_source"]["type"] == "tapd"
    assert loaded["data_source"]["enabled"] is True
    assert loaded["data_source"]["tapd"]["workspace_id"] == "1,2"
    assert loaded["tapd"]["workspace_id"] == "1,2"
    assert loaded["tapd"]["enabled"] is True

    # 空白密码保留旧值。
    s.save_ui_settings({"data_source": {
        "type": "tapd", "name": "TAPD", "enabled": True,
        "tapd": {"api_user": "u1", "api_password": ""},
    }})
    assert s.load_ui_settings()["data_source"]["tapd"]["api_password"] == "p1"


def test_settings_api_roundtrip(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, project_roots=()))
    with TestClient(app) as client:
        r = client.get("/api/settings")
        assert r.status_code == 200
        assert r.json()["executor"] == "codebuddy"

        payload = {
            "executor": "codebuddy",
            "codebuddy_command": "npx -y @tencent-ai/codebuddy-code@latest",
            "dsh_path": "/opt/dsh",
            "default_model": "deep-model",
            "max_concurrent": 2,
            "auto_sync": False,
        }
        r = client.put("/api/settings", json=payload)
        assert r.status_code == 200
        saved = r.json()
        assert saved["codebuddy_command"] == payload["codebuddy_command"]
        assert saved["auto_sync"] is False

        # Reload through the app to prove persistence, not just response echo.
        assert client.get("/api/settings").json() == saved


def test_agent_model_config_roundtrip_and_blank_key_keeps_value(tmp_path):
    """模型配置经 /api/settings 持久化；空 api_key 表示保持原值（UI 掩码）。"""
    app = create_app(Settings(data_dir=tmp_path, project_roots=()))
    with TestClient(app) as client:
        payload = client.get("/api/settings").json()
        assert payload["models"] == []
        assert payload["agent_model"] == ""
        payload["models"] = [{
            "id": "qwen-max",
            "label": "Qwen Max",
            "provider": "Qwen",
            "base_url": "https://example.invalid/v1",
            "api_key": "sk-secret",
            "enabled": True,
        }]
        payload["agent_model"] = "qwen-max"
        saved = client.put("/api/settings", json=payload).json()
        assert saved["models"][0]["id"] == "qwen-max"
        assert saved["models"][0]["api_key"] == "sk-secret"
        assert saved["agent_model"] == "qwen-max"

        # 提交空秘钥不应把已保存的秘钥冲掉。
        payload = client.get("/api/settings").json()
        payload["models"][0]["api_key"] = ""
        saved = client.put("/api/settings", json=payload).json()
        assert saved["models"][0]["api_key"] == "sk-secret"

        # 指向不存在的模型时回退为空（后端再用第一个启用的模型）。
        payload["agent_model"] = "missing"
        saved = client.put("/api/settings", json=payload).json()
        assert saved["agent_model"] == ""


def test_model_config_does_not_change_cli_model_choices(tmp_path):
    """后端 Agent 的模型配置不能混进执行端的模型下拉。"""
    app = create_app(Settings(data_dir=tmp_path, project_roots=()))
    with TestClient(app) as client:
        payload = client.get("/api/settings").json()
        payload["models"] = [{
            "id": "backend-only",
            "label": "后端专用",
            "provider": "Vendor",
            "base_url": "https://example.invalid/v1",
            "api_key": "sk-secret",
            "enabled": True,
        }]
        client.put("/api/settings", json=payload)
        choices = client.get("/api/config").json()["models"]
        assert all(choice["id"] != "backend-only" for choice in choices)


def test_saving_settings_applies_without_restart(tmp_path):
    """PUT /api/settings must refresh executors and concurrency in place.

    Previously both were snapshotted at startup, so the UI told the user to
    restart the service. Saving should now take effect for the next run.
    """
    app = create_app(Settings(data_dir=tmp_path, project_roots=()))
    with TestClient(app) as client:
        before = client.get("/api/config").json()
        assert before["max_concurrent"] == 1

        payload = client.get("/api/settings").json()
        payload["max_concurrent"] = 3
        payload["backends"]["dsh"]["command"] = "/opt/custom-dsh"
        payload["backends"]["dsh"]["enabled"] = True
        payload["executor"] = "dsh"
        saved = client.put("/api/settings", json=payload)
        assert saved.status_code == 200

        after = client.get("/api/config").json()
        # concurrency and default backend are live
        assert after["max_concurrent"] == 3
        assert after["executor"] == "dsh"
        dsh = next(b for b in after["backends"] if b["id"] == "dsh")
        # the rebuilt executor reads the new command (no restart)
        assert dsh["command"].endswith("custom-dsh")
        # scheduler's gate was resized too
        assert app.state.ctx.scheduler.settings.max_concurrent == 3


def test_backend_probe_reports_capability_on_demand(tmp_path):
    """The probe endpoint verifies capability without being on the config path.

    /api/config must not spawn CLI processes; the UI calls this once, the first
    time the user opens a model picker.
    """
    settings = Settings(data_dir=tmp_path, project_roots=())
    ui = settings.load_ui_settings()
    # Point dsh at a nonexistent binary so the probe deterministically degrades
    # to model_selection=False, regardless of whether DSH is installed locally.
    ui["backends"]["dsh"]["command"] = "/nonexistent/dsh-cli-for-test"
    settings.save_ui_settings(ui)
    app = create_app(settings)
    with TestClient(app) as client:
        # config is optimistic (no probe yet)
        config = client.get("/api/config").json()
        dsh = next(b for b in config["backends"] if b["id"] == "dsh")
        assert dsh["model_selection"] is True

        # probing a backend whose CLI cannot be run degrades to False, not a 500
        probed = client.post("/api/backends/dsh/probe")
        assert probed.status_code == 200
        body = probed.json()
        assert body["id"] == "dsh"
        assert body["model_selection"] is False
        assert isinstance(body["models"], list)

        missing = client.post("/api/backends/nope/probe")
        assert missing.status_code == 404


def test_discover_command_keeps_bare_name_when_on_path(monkeypatch):
    """PATH resolution wins: the bare name stays portable across machines."""
    from taskagent import config

    monkeypatch.setattr(config.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert config.discover_command("dsh") == "dsh"


def test_discover_command_falls_back_to_bundle(monkeypatch, tmp_path):
    """Desktop-app CLIs ship outside PATH; resolve the bundle launcher."""
    from taskagent import config

    fake = tmp_path / "dsh"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(config.shutil, "which", lambda name: None)
    monkeypatch.setitem(config.KNOWN_CLI_PATHS, "dsh", (str(fake),))
    assert config.discover_command("dsh") == str(fake)


def test_discover_command_trusts_explicit_path(monkeypatch):
    from taskagent import config

    monkeypatch.setattr(config.shutil, "which", lambda name: None)
    assert config.discover_command("/opt/custom/dsh") == "/opt/custom/dsh"


def test_load_ui_settings_resolves_bundled_cli(monkeypatch, tmp_path):
    """A fresh install shows a usable path instead of a bare name."""
    from taskagent import config

    fake = tmp_path / "dsh"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(config.shutil, "which", lambda name: None)
    monkeypatch.setitem(config.KNOWN_CLI_PATHS, "dsh", (str(fake),))

    s = Settings(data_dir=tmp_path / "data")
    assert s.load_ui_settings()["backends"]["dsh"]["command"] == str(fake)


def test_auto_run_endpoint_selects_only_requested(tmp_path):
    from fastapi.testclient import TestClient

    from taskagent.api import create_app

    app = create_app(Settings(data_dir=tmp_path, project_roots=()))
    with TestClient(app) as client:
        # 空选择直接 400；未知 id 如实回「任务不存在」。
        assert client.post("/api/tasks/auto-run", json={"ids": []}).status_code == 400
        result = client.post("/api/tasks/auto-run", json={"ids": ["missing"]}).json()
        assert result["started"] == []
        assert result["skipped"][0]["reason"] == "任务不存在"
        # 自动执行准入/熔断是一份结构化配置，默认不限制类型与仓库。
        automation = client.get("/api/settings").json()["automation"]
        assert automation["allowed_kinds"] == []
        assert automation["repo_allowlist"] == []
        assert automation["failure_threshold"] == 3


def test_delivery_global_settings_roundtrip(tmp_path):
    s = Settings(
        data_dir=tmp_path,
        delivery_mode="commit",
        delivery_merge="auto",
        delivery_remote="upstream",
    )
    # 首次运行：全局默认由环境变量播种。
    initial = s.load_ui_settings()["delivery"]
    assert initial["mode"] == "commit"
    assert initial["merge"] == "auto"
    assert initial["remote"] == "upstream"

    # 保存后以系统设置为准，且非法值被收敛。
    s.save_ui_settings({"delivery": {
        "mode": "pr", "merge": "manual", "remote": "origin",
        "base": "main", "provider": "github", "api_base": "https://ghe/api/v3",
    }})
    loaded = s.load_ui_settings()["delivery"]
    assert loaded["mode"] == "pr"
    assert loaded["merge"] == "manual"
    assert loaded["base"] == "main"
    assert loaded["provider"] == "github"

    # 局部更新不能把未提交的字段清空。
    s.save_ui_settings({"delivery": {"merge": "auto"}})
    loaded = s.load_ui_settings()["delivery"]
    assert loaded["mode"] == "pr"
    assert loaded["merge"] == "auto"

    # 非法枚举回落到安全默认。
    s.save_ui_settings({"delivery": {"mode": "wat", "merge": "x", "provider": "gitlab-x"}})
    loaded = s.load_ui_settings()["delivery"]
    assert loaded["mode"] == "off"
    assert loaded["merge"] == "manual"
    assert loaded["provider"] == ""


def test_settings_api_saves_global_delivery(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        saved = client.put("/api/settings", json={
            "delivery": {
                "mode": "pr", "merge": "auto", "remote": "origin",
                "base": "main", "provider": "github", "api_base": "",
            }
        })
        assert saved.status_code == 200, saved.text
        current = client.get("/api/settings").json()
        assert current["delivery"]["mode"] == "pr"
        assert current["delivery"]["merge"] == "auto"
        assert current["delivery"]["base"] == "main"
        assert current["delivery"]["provider"] == "github"


def test_automation_settings_roundtrip_and_circuit_api(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, project_roots=()))
    with TestClient(app) as client:
        current = client.get("/api/settings").json()
        assert current["automation"]["allowed_kinds"] == []
        assert current["automation"]["failure_threshold"] == 3

        saved = client.put("/api/settings", json={
            "automation": {
                "allowed_kinds": ["bug", "nope"],
                "repo_allowlist": ["CheerlyTaskAgent"],
                "failure_threshold": 2,
                "cooldown_minutes": 15,
                "max_recoveries": 2,
            }
        })
        assert saved.status_code == 200, saved.text
        automation = client.get("/api/settings").json()["automation"]
        assert automation["allowed_kinds"] == ["bug"]
        assert automation["repo_allowlist"] == ["CheerlyTaskAgent"]
        assert automation["failure_threshold"] == 2
        assert automation["max_recoveries"] == 2

        state = client.get("/api/automation").json()
        assert state["policy"]["failure_threshold"] == 2
        assert state["repos"] == {}
        reset = client.post("/api/automation/circuit/reset", json={}).json()
        assert reset["reset"] == 0
        assert reset["state"]["repos"] == {}


def test_decompose_settings_and_memory_endpoints(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, project_roots=()))
    with TestClient(app) as client:
        saved = client.put("/api/settings", json={
            "automation": {"decompose": True, "max_subtasks": 3},
        })
        assert saved.status_code == 200, saved.text
        automation = client.get("/api/settings").json()["automation"]
        assert automation["decompose"] is True
        assert automation["max_subtasks"] == 3

        memory = client.get("/api/memory").json()
        assert memory["stats"]["total"] == 0
        assert memory["clusters"] == []
        assert client.post("/api/memory/clear", json={}).json()["removed"] == 0

        # 不存在的任务：拆解接口如实 404。
        assert client.post("/api/items/missing/decompose", json={}).status_code == 404


def test_decompose_endpoint_requires_model(tmp_path):
    from taskagent.models import ItemStatus, LocalRepo, WorkItem
    from taskagent.store import TaskStore

    store = TaskStore(tmp_path / "tasks")
    item = WorkItem.create(title="跨仓需求", description="x", kind="feature")
    item.repo = LocalRepo(path=str(tmp_path), origin="", name="repo")
    item.status = ItemStatus.READY
    store.save(item)

    app = create_app(Settings(data_dir=tmp_path, project_roots=()))
    with TestClient(app) as client:
        response = client.post(f"/api/items/{item.id}/decompose", json={})
        assert response.status_code == 400
        # 没有候选仓库 / 没有模型都会被如实拒绝，不会瞎拆。
        assert response.json()["detail"]
