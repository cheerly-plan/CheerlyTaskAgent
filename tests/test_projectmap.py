from __future__ import annotations

from pathlib import Path

import pytest

from taskagent.projectmap import ProjectMappingStore, ProjectRegistry


@pytest.fixture
def folders(tmp_path: Path) -> tuple[Path, Path]:
    a = tmp_path / "CheerlyAIChat"
    b = tmp_path / "ai_agent_backend"
    a.mkdir()
    b.mkdir()
    return a, b


@pytest.fixture
def registry(tmp_path: Path, folders: tuple[Path, Path]) -> ProjectRegistry:
    store = ProjectMappingStore(tmp_path / "projects.json")
    reg = ProjectRegistry(store)
    reg.add({"name": "CheerlyAIChat", "aliases": ["ai_chat", "AI 聊天"],
             "path": str(folders[0])})
    reg.add({"name": "CheerlyAgent", "aliases": ["ai_agent_backend"],
             "path": str(folders[1])})
    return reg


def test_alias_match_cjk_and_ascii(registry: ProjectRegistry, folders):
    assert registry.resolve_text("修复 ai_chat 的登录 bug").path == str(folders[0])
    assert registry.resolve_text("【BUG】AI 聊天白屏").path == str(folders[0])
    assert registry.resolve_text("登录接口 500") is None


def test_alias_case_insensitive(registry: ProjectRegistry, folders):
    assert registry.resolve_text("修一下 AI_CHAT").path == str(folders[0])
    assert registry.resolve_text("cheerlyaichat 崩了").path == str(folders[0])


def test_ascii_alias_word_boundary(folders: tuple[Path, Path], tmp_path: Path):
    """'chat' must not match inside 'ai_chat' - use delimiter-aware guard."""
    store = ProjectMappingStore(tmp_path / "p.json")
    reg = ProjectRegistry(store)
    reg.add({"name": "ChatApp", "aliases": ["chat"], "path": str(folders[0])})
    assert reg.resolve_text("chat 崩了") is not None          # standalone
    # 'chat' inside 'ai_chat' is preceded by '_' => should NOT match
    assert reg.resolve_text("请修 ai_chat 模块") is None


def test_longest_alias_wins(tmp_path: Path, folders):
    store = ProjectMappingStore(tmp_path / "p.json")
    reg = ProjectRegistry(store)
    reg.add({"name": "Generic", "aliases": ["agent"], "path": str(folders[1])})
    reg.add({"name": "Specific", "aliases": ["ai_agent_backend", "agent backend"],
             "path": str(folders[0])})
    hit = reg.resolve_text("修复 agent backend 的问题")
    assert hit is not None and hit.path == str(folders[0])


def test_disabled_mapping_skipped(registry: ProjectRegistry, folders):
    m = registry.list()[0]
    registry.update(m.id, {"enabled": False})
    assert registry.resolve_text("修复 ai_chat 登录") is None
    assert registry.resolve_text("修复 ai_agent_backend") is not None


def test_crud_persists(registry: ProjectRegistry, tmp_path: Path, folders):
    # update
    m = registry.list()[0]
    registry.update(m.id, {"note": "changed"})
    # toggle-style partial update must not wipe name/path
    registry.update(m.id, {"enabled": False})
    reloaded = ProjectRegistry(ProjectMappingStore(tmp_path / "projects.json"))
    item = next(x for x in reloaded.list() if x.id == m.id)
    assert item.note == "changed"
    assert item.name == "CheerlyAIChat"
    assert item.path == str(folders[0])
    assert item.enabled is False
    # delete
    assert reloaded.remove(m.id) is True
    assert reloaded.get(m.id) is None


def test_seed_initialization(tmp_path: Path, folders):
    seed = tmp_path / "seed.json"
    seed.write_text('{"mappings":[{"id":"s1","name":"SeedProj",'
                    '"aliases":["seedproj"],"path":"' + str(folders[0]) + '"}]}',
                    encoding="utf-8")
    reg = ProjectRegistry(ProjectMappingStore(tmp_path / "data" / "projects.json", seed=seed))
    assert len(reg.list()) == 1
    assert reg.resolve_text("修 seedproj") is not None


