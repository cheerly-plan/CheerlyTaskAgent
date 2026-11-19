from __future__ import annotations

from fastapi.testclient import TestClient

from taskagent.api import create_app
from taskagent.config import Settings
from taskagent.sources import RawItem
from taskagent.tapd import TapdClient, TapdConfig, TapdError, TapdItem


def test_list_projects_parses_workspace_rows(monkeypatch):
    """list_projects 用 company_id 调 /workspaces/projects，解析行并过滤企业自身。"""
    cfg = TapdConfig(api_user="u", api_password="p", workspace_id="1")
    client = TapdClient(cfg)
    seen = {}

    def fake_request(method, path, *, params=None, data=None):
        seen["path"] = path
        seen["params"] = params
        return {"status": 1, "data": [
            {"Workspace": {"id": "20003261", "name": "我的企业", "category": "organization"}},
            {"Workspace": {"id": "755", "name": "TAPD平台", "pretty_name": "tapd", "category": "project"}},
            {"Workspace": {"id": "32312011", "name": "Cheerly", "pretty_name": "", "category": "project"}},
            {"Workspace": {}},  # 缺 id 的行应被跳过
        ]}

    monkeypatch.setattr(client, "_request", fake_request)
    projects = client.list_projects(" 62957685 ")
    assert seen["path"] == "workspaces/projects"
    assert seen["params"] == {"company_id": "62957685"}
    # 企业行（category=organization）不进候选，避免把企业当成项目。
    assert projects == [
        {"id": "755", "name": "TAPD平台", "pretty_name": "tapd", "company_id": "62957685"},
        {"id": "32312011", "name": "Cheerly", "pretty_name": "", "company_id": "62957685"},
    ]


def test_list_projects_requires_company_id():
    client = TapdClient(TapdConfig(api_user="u", api_password="p"))
    try:
        client.list_projects("  ")
        assert False, "should raise"
    except TapdError as exc:
        assert "company_id" in str(exc)


def test_workspaces_endpoint_uses_request_body(tmp_path, monkeypatch):
    """端点从请求体取 company_id，回退到已保存配置。"""
    s = Settings(data_dir=tmp_path)
    s.save_ui_settings({"tapd": {"api_user": "u", "api_password": "p", "company_id": "saved42"}})
    app = create_app(Settings(data_dir=tmp_path, project_roots=()))
    calls = {}

    def fake_list(self, company_id):
        calls["company_id"] = company_id
        return [{"id": "1", "name": "P", "pretty_name": "p"}]

    monkeypatch.setattr(TapdClient, "list_projects", fake_list)
    with TestClient(app) as client:
        # 请求体未给 company_id -> 回退到配置里的 saved42
        r = client.post("/api/tapd/workspaces", json={})
        assert r.status_code == 200
        assert r.json()["projects"][0]["id"] == "1"
        assert calls["company_id"] == "saved42"
        # 请求体给了 company_id -> 优先用请求体（不再需要 nick）
        r = client.post("/api/tapd/workspaces", json={"company_id": "9"})
        assert r.status_code == 200
        assert calls["company_id"] == "9"


def test_tapd_config_strips_whitespace_from_credentials():
    """粘贴凭证时带入的制表符/换行会被清理，否则 Basic Auth 返回 401。"""
    cfg = TapdConfig(
        base_url="  https://api.tapd.cn/  ",
        workspace_id="\t32312011 ",
        api_user=" iuu4oPy6\n",
        api_password="\tD9D3BD3D-87BA-FB8E-8B28-7DB923CCE92B\n",
        webhook_secret="  shh  ",
    )
    assert cfg.base_url == "https://api.tapd.cn"
    assert cfg.workspace_id == "32312011"
    assert cfg.api_user == "iuu4oPy6"
    assert cfg.api_password == "D9D3BD3D-87BA-FB8E-8B28-7DB923CCE92B"
    assert cfg.webhook_secret == "shh"


def test_tapd_settings_defaults_and_roundtrip(tmp_path):
    s = Settings(data_dir=tmp_path)
    initial = s.load_ui_settings()["tapd"]
    assert initial["enabled"] is False
    assert initial["base_url"] == "https://api.tapd.cn"
    assert initial["workspace_id"] == ""
    assert initial["entity_types"] == ["story", "bug"]
    assert initial["status_on_success"] == "resolved"
    assert initial["exec_mode"] == "dry-run"

    s.save_ui_settings({"tapd": {
        "enabled": True,
        "workspace_id": "10104801",
        "api_user": "app",
        "api_password": "secret-1",
        "entity_types": ["bug"],
        "pull_statuses": "planning, in_progress",
        "interval_minutes": 2,
        "exec_mode": "apply",
    }})
    loaded = s.load_ui_settings()["tapd"]
    assert loaded["enabled"] is True
    assert loaded["workspace_id"] == "10104801"
    assert loaded["api_password"] == "secret-1"
    assert loaded["entity_types"] == ["bug"]
    assert loaded["pull_statuses"] == ["planning", "in_progress"]
    assert loaded["exec_mode"] == "apply"

    # Blank password keeps stored value; partial update keeps other fields.
    s.save_ui_settings({"tapd": {"api_password": "", "enabled": False}})
    again = s.load_ui_settings()["tapd"]
    assert again["api_password"] == "secret-1"
    assert again["enabled"] is False
    assert again["workspace_id"] == "10104801"


