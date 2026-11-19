"""结构化画像：从仓库元数据推断角色（前端/后端/移动端）。"""

from __future__ import annotations

import json
from pathlib import Path

from taskagent.repoprofile import RepoProfileStore, build_profile, ensure_profiles


def _make_repo(root: Path, *, package: dict | None = None, readme: str = "", dirs: list[str] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if package is not None:
        (root / "package.json").write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
    if readme:
        (root / "README.md").write_text(readme, encoding="utf-8")
    for name in dirs or []:
        (root / name).mkdir(exist_ok=True)
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "index.ts").write_text("export {};\n", encoding="utf-8")
    return root


def test_profile_detects_frontend_from_dependencies(tmp_path):
    repo = _make_repo(
        tmp_path / "chat",
        package={"name": "cheerly-chat", "description": "会员聊天前端", "dependencies": {"react": "^18"}},
        readme="# 会员聊天\n包含聊天与会话页面。",
    )
    profile = build_profile("m1", "CheerlyAIChat", str(repo))
    assert profile.roles == ["frontend"]
    assert "react" in profile.fingerprints
    assert profile.package_name == "cheerly-chat"
    assert profile.readme_title == "会员聊天"
    assert profile.languages.get("TypeScript", 0) >= 1


def test_profile_detects_backend_from_pyproject(tmp_path):
    repo = tmp_path / "svc"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "cheerly-agent"\ndescription = "智能体服务"\n'
        'dependencies = [\n  "fastapi>=0.110",\n  "uvicorn",\n]\n',
        encoding="utf-8",
    )
    profile = build_profile("m2", "CheerlyAgent", str(repo))
    assert "backend" in profile.roles
    assert profile.package_name == "cheerly-agent"


def test_profile_detects_mobile_from_native_dirs(tmp_path):
    repo = _make_repo(tmp_path / "app", package={"name": "mini"})
    (repo / "ios").mkdir()
    (repo / "android").mkdir()
    profile = build_profile("m3", "MiniProgram", str(repo))
    assert "mobile" in profile.roles


def test_profile_of_missing_path_is_empty(tmp_path):
    profile = build_profile("m4", "Gone", str(tmp_path / "nope"))
    assert profile.roles == []
    assert profile.readme_title == ""


def test_store_persists_and_prunes(tmp_path):
    repo = _make_repo(tmp_path / "fe", package={"name": "fe", "dependencies": {"react": "18"}})
    store = RepoProfileStore(tmp_path / "repo-profiles.json")

    class Mapping:
        def __init__(self, id_, name, path, enabled=True):
            self.id = id_
            self.name = name
            self.path = path
            self.enabled = enabled

    mappings = [Mapping("m1", "FE", str(repo))]
    result = ensure_profiles(store, mappings)
    assert result["created"] == 1
    assert store.get("m1") is not None

    # 重新加载：文件可读
    reloaded = RepoProfileStore(tmp_path / "repo-profiles.json")
    assert reloaded.get("m1").name == "FE"

    # 映射删掉后画像应被清理
    ensure_profiles(reloaded, [])
    assert reloaded.get("m1") is None


def test_store_need_rebuild_when_path_moves(tmp_path):
    a = _make_repo(tmp_path / "a", package={"name": "a"})
    b = _make_repo(tmp_path / "b", package={"name": "b"})
    store = RepoProfileStore(tmp_path / "p.json")
    store.rebuild("m1", "A", str(a))
    assert store.need_rebuild("m1", str(b)) is True
    assert store.need_rebuild("m1", str(a)) is False
