"""Auto-generation of data/verify.json (detection + ensure/rebuild)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from taskagent.verifyconfig import (
    build_entry,
    detect_checks,
    detect_link_dirs,
    ensure_verify_config,
    fingerprint,
    is_stale,
    prune_verify_config,
)


def _mapping(name: str, path: Path, *, enabled: bool = True):
    return SimpleNamespace(id=f"id-{name}", name=name, path=str(path), enabled=enabled)


def _python_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\ntestpaths = [\"tests\"]\n[tool.ruff]\nline-length = 100\n",
        encoding="utf-8",
    )
    return repo


def _node_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    web = repo / "web"
    web.mkdir(parents=True)
    (web / "package.json").write_text(
        json.dumps({
            "scripts": {
                "test": "vitest run",
                "lint": "eslint src",
                "typecheck": "tsc -b",
                "build": "vite build",
            },
        }),
        encoding="utf-8",
    )
    (web / "pnpm-lock.yaml").write_text("lockfileVersion: '9'\n", encoding="utf-8")
    (web / "node_modules").mkdir()
    return repo


def test_detect_python_repo(tmp_path: Path) -> None:
    repo = _python_repo(tmp_path)
    checks = detect_checks(repo)
    by_category = {check["category"]: check for check in checks}
    assert by_category["test"]["command"] == [".venv/bin/python", "-m", "pytest", "-q"]
    assert by_category["lint"]["command"] == [
        ".venv/bin/python", "-m", "ruff", "check", "src", "tests",
    ]
    assert detect_link_dirs(repo) == [".venv"]


def test_detect_node_repo(tmp_path: Path) -> None:
    repo = _node_repo(tmp_path)
    checks = {check["category"]: check for check in detect_checks(repo)}
    assert checks["test"]["command"] == ["pnpm", "--dir", "web", "run", "test"]
    assert checks["typecheck"]["command"] == ["pnpm", "--dir", "web", "run", "typecheck"]
    assert checks["build"]["command"] == ["pnpm", "--dir", "web", "run", "build"]
    assert detect_link_dirs(repo) == ["web/node_modules"]


def test_node_typecheck_fallback_from_tsconfig(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    web = repo / "web"
    web.mkdir(parents=True)
    (web / "package.json").write_text(
        json.dumps({"scripts": {"lint": "eslint src", "build": "vite build"}}),
        encoding="utf-8",
    )
    (web / "pnpm-lock.yaml").write_text("lockfileVersion: '9'\n", encoding="utf-8")
    (web / "tsconfig.json").write_text("{}", encoding="utf-8")
    checks = {check["category"]: check for check in detect_checks(repo)}
    assert checks["typecheck"]["command"] == ["pnpm", "--dir", "web", "exec", "tsc", "-b"]


def test_detect_go_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text("module demo\n", encoding="utf-8")
    categories = {check["category"] for check in detect_checks(repo)}
    assert {"test", "lint", "build"} <= categories


def test_ensure_adds_missing_and_preserves_existing(tmp_path: Path) -> None:
    path = tmp_path / "verify.json"
    path.write_text(
        json.dumps({"repos": {"manual": {"checks": [{"id": "x", "command": ["true"]}]}}}),
        encoding="utf-8",
    )
    repo = _python_repo(tmp_path)
    result = ensure_verify_config(path, [
        _mapping("manual", tmp_path / "other"),
        _mapping("python", repo),
    ])
    assert result["created"] == 1
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["repos"]["manual"]["checks"][0]["id"] == "x"  # untouched
    assert data["repos"]["python"]["source"] == "auto"
    assert data["repos"]["python"]["checks"]


def test_ensure_does_not_write_empty_detection(tmp_path: Path) -> None:
    path = tmp_path / "verify.json"
    empty = tmp_path / "empty"
    empty.mkdir()
    ensure_verify_config(path, [_mapping("empty", empty)])
    assert not path.exists()

    ensure_verify_config(path, [_mapping("empty", empty)], force=True)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["repos"]["empty"]["checks"] == []
    assert data["repos"]["empty"]["detected"] is False


def test_force_rebuild_overwrites_and_prunes(tmp_path: Path) -> None:
    path = tmp_path / "verify.json"
    path.write_text(
        json.dumps({"repos": {
            "manual": {"checks": [{"id": "old", "command": ["true"]}]},
            "gone": {"checks": []},
        }}),
        encoding="utf-8",
    )
    repo = _python_repo(tmp_path)
    result = ensure_verify_config(
        path, [_mapping("manual", repo)], force=True, prune=True
    )
    assert result["pruned"] == 1
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["repos"]["manual"]["source"] == "auto"
    assert {"python-test", "python-lint"} <= {c["id"] for c in data["repos"]["manual"]["checks"]}
    assert "gone" not in data["repos"]


def test_force_rebuild_preserves_na_waivers(tmp_path: Path) -> None:
    path = tmp_path / "verify.json"
    repo = _python_repo(tmp_path)
    path.write_text(
        json.dumps({"repos": {"repo": {
            "checks": [{"id": "old", "category": "test", "command": ["true"]}],
            "na": {"build": "库项目，无构建", "typecheck": "未安装 mypy"},
        }}}),
        encoding="utf-8",
    )
    ensure_verify_config(path, [_mapping("repo", repo)], force=True)
    entry = json.loads(path.read_text(encoding="utf-8"))["repos"]["repo"]
    assert entry["na"] == {"build": "库项目，无构建", "typecheck": "未安装 mypy"}
    assert {check["category"] for check in entry["checks"]} == {"test", "lint"}


def test_prune_verify_config(tmp_path: Path) -> None:
    path = tmp_path / "verify.json"
    path.write_text(
        json.dumps({"repos": {"a": {}, "b": {}}}), encoding="utf-8"
    )
    assert prune_verify_config(path, {"a"}) == 1
    data = json.loads(path.read_text(encoding="utf-8"))
    assert list(data["repos"]) == ["a"]


def test_fingerprint_and_staleness(tmp_path: Path) -> None:
    repo = _python_repo(tmp_path)
    entry = build_entry("repo", str(repo))
    assert entry["checks"]
    assert not is_stale(entry, repo)

    (repo / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    assert fingerprint(repo) != entry["fingerprint"]
    assert is_stale(entry, repo)


def test_disabled_mapping_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "verify.json"
    repo = _python_repo(tmp_path)
    result = ensure_verify_config(
        path, [_mapping("python", repo, enabled=False)]
    )
    assert result["created"] == 0
    assert not path.exists()

def test_verify_config_api_generates_and_prunes(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from taskagent.api import create_app
    from taskagent.config import Settings

    repo = _python_repo(tmp_path)
    settings = Settings(data_dir=tmp_path / "data", project_roots=())
    app = create_app(settings)
    with TestClient(app) as client:
        created = client.post("/api/mappings", json={"name": "demo", "path": str(repo)})
        assert created.status_code == 201, created.text
        mapping_id = created.json()["id"]

        rows = client.get("/api/verify-config").json()["repos"]
        assert len(rows) == 1
        assert rows[0]["configured"] is True
        assert {c["category"] for c in rows[0]["checks"]} == {"test", "lint"}

        rebuilt = client.post("/api/verify-config/rebuild").json()
        assert rebuilt["rebuilt"] == 1
        assert rebuilt["repos"][0]["stale"] is False

        assert client.delete(f"/api/mappings/{mapping_id}").status_code == 200
        assert client.get("/api/verify-config").json()["repos"] == []


def test_verify_config_api_reports_stale(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from taskagent.api import create_app
    from taskagent.config import Settings

    repo = _python_repo(tmp_path)
    settings = Settings(data_dir=tmp_path / "data", project_roots=())
    app = create_app(settings)
    with TestClient(app) as client:
        client.post("/api/mappings", json={"name": "demo", "path": str(repo)})
        before = client.get("/api/verify-config").json()["repos"][0]
        assert before["stale"] is False
        (repo / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
        after = client.get("/api/verify-config").json()["repos"][0]
        assert after["stale"] is True


def test_verify_config_api_edits_na(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from taskagent.api import create_app
    from taskagent.config import Settings

    repo = _python_repo(tmp_path)
    settings = Settings(data_dir=tmp_path / "data", project_roots=())
    app = create_app(settings)
    with TestClient(app) as client:
        created = client.post("/api/mappings", json={"name": "demo", "path": str(repo)})
        mapping_id = created.json()["id"]
        updated = client.put(f"/api/verify-config/{mapping_id}/na", json={
            "na": {
                "typecheck": {"reason": "未安装 mypy"},
                "build": {"reason": "暂无构建", "expires_at": "2000-01-01"},
            },
        })
        assert updated.status_code == 200, updated.text
        row = updated.json()["repos"][0]
        assert row["na"]["typecheck"] == "未安装 mypy"
        assert row["na_meta"]["typecheck"]["approved_by"] == "user"
        assert "build" in row["expired_na"]
        assert "build" in row["missing_categories"]
