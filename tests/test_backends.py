from __future__ import annotations

import json

from fastapi.testclient import TestClient

from taskagent.api import create_app
from taskagent.backends import build_registry, describe_backends
from taskagent.config import Settings
from taskagent.executor import (
    BACKEND_CODEBUDDY,
    BACKEND_DSH,
    CodeBuddyExecutor,
    DshExecutor,
    ExecutorRegistry,
)
from taskagent.models import WorkItem


def test_two_backends_implement_uniform_interface(tmp_path):
    common = {"worktrees_dir": tmp_path / "wt"}
    executors = [
        CodeBuddyExecutor(**common),
        DshExecutor(**common),
    ]
    registry = ExecutorRegistry(
        {e.backend_id: e for e in executors},
        default_backend_id=BACKEND_CODEBUDDY,
    )
    assert set(registry.ids()) == {BACKEND_CODEBUDDY, BACKEND_DSH}
    by_id = {info["id"]: info for info in registry.describe()}
    assert by_id["codebuddy"]["label"] == "CodeBuddy CLI"
    assert by_id["dsh"]["label"] == "DSH"
    # unknown ids fall back to the default backend, never crash dispatch
    assert registry.resolve("does-not-exist").backend_id == BACKEND_CODEBUDDY


def test_dsh_never_passes_model_flag(tmp_path):
    """dsh --profile headless has no --model: never leak the flag to it.

    The model is pinned in the profile's agent-default-model entry, so a
    per-task choice cannot be honoured even though the UI used to offer one.
    """
    dsh = DshExecutor(command="dsh", worktrees_dir=tmp_path / "wt", model="deepseek-flash")
    item = WorkItem.create(title="t", description="d", kind="task", model="deepseek-v4-pro")
    assert "--model" not in dsh.build_command(item)
    assert dsh.supports_model_selection is False

    cb = CodeBuddyExecutor(command="codebuddy", worktrees_dir=tmp_path / "wt")
    assert "--model" in cb.build_command(item)
    assert cb.supports_model_selection is True


def test_describe_backends_flags_model_selection(tmp_path):
    """The UI needs to know which backends can honour a model choice."""
    s = Settings(data_dir=tmp_path)
    s.save_ui_settings({
        "executor": "codebuddy",
        "backends": {
            "codebuddy": {"enabled": True, "command": "/nonexistent/codebuddy"},
            "dsh": {"enabled": True, "command": "/nonexistent/dsh"},
        },
    })
    described = {d["id"]: d for d in describe_backends(s, build_registry(s))}
    assert described["codebuddy"]["model_selection"] is True
    assert described["dsh"]["model_selection"] is False


def test_build_registry_reads_per_type_profiles(tmp_path):
    s = Settings(data_dir=tmp_path)
    s.save_ui_settings({
        "executor": "dsh",
        "backends": {
            "codebuddy": {"enabled": True, "command": "/opt/cb", "model": "m1", "timeout_s": 42},
            "dsh": {"enabled": True, "command": "/opt/dsh"},
        }
    })
    registry = build_registry(s)
    cb = registry.get("codebuddy")
    dsh = registry.get("dsh")
    assert cb is not None and cb.command == "/opt/cb" and cb.model == "m1" and cb.timeout_s == 42
    assert dsh is not None and dsh.command == "/opt/dsh"
    described = {d["id"]: d for d in describe_backends(s, registry)}
    assert described["codebuddy"]["enabled"] is True


def test_backends_can_be_added_and_removed(tmp_path):
    """Backends are configuration-driven, not a fixed set of three."""
    s = Settings(data_dir=tmp_path)
    ui = s.load_ui_settings()
    ui["backends"]["my-agent"] = {
        "label": "My Agent",
        "enabled": True,
        "command": "/opt/my-agent",
        "model": "m1",
        "timeout_s": 30,
        "auto_sync": False,
    }
    s.save_ui_settings(ui)

    loaded = s.load_ui_settings()["backends"]
    assert loaded["my-agent"]["label"] == "My Agent"
    assert loaded["my-agent"]["command"] == "/opt/my-agent"
    assert loaded["my-agent"]["timeout_s"] == 30
    assert loaded["my-agent"]["auto_sync"] is False

    registry = build_registry(s)
    custom = registry.get("my-agent")
    assert custom is not None
    assert custom.backend_id == "my-agent"
    assert custom.label == "My Agent"
    assert custom.command == "/opt/my-agent"
    described = {d["id"]: d for d in describe_backends(s, registry)}
    assert described["my-agent"]["label"] == "My Agent"
    assert described["my-agent"]["enabled"] is True

    # Removing it from the submitted map persists; nothing is force-kept.
    loaded.pop("my-agent")
    s.save_ui_settings({"backends": loaded})
    assert "my-agent" not in s.load_ui_settings()["backends"]
    assert build_registry(s).get("my-agent") is None


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
    assert ids == [BACKEND_CODEBUDDY, BACKEND_DSH]


def test_manual_task_persists_executor(tmp_path):
    s = Settings(data_dir=tmp_path, project_roots=(tmp_path,))
    (tmp_path / "projects.json").write_text(
        json.dumps([{"name": "demo", "aliases": [], "path": str(tmp_path), "enabled": True}]),
        encoding="utf-8",
    )
    with TestClient(create_app(s)) as client:
        created = client.post(
            "/api/tasks/manual",
            json={"title": "t", "repo_name": "demo", "executor": "dsh", "exec_mode": "dry-run"},
        ).json()
    assert created["executor"] == "dsh"


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


def test_no_backend_means_no_privileged_fallback(tmp_path):
    """With no backends configured, resolve returns None and codebuddy is NOT re-added."""
    s = Settings(data_dir=tmp_path)
    s.save_ui_settings({"backends": {}, "executor": ""})
    registry = build_registry(s)
    assert registry.ids() == []
    assert registry.resolve("anything") is None
    assert registry.resolve(None) is None
    assert s.load_ui_settings()["backends"] == {}


def test_default_backend_is_the_fallback(tmp_path):
    """A missing/unknown backend resolves to the configured default backend."""
    s = Settings(data_dir=tmp_path)
    s.save_ui_settings({
        "executor": "dsh",
        "backends": {
            "codebuddy": {"enabled": True, "command": "/opt/cb"},
            "dsh": {"enabled": True, "command": "/opt/dsh"},
        },
    })
    registry = build_registry(s)
    assert registry.default_backend_id == "dsh"
    assert registry.resolve("does-not-exist").backend_id == "dsh"
    assert registry.resolve(None).backend_id == "dsh"
    assert registry.resolve("codebuddy").backend_id == "codebuddy"


def test_codebuddy_can_be_removed(tmp_path):
    """codebuddy is a normal seed, not a privileged backend: it can be deleted."""
    s = Settings(data_dir=tmp_path)
    ui = s.load_ui_settings()
    del ui["backends"]["codebuddy"]
    ui["executor"] = "dsh"
    s.save_ui_settings(ui)
    loaded = s.load_ui_settings()
    assert "codebuddy" not in loaded["backends"]
    assert loaded["executor"] == "dsh"
    registry = build_registry(s)
    assert "codebuddy" not in registry.ids()
