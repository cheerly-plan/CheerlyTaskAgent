from __future__ import annotations

import json

from fastapi.testclient import TestClient

from taskagent.api import create_app
from taskagent.backends import build_registry, describe_backends
from taskagent.config import Settings
from taskagent.executor import (
    BACKEND_CODEBUDDY,
    BACKEND_DSH,
    BACKEND_WORKBUDDY,
    CodeBuddyExecutor,
    DshExecutor,
    ExecutorRegistry,
    WorkBuddyExecutor,
)
from taskagent.models import WorkItem


def test_three_backends_implement_uniform_interface(tmp_path):
    common = {"worktrees_dir": tmp_path / "wt"}
    executors = [
        CodeBuddyExecutor(**common),
        WorkBuddyExecutor(**common),
        DshExecutor(**common),
    ]
    registry = ExecutorRegistry({e.backend_id: e for e in executors})
    assert set(registry.ids()) == {BACKEND_CODEBUDDY, BACKEND_WORKBUDDY, BACKEND_DSH}
    by_id = {info["id"]: info for info in registry.describe()}
    assert by_id["codebuddy"]["label"] == "CodeBuddy CLI"
    assert by_id["workbuddy"]["label"] == "WorkBuddy CLI"
    assert by_id["dsh"]["label"] == "DSH"
    # unknown ids fall back to codebuddy, never crash dispatch
    assert registry.resolve("does-not-exist").backend_id == BACKEND_CODEBUDDY


def test_build_registry_reads_per_type_profiles(tmp_path):
    s = Settings(data_dir=tmp_path)
    s.save_ui_settings({
        "backends": {
            "workbuddy": {"enabled": True, "command": "/opt/wb", "model": "m1", "timeout_s": 42},
            "dsh": {"enabled": True, "command": "/opt/dsh"},
        }
    })
    registry = build_registry(s)
    wb = registry.get("workbuddy")
    dsh = registry.get("dsh")
    assert wb is not None and wb.command == "/opt/wb" and wb.model == "m1" and wb.timeout_s == 42
    assert dsh is not None and dsh.command == "/opt/dsh"
    described = {d["id"]: d for d in describe_backends(s, registry)}
    assert described["workbuddy"]["enabled"] is True
    assert described["codebuddy"]["enabled"] is True  # fallback always on


def test_workitem_executor_roundtrip_and_legacy_default():
    item = WorkItem.create(title="t", description="d", executor="dsh")
    assert item.executor == "dsh"
    assert WorkItem.from_dict(item.to_dict()).executor == "dsh"
    legacy = item.to_dict()
    legacy.pop("executor")
    assert WorkItem.from_dict(legacy).executor == BACKEND_CODEBUDDY


def test_api_config_lists_backends(tmp_path):
    s = Settings(data_dir=tmp_path, project_roots=(tmp_path,))
    with TestClient(create_app(s)) as client:
        cfg = client.get("/api/config").json()
    ids = [b["id"] for b in cfg["backends"]]
    assert ids == [BACKEND_CODEBUDDY, BACKEND_WORKBUDDY, BACKEND_DSH]


def test_manual_task_persists_executor(tmp_path):
    s = Settings(data_dir=tmp_path, project_roots=(tmp_path,))
    (tmp_path / "projects.json").write_text(
        json.dumps([{"name": "demo", "aliases": [], "path": str(tmp_path), "enabled": True}]),
        encoding="utf-8",
    )
    with TestClient(create_app(s)) as client:
        created = client.post(
            "/api/tasks/manual",
            json={"title": "t", "repo_name": "demo", "executor": "workbuddy", "exec_mode": "dry-run"},
        ).json()
    assert created["executor"] == "workbuddy"


def test_settings_api_backends_roundtrip(tmp_path):
    s = Settings(data_dir=tmp_path, project_roots=(tmp_path,))
    with TestClient(create_app(s)) as client:
        ui = client.get("/api/settings").json()
        assert "backends" in ui
        ui["backends"]["dsh"]["enabled"] = True
        ui["backends"]["dsh"]["command"] = "/custom/dsh"
        saved = client.put("/api/settings", json=ui).json()
    assert saved["backends"]["dsh"]["enabled"] is True
    assert saved["backends"]["dsh"]["command"] == "/custom/dsh"
    assert saved["backends"]["codebuddy"]["enabled"] is True