def test_tapd_normalises_bad_values(tmp_path):
    s = Settings(data_dir=tmp_path)
    s.save_ui_settings({"tapd": {
        "interval_minutes": 0,
        "entity_types": ["bogus", "story"],
        "exec_mode": "nonsense",
    }})
    cfg = s.load_ui_settings()["tapd"]
    assert cfg["interval_minutes"] == 5
    assert cfg["entity_types"] == ["story"]  # bogus dropped
    assert cfg["exec_mode"] == "dry-run"


def test_project_cache_normalisation(tmp_path):
    """项目缓存保留 company_id、去重、过滤无效行，供企业下拉复用。"""
    s = Settings(data_dir=tmp_path)
    s.save_ui_settings({"tapd": {
        "workspace_id": " 32312011，755 ",
        "projects": [
            {"id": "755", "name": " TAPD平台 ", "pretty_name": "tapd", "company_id": " 42 "},
            {"id": " 755 ", "name": "重复项", "company_id": "42"},
            {"id": "32312011", "name": "Cheerly"},
            {"id": "", "name": "无 ID"},
            "bad row",
        ],
    }})
    cfg = s.load_ui_settings()["tapd"]
    assert cfg["workspace_id"] == "32312011,755"
    assert cfg["projects"] == [
        {"id": "755", "name": "TAPD平台", "pretty_name": "tapd", "company_id": "42"},
        {"id": "32312011", "name": "Cheerly", "pretty_name": "", "company_id": ""},
    ]


def test_multi_workspace_fetch_uses_each_workspace_and_dedupes(monkeypatch):
    """多选项目时逐个 workspace_id 拉取，并过滤跨项目重复 ID。"""
    cfg = TapdConfig(
        api_user="u", api_password="p", workspace_id=" 1，2 ",
        entity_types=["story"],
    )
    client = TapdClient(cfg)
    seen = []

    def fake_request(method, path, *, params=None, data=None):
        seen.append(params["workspace_id"])
        if params["workspace_id"] == "1":
            return {"status": 1, "data": [
                {"Story": {"id": "10", "name": "A"}},  # 缺 workspace_id，应补当前工作区
            ]}
        return {"status": 1, "data": [
            {"Story": {"id": "10", "name": "A dup", "workspace_id": "2"}},
            {"Story": {"id": "20", "name": "B", "workspace_id": "2"}},
        ]}

    monkeypatch.setattr(client, "_request", fake_request)
    items = client.fetch("story")
    assert seen == ["1", "2"]
    assert cfg.primary_workspace_id == "1"
    assert [item.id for item in items] == ["10", "20"]
    assert items[0].workspace_id == "1"


def test_client_not_configured_reports_missing():
    client = TapdClient(TapdConfig())
    assert not client.config.is_configured()
    assert set(client.config.missing()) == {"workspace_id", "api_user", "api_password"}
    # fetch_all swallows per-entity errors into result.error (service resilience),
    # while the low-level request raises TapdError.
    result = client.fetch_all()
    assert "未配置" in result.error
    assert result.items == []
    try:
        client._request("GET", "stories")
    except TapdError as exc:
        assert "未配置" in str(exc)
    else:
        raise AssertionError("_request should raise when unconfigured")


def test_normalise_story_and_bug():
    story = TapdClient._normalise("story", {
        "id": "123", "workspace_id": "9", "name": "登录优化",
        "description": "详情", "status": "planning", "owner": "alice",
        "priority_label": "高", "modified": "2026-01-01 10:00:00",
    })
    assert story.id == "123"
    assert story.title == "登录优化"
    assert story.ref == "tapd:story:123"
    assert story.owner == "alice"

    bug = TapdClient._normalise("bug", {
        "id": "456", "workspace_id": "9", "title": "崩溃",
        "status": "in_progress", "custom_field_git_url": "https://github.com/a/b.git",
    })
    assert bug.ref == "tapd:bug:456"
    assert bug.git_url_hint.endswith(".git")


