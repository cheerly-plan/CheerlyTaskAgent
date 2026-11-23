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


def test_normalise_extracts_parent_fields():
    child = TapdClient._normalise("story", {
        "id": "58", "workspace_id": "9", "name": "前端开发",
        "parent_id": "36", "children_id": "|",
    })
    assert child.parent_id == "36"
    assert child.children_ids == []
    assert child.parent_ref == "tapd:story:36"

    parent = TapdClient._normalise("story", {
        "id": "36", "workspace_id": "9", "name": "父需求",
        "parent_id": "0", "children_id": "|58|99|",
    })
    assert parent.parent_id == ""
    assert parent.children_ids == ["58", "99"]
    assert parent.parent_ref == ""

    task = TapdClient._normalise("task", {
        "id": "7", "workspace_id": "9", "title": "子任务",
        "story_id": "36", "parent_id": "0",
    })
    # task 的父是它归属的 story，parent_ref 固定按 story 构造
    assert task.parent_id == "36"
    assert task.parent_ref == "tapd:story:36"


def test_from_tapd_passes_parent_ref():
    from taskagent.sources import parse_raw

    child = TapdItem(
        entity_type="story", id="58", workspace_id="9",
        title="前端", description="", status="planning", owner="",
        priority="", iteration_id="", created="", modified="",
        parent_id="36",
    )
    raw = RawItem.from_tapd(child)
    assert raw.parent_ref == "tapd:story:36"
    assert parse_raw(raw).parent_ref == "tapd:story:36"
    assert parse_raw(raw).is_container is False


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
        from taskagent.sources import parse_raw
        from taskagent.store import TaskStore
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


def test_is_closed_status_recognises_en_and_zh():
    """已关闭状态英文 code 与中文名都要识别，活跃状态不误判。"""
    from taskagent.tapd import is_closed_status

    for status in ("resolved", "Verified", " DONE ", "closed", "rejected", "suspended",
                   "已解决", "已关闭", "已拒绝", "挂起"):
        assert is_closed_status(status), status
    for status in ("planning", "developing", "testing", "reopened", "new",
                   "in_progress", "open", "progressing", "", "reviewing"):
        assert not is_closed_status(status), status


def test_import_raw_archived_lands_in_history_not_queue(env):
    """已关闭条目入库后为归档终态：不映射仓库、不进执行队列、归到历史。"""
    scheduler, store, _, _, _ = env
    from taskagent.models import ItemStatus
    from taskagent.sources import RawItem

    raw = RawItem.from_email(
        subject="【需求】已关闭的旧需求",
        body="https://github.com/acme/app-one.git",
        message_id="closed-1",
    )
    item, created = scheduler.import_raw(raw, archived=True)
    assert created
    assert item.status == ItemStatus.SKIPPED
    assert item.exec_mode == "apply"
    assert item.repo is None
    assert item.is_terminal()
    assert store.get(item.id) is not None


def test_import_raw_active_still_maps_and_runs(env):
    """活跃状态仍按原逻辑映射并可进队列，回归保护。"""
    scheduler, _, _, _, _ = env
    from taskagent.models import ItemStatus
    from taskagent.sources import RawItem

    raw = RawItem.from_email(
        subject="【需求】进行中的需求",
        body="https://github.com/acme/app-one.git",
        message_id="active-1",
    )
    item, created = scheduler.import_raw(raw, archived=False)
    assert created
    assert item.status == ItemStatus.READY
    assert item.repo is not None


def test_sync_closes_status_archives_but_keeps_record(tmp_path, monkeypatch):
    """sync 端到端：已关闭条目进历史（skipped+apply），活跃条目留任务中心。"""
    from taskagent import tapd as tapd_module
    from taskagent.store import TaskStore

    settings = _make_tapd_app(tmp_path)
    active = tapd_module.TapdItem(
        entity_type="story", id="300", workspace_id="32312011",
        title="进行中需求", description="d", status="developing", owner="", priority="",
        iteration_id="", created="2026-01-01 09:00:00", modified="2026-01-02 10:00:00",
    )
    closed = tapd_module.TapdItem(
        entity_type="bug", id="400", workspace_id="32312011",
        title="已关闭缺陷", description="d", status="closed", owner="", priority="",
        iteration_id="", created="2026-01-01 09:00:00", modified="2026-01-02 10:00:00",
    )

    def fake_fetch_all(self, *, modified_since="", limit=200, max_pages=5):
        result = tapd_module.TapdResult()
        result.items = [active, closed]
        return result

    monkeypatch.setattr(tapd_module.TapdClient, "fetch_all", fake_fetch_all)
    app = create_app(settings)
    with TestClient(app) as client:
        r = client.post("/api/tapd/sync", json={"force": True})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["created"] == 1
        assert data["archived"] == 1

        items = client.get("/api/items?limit=100").json()["items"]
        active_item = next(i for i in items if i["source_ref"] == "tapd:story:300")
        closed_item = next(i for i in items if i["source_ref"] == "tapd:bug:400")
        assert active_item["status"] in ("discovered", "ready")
        assert closed_item["status"] == "skipped"
        assert closed_item["exec_mode"] == "apply"
        assert TaskStore(settings.tasks_dir).get(closed_item["id"]) is not None