def test_one_workspace_maps_to_multiple_local_repos(tmp_path: Path, folders):
    """一个 TAPD 项目可关联多个本地仓库，旧的单字段仍能反查。"""
    reg = ProjectRegistry(ProjectMappingStore(tmp_path / "projects.json"))
    a = reg.add({
        "name": "RepoA", "aliases": [], "path": str(folders[0]),
        "tapd_workspace_ids": ["32312011", "755"],
    })
    b = reg.add({
        "name": "RepoB", "aliases": [], "path": str(folders[1]),
        "tapd_workspace_id": "32312011",  # 旧字段写入
    })
    found = reg.find_all_by_workspace("32312011")
    assert {m.id for m in found} == {a.id, b.id}
    assert [m.id for m in reg.find_all_by_workspace("755")] == [a.id]
    assert reg.find_by_workspace("32312011").id == a.id
    assert a.tapd_workspace_id == "32312011"
    reloaded = ProjectRegistry(ProjectMappingStore(tmp_path / "projects.json"))
    assert reloaded.get(b.id).tapd_workspace_ids == ["32312011"]


def test_find_by_workspace_and_roundtrip(tmp_path: Path, folders):
    """tapd_workspace_id 字段能持久化，且能按 workspace 反查本地映射。"""
    store = ProjectMappingStore(tmp_path / "projects.json")
    reg = ProjectRegistry(store)
    m = reg.add({
        "name": "BoundProj", "aliases": ["bound"],
        "path": str(folders[0]), "tapd_workspace_id": "32312011",
    })
    # 反查命中
    found = reg.find_by_workspace("32312011")
    assert found is not None and found.id == m.id
    assert reg.find_by_workspace("999") is None
    # 停用后不再命中
    reg.update(m.id, {"enabled": False})
    assert reg.find_by_workspace("32312011") is None
    # 字段往返
    reloaded = ProjectRegistry(ProjectMappingStore(tmp_path / "projects.json"))
    assert reloaded.get(m.id).tapd_workspace_id == "32312011"


def test_mapping_api_persists_tapd_bindings(tmp_path):
    """TAPD 项目绑定必须随映射一起保存（此前被请求模型静默丢弃）。"""
    from fastapi.testclient import TestClient

    from taskagent.api import create_app
    from taskagent.config import Settings

    app = create_app(Settings(data_dir=tmp_path, project_roots=()))
    with TestClient(app) as client:
        created = client.post(
            "/api/mappings",
            json={"name": "Demo", "path": str(tmp_path), "tapd_workspace_ids": ["32312011", "1001"]},
        )
        assert created.status_code == 201
        assert created.json()["tapd_workspace_ids"] == ["32312011", "1001"]

        listed = client.get("/api/mappings").json()["mappings"]
        assert listed[0]["tapd_workspace_ids"] == ["32312011", "1001"]
        assert listed[0]["tapd_workspace_id"] == "32312011"


def test_auto_map_endpoint_backfills_unmapped_tapd_items(tmp_path):
    """先建单、后补绑定：自动映射按 TAPD 项目把历史工单补上仓库。"""
    from fastapi.testclient import TestClient

    from taskagent.api import create_app
    from taskagent.config import Settings
    from taskagent.models import SourceType, WorkItem

    app = create_app(Settings(data_dir=tmp_path, project_roots=()))
    with TestClient(app) as client:
        client.post(
            "/api/mappings",
            json={"name": "Demo", "path": str(tmp_path), "tapd_workspace_ids": ["32312011"]},
        )
        store = app.state.ctx.store
        item = WorkItem.create(
            title="存量需求", description="", source=SourceType.TAPD.value,
            source_ref="tapd:story:1132312011001000017",
        )
        store.save(item)

        result = client.post("/api/items/auto-map").json()
        assert result["mapped"] == 1

        mapped = client.get(f"/api/items/{item.id}").json()
        assert mapped["repo"]["name"] == "Demo"
        assert mapped["status"] == "ready"


def test_auto_map_leaves_ambiguous_workspace_alone(tmp_path):
    """同一项目绑定多个仓库时无法自动选择，必须留给人工。"""
    from fastapi.testclient import TestClient

    from taskagent.api import create_app
    from taskagent.config import Settings
    from taskagent.models import SourceType, WorkItem

    app = create_app(Settings(data_dir=tmp_path, project_roots=()))
    with TestClient(app) as client:
        for name in ("A", "B"):
            folder = tmp_path / name
            folder.mkdir()
            client.post(
                "/api/mappings",
                json={"name": name, "path": str(folder), "tapd_workspace_ids": ["35825536"]},
            )
        store = app.state.ctx.store
        item = WorkItem.create(
            title="多仓库需求", description="", source=SourceType.TAPD.value,
            source_ref="tapd:story:1135825536001000036",
        )
        store.save(item)

        result = client.post("/api/items/auto-map").json()
        assert result["mapped"] == 0
        assert result["ambiguous"] == 1
        assert client.get(f"/api/items/{item.id}").json()["repo"] is None