def test_tapd_raw_ref_drives_dedupe():
    item = TapdItem(
        entity_type="story", id="777", workspace_id="9",
        title="需求", description="", status="planning", owner="",
        priority="", iteration_id="", created="", modified="",
    )
    raw = RawItem.from_tapd(item)
    assert raw.source_ref == "tapd:story:777"
    assert raw.title.startswith("[需求]")


def test_sync_endpoint_without_credentials(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, project_roots=()))
    with TestClient(app) as client:
        r = client.post("/api/tapd/sync")
        assert r.status_code == 400
        assert "TAPD 未配置" in r.json()["detail"]


def test_webhook_rejects_bad_secret_and_missing_fields(tmp_path):
    s = Settings(data_dir=tmp_path)
    s.save_ui_settings({"tapd": {"webhook_secret": "shh"}})
    app = create_app(Settings(data_dir=tmp_path, project_roots=()))
    with TestClient(app) as client:
        # wrong secret -> 403
        r = client.post("/api/tapd/webhook", json={
            "event": "story::create", "id": "1", "secret": "wrong",
        })
        assert r.status_code == 403
        # correct secret but missing id -> 400
        r = client.post("/api/tapd/webhook", json={
            "event": "story::create", "secret": "shh",
        })
        assert r.status_code == 400
        # delete events are ignored without hitting the API
        r = client.post("/api/tapd/webhook", json={
            "event": "story::delete", "id": "1", "secret": "shh",
        })
        assert r.status_code == 200
        assert r.json().get("ignored") is True


def test_sync_force_ignores_incremental_cursor(tmp_path, monkeypatch):
    """增量游标只拉「修改过」的条目；force=True 忽略游标做全量重拉。"""
    from taskagent import tapd as tapd_module
    from taskagent.api import create_app
    from taskagent.config import Settings

    settings = Settings(data_dir=tmp_path, project_roots=())
    ui = settings.load_ui_settings()
    # enabled=False 关掉后台定时轮询，避免它在测试期间也调用 fetch_all
    ui["tapd"].update(
        {"enabled": False, "workspace_id": "32312011", "api_user": "u", "api_password": "p"}
    )
    settings.save_ui_settings(ui)

    item = tapd_module.TapdItem(
        entity_type="story", id="1132312011001000017", workspace_id="32312011",
        title="需求 A", description="", status="open", owner="", priority="",
        iteration_id="", created="2026-01-01 09:00:00", modified="2026-01-01 10:00:00",
    )
    cursors: list[str] = []

    def fake_fetch_all(self, *, modified_since="", limit=200, max_pages=5):
        cursors.append(modified_since)
        result = tapd_module.TapdResult()
        if not modified_since:  # 全量才返回条目
            result.items.append(item)
        return result

    monkeypatch.setattr(tapd_module.TapdClient, "fetch_all", fake_fetch_all)
    app = create_app(settings)
    with TestClient(app) as client:
        client.post("/api/tapd/sync", json={})          # 首次：无游标
        client.post("/api/tapd/sync", json={})          # 增量：带游标
        client.post("/api/tapd/sync", json={"force": True})  # 全量：忽略游标

    assert cursors[0] == ""
    assert cursors[1] == "2026-01-01 10:00:00"
    assert cursors[2] == ""


def _make_tapd_app(tmp_path):
    """Build an app with TAPD configured but polling disabled."""
    from taskagent.config import Settings

    settings = Settings(data_dir=tmp_path, project_roots=())
    ui = settings.load_ui_settings()
    ui["tapd"].update({
        "enabled": False,
        "workspace_id": "32312011",
        "api_user": "u",
        "api_password": "p",
    })
    settings.save_ui_settings(ui)
    return settings


def test_preview_endpoint_without_credentials(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, project_roots=()))
    with TestClient(app) as client:
        r = client.post("/api/tapd/preview")
        assert r.status_code == 400
        assert "TAPD 未配置" in r.json()["detail"]


