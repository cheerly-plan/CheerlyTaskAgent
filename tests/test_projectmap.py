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