def test_archived_item_revives_when_source_reopens(env):
    """已归档条目来源变活跃（reopened）后复活，回到任务中心而非留在历史。"""
    scheduler, store, _, _, _ = env
    from taskagent.models import ARCHIVED_SOURCE_STAGE, ItemStatus
    from taskagent.sources import RawItem

    raw = RawItem.from_email(subject="【缺陷】回归", body="x", message_id="reopen-1")
    archived, _ = scheduler.import_raw(raw, archived=True)
    assert archived.status == ItemStatus.SKIPPED
    assert store.get(archived.id).stage == ARCHIVED_SOURCE_STAGE

    # 同一来源重新活跃
    revived, created = scheduler.import_raw(raw, archived=False, auto_map=False)
    assert not created
    assert revived.id == archived.id
    assert revived.status == ItemStatus.DISCOVERED
    assert revived.exec_mode == "dry-run"
    assert revived.stage == ""


def test_preview_and_import_split_archived_from_created(tmp_path, monkeypatch):
    """已关闭条目在预览/导入结果中单独计入 archived，不再算作新增。"""
    from taskagent import tapd as tapd_module

    settings = _make_tapd_app(tmp_path)
    active = tapd_module.TapdItem(
        entity_type="story", id="500", workspace_id="32312011",
        title="新需求", description="d", status="developing", owner="", priority="",
        iteration_id="", created="2026-01-01 09:00:00", modified="2026-01-03 10:00:00",
    )
    closed = tapd_module.TapdItem(
        entity_type="bug", id="600", workspace_id="32312011",
        title="已关闭缺陷", description="d", status="closed", owner="", priority="",
        iteration_id="", created="2026-01-01 09:00:00", modified="2026-01-03 10:00:00",
    )

    def fake_fetch_all(self, *, modified_since="", limit=200, max_pages=5):
        result = tapd_module.TapdResult()
        result.items = [active, closed]
        return result

    monkeypatch.setattr(tapd_module.TapdClient, "fetch_all", fake_fetch_all)
    app = create_app(settings)
    with TestClient(app) as client:
        pv = client.post("/api/tapd/preview", json={}).json()
        by_ref = {i["source_ref"]: i for i in pv["items"]}
        assert by_ref["tapd:story:500"]["status"] == "new"
        assert by_ref["tapd:story:500"]["archived"] is False
        assert by_ref["tapd:bug:600"]["status"] == "new"
        assert by_ref["tapd:bug:600"]["archived"] is True

        r = client.post("/api/tapd/import", json={
            "items": [
                {"source_ref": "tapd:story:500", "exec_mode": "dry-run"},
                {"source_ref": "tapd:bug:600", "exec_mode": "dry-run"},
            ],
            "cursor": pv["cursor"],
        })
        assert r.status_code == 200, r.text
        result = r.json()
        assert result["created"] == 1
        assert result["archived"] == 1
        assert result["updated"] == 0


def test_sync_and_preview_skip_parent_stories(tmp_path, monkeypatch):
    """父需求（TAPD children_id 非空）不进任务库，也不出现在预览里。"""
    from taskagent import tapd as tapd_module

    settings = Settings(data_dir=tmp_path, project_roots=())
    ui = settings.load_ui_settings()
    ui["tapd"].update(
        {"enabled": False, "workspace_id": "9", "api_user": "u", "api_password": "p"}
    )
    settings.save_ui_settings(ui)

    parent = TapdItem(
        entity_type="story", id="36", workspace_id="9",
        title="父需求", description="", status="planning", owner="",
        priority="", iteration_id="", created="", modified="2026-01-01 10:00:00",
        children_ids=["58"],
    )
    child = TapdItem(
        entity_type="story", id="58", workspace_id="9",
        title="子需求", description="", status="planning", owner="",
        priority="", iteration_id="", created="", modified="2026-01-01 10:00:00",
        parent_id="36", parent_title="父需求",
    )

    def fake_fetch_all(self, *, modified_since="", limit=200, max_pages=5):
        result = tapd_module.TapdResult()
        result.items = [parent, child]
        return result

    monkeypatch.setattr(tapd_module.TapdClient, "fetch_all", fake_fetch_all)
    app = create_app(settings)
    with TestClient(app) as client:
        client.post("/api/tapd/sync", json={"force": True})
        refs = [item["source_ref"] for item in client.get("/api/items").json()["items"]]
        assert "tapd:story:36" not in refs
        assert "tapd:story:58" in refs

        preview = client.post("/api/tapd/preview", json={"force": True}).json()
        preview_refs = [item["source_ref"] for item in preview["items"]]
        assert "tapd:story:36" not in preview_refs
        assert "tapd:story:58" in preview_refs