def test_preview_classifies_new_changed_unchanged_without_writing(tmp_path, monkeypatch):
    """Preview tags items by diff vs local tasks and does NOT persist anything."""
    from taskagent import tapd as tapd_module
    from taskagent.models import WorkItem

    settings = _make_tapd_app(tmp_path)
    new_item = tapd_module.TapdItem(
        entity_type="story", id="100", workspace_id="32312011",
        title="全新需求", description="desc-new", status="open", owner="", priority="",
        iteration_id="", created="2026-01-01 09:00:00", modified="2026-01-02 10:00:00",
    )
    changed_item = tapd_module.TapdItem(
        entity_type="bug", id="200", workspace_id="32312011",
        title="已改标题", description="desc-changed", status="open", owner="", priority="",
        iteration_id="", created="2026-01-01 09:00:00", modified="2026-01-03 10:00:00",
    )
    same_item = tapd_module.TapdItem(
        entity_type="story", id="300", workspace_id="32312011",
        title="未变化需求", description="desc-same", status="open", owner="", priority="",
        iteration_id="", created="2026-01-01 09:00:00", modified="2026-01-04 10:00:00",
    )

    def fake_fetch_all(self, *, modified_since="", limit=200, max_pages=5):
        result = tapd_module.TapdResult()
        result.items = [new_item, changed_item, same_item]
        return result

    monkeypatch.setattr(tapd_module.TapdClient, "fetch_all", fake_fetch_all)
    app = create_app(settings)
    with TestClient(app) as client:
        # Seed the two existing tasks directly in the store (status=DONE-ish data kept).
        from taskagent.store import TaskStore
        from taskagent.sources import parse_raw
        store = TaskStore(settings.tasks_dir)
        # Seed with exactly what parse_raw produces so "unchanged" truly matches.
        changed_probe = parse_raw(RawItem.from_tapd(changed_item))
        changed_old = WorkItem.create(
            title="旧标题", description=changed_probe.description,
            source="tapd", source_ref="tapd:bug:200",
        )
        same_probe = parse_raw(RawItem.from_tapd(same_item))
        same_old = WorkItem.create(
            title=same_probe.title, description=same_probe.description,
            source="tapd", source_ref="tapd:story:300",
        )
        store.save(changed_old)
        store.save(same_old)

        r = client.post("/api/tapd/preview", json={})
        assert r.status_code == 200, r.text
        data = r.json()
        by_ref = {i["source_ref"]: i for i in data["items"]}

        assert by_ref["tapd:story:100"]["status"] == "new"
        assert by_ref["tapd:bug:200"]["status"] == "changed"
        assert "title" in by_ref["tapd:bug:200"]["changes"]
        assert by_ref["tapd:story:300"]["status"] == "unchanged"
        assert data["nonce"]

        # Preview must not create tasks: only the 2 seeded ones remain.
        assert client.get("/api/items").json()["total"] == 2


def test_import_applies_new_and_updated_and_advances_cursor(tmp_path, monkeypatch):
    """Import writes only checked items: new -> created, changed -> updated."""
    from taskagent import tapd as tapd_module
    from taskagent.models import WorkItem
    from taskagent.store import TaskStore

    settings = _make_tapd_app(tmp_path)
    new_item = tapd_module.TapdItem(
        entity_type="story", id="100", workspace_id="32312011",
        title="全新需求", description="desc-new", status="open", owner="", priority="",
        iteration_id="", created="2026-01-01 09:00:00", modified="2026-01-02 10:00:00",
    )
    changed_item = tapd_module.TapdItem(
        entity_type="bug", id="200", workspace_id="32312011",
        title="已改标题", description="desc-changed", status="open", owner="", priority="",
        iteration_id="", created="2026-01-01 09:00:00", modified="2026-01-03 10:00:00",
    )

    def fake_fetch_all(self, *, modified_since="", limit=200, max_pages=5):
        result = tapd_module.TapdResult()
        result.items = [new_item, changed_item]
        return result

    monkeypatch.setattr(tapd_module.TapdClient, "fetch_all", fake_fetch_all)
    app = create_app(settings)
    with TestClient(app) as client:
        from taskagent.sources import parse_raw
        store = TaskStore(settings.tasks_dir)
        changed_probe = parse_raw(RawItem.from_tapd(changed_item))
        changed_old = WorkItem.create(
            title="旧标题", description=changed_probe.description,
            source="tapd", source_ref="tapd:bug:200",
        )
        from taskagent.models import ItemStatus
        changed_old.status = ItemStatus.SUCCEEDED
        store.save(changed_old)

        pv = client.post("/api/tapd/preview", json={}).json()
        # Import BOTH the new item and the changed one.
        r = client.post("/api/tapd/import", json={
            "items": [
                {"source_ref": "tapd:story:100", "executor": "codebuddy", "model": "hy4flash", "exec_mode": "dry-run"},
                {"source_ref": "tapd:bug:200", "executor": "codebuddy", "model": "glm-5.3", "exec_mode": "dry-run"},
            ],
            "cursor": pv["cursor"],
        })
        assert r.status_code == 200, r.text
        result = r.json()
        assert result["created"] == 1
        assert result["updated"] == 1
        assert result["skipped"] == 0

        # Changed task updated in place (title refreshed, status/history kept).
        updated = store.get(changed_old.id)
        assert updated.title == "已改标题"
        assert updated.status == "succeeded"
        assert updated.model == "glm-5.3"

        # New task created with the chosen config.
        items = client.get("/api/items").json()["items"]
        created = next(i for i in items if i["source_ref"] == "tapd:story:100")
        assert created["model"] == "hy4flash"
