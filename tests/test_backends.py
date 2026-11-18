from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from taskagent import dsh_profile
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


def test_dsh_uses_patch_overlay_instead_of_model_flag(tmp_path):
    """dsh --profile headless has no --model: the choice goes through --patch.

    The overlay is a per-run file, so concurrent tasks cannot fight over one
    profile setting, and the user's own profile is never modified.
    """
    profiles = tmp_path / "profiles" / "desktop"
    profiles.mkdir(parents=True)
    (profiles / "cordis.patch.yml").write_text(
        "- id: llm-pi-ai\n"
        '  name: "@deepseek-ai/dsh-llm-pi-ai"\n'
        "  config:\n"
        "    providers:\n"
        "      qwen-token-plan:\n"
        "        baseURL: https://example.invalid/v1\n"
        "        apiKeyEnv: QWEN_TOKEN_PLAN_API_KEY\n"
        "        models:\n"
        "          - id: deepseek-v4-pro\n"
        "            name: DeepSeek V4 Pro\n",
        encoding="utf-8",
    )

    dsh = DshExecutor(
        command="/bin/true",
        worktrees_dir=tmp_path / "wt",
        patch_dir=tmp_path / "patches",
        profile_home=tmp_path,
    )
    item = WorkItem.create(title="t", description="d", kind="task", model="deepseek-v4-pro")
    argv = dsh.build_command(item)

    assert "--model" not in argv
    assert "--patch" in argv
    patch_file = Path(argv[argv.index("--patch") + 1])
    assert patch_file.is_file()
    text = patch_file.read_text(encoding="utf-8")
    assert "model: deepseek-v4-pro" in text
    assert "provider: qwen-token-plan" in text

    models = {entry["id"] for entry in dsh.list_models()}
    assert "deepseek-v4-pro" in models

    dsh.cleanup_patch(item.id)
    assert not patch_file.exists()


def test_dsh_patch_includes_provider_when_not_default(tmp_path):
    """A gateway the headless profile doesn't know must be re-declared."""
    entry = dsh_profile.ModelEntry(
        provider="qwen-token-plan",
        model_id="kimi-k2.7-code",
        label="Kimi K2.7 Code",
        base_url="https://example.invalid/v1",
        api_key_env="QWEN_TOKEN_PLAN_API_KEY",
    )
    text = dsh_profile.build_patch_yaml(
        provider=entry.provider,
        model_id=entry.model_id,
        entry=entry,
        default_provider="deepseek-official",
    )
    assert "llm-pi-ai" in text
    assert "baseURL: https://example.invalid/v1" in text
    assert "apiKeyEnv: QWEN_TOKEN_PLAN_API_KEY" in text
    assert "model: kimi-k2.7-code" in text


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
    # DSH can select a model, but through a --patch overlay rather than --model.
    assert described["dsh"]["model_selection"] is True


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


def test_list_models_reads_local_cache_structured():
    """list_models parses codebuddy's cached model list into {id, label, context}."""
    ex = CodeBuddyExecutor(command="/nonexistent/codebuddy")

    # availableModels shape (models.json).
    payload = {
        "availableModels": [
            {"id": "deepseek-v4-pro", "name": "DeepSeek-V4-Pro", "vendor": "f", "maxInputTokens": 1000000},
            {"id": "glm-5.3", "name": "GLM-5.3", "vendor": "e", "maxInputTokens": 1000000},
        ]
    }
    found = ex._models_from_cache_payload(payload)
    assert [m["id"] for m in found] == ["deepseek-v4-pro", "glm-5.3"]
    assert found[0]["label"] == "DeepSeek-V4-Pro"
    assert found[0]["context"] == 1000000
    assert found[0]["vendor"] == "f"

    # models shape (local_storage). Label falls back to id when name missing.
    payload2 = {"models": [{"id": "kimi-k3-1", "maxInputTokens": 1000000}]}
    found2 = ex._models_from_cache_payload(payload2)
    assert found2 == [{"id": "kimi-k3-1", "label": "kimi-k3-1", "context": 1000000}]

    # entries without a usable id are skipped.
    payload3 = {"models": [{"name": "no-id"}, "bare-string", {"id": ""}]}
    assert ex._models_from_cache_payload(payload3) == []


def test_list_models_reads_local_storage_file(monkeypatch, tmp_path):
    """_models_from_local_cache scans ~/.codebuddy/local_storage/*.info."""
    home = tmp_path / "home"
    storage = home / ".codebuddy" / "local_storage"
    storage.mkdir(parents=True)
    (storage / "entry_aaa.info").write_text(
        json.dumps([{"userId": "u1", "data": {"models": [
            {"id": "deepseek-v4.1-flash", "name": "DeepSeek-V4.1-Flash", "maxInputTokens": 1000000},
        ]}}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: home)

    ex = CodeBuddyExecutor(command="/nonexistent/codebuddy")
    found = ex._models_from_local_cache()
    assert [m["id"] for m in found] == ["deepseek-v4.1-flash"]
    assert found[0]["label"] == "DeepSeek-V4.1-Flash"


def test_list_models_reads_gzip_product_config(monkeypatch, tmp_path):
    """The real cache shape: a JSON string holding base64 gzip product config.

    The config's top-level ``models`` carries rich metadata while the cli
    agent's ``models`` array is the /model list allowlist — results must be
    filtered to it and follow its order.
    """
    import base64
    import gzip

    home = tmp_path / "home"
    storage = home / ".codebuddy" / "local_storage"
    storage.mkdir(parents=True)

    config = {
        "models": [
            {"id": "default", "name": "Default", "maxInputTokens": 200000},
            {"id": "glm-5.3", "name": "GLM-5.3", "vendor": "e", "maxInputTokens": 1000000},
            {"id": "hy4flash", "name": "Hy4 flash", "vendor": "v", "maxInputTokens": 960000},
            {"id": "kling-video-v3", "name": "Kling-Video-V3"},
        ],
        "agents": [
            {"name": "cli", "tags": ["cli", "default"], "models": ["hy4flash", "glm-5.3"]},
            {"name": "lite-agent", "models": ["lite"]},
        ],
    }
    packed = base64.b64encode(gzip.compress(json.dumps(config).encode())).decode()
    # A bare marker string sorts before the payload alphabetically; the
    # scanner must skip it instead of crashing or stopping early.
    (storage / "entry_aaa.info").write_text(json.dumps("internal"), encoding="utf-8")
    (storage / "entry_zzz.info").write_text(json.dumps(packed), encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)

    ex = CodeBuddyExecutor(command="/nonexistent/codebuddy")
    found = ex._models_from_local_cache()
    assert [m["id"] for m in found] == ["hy4flash", "glm-5.3"]
    assert found[0] == {"id": "hy4flash", "label": "Hy4 flash", "vendor": "v", "context": 960000}
    assert found[1]["label"] == "GLM-5.3"
    assert found[1]["context"] == 1000000


def test_dedupe_models_collapses_same_label_and_context():
    """hy4flash-free / hy4flash both display "Hy4 flash" — one row, alias kept."""
    models = [
        {"id": "hy4flash-free", "label": "Hy4 flash", "context": 960000},
        {"id": "hy4flash", "label": "Hy4 flash", "context": 960000},
        {"id": "glm-5.3", "label": "GLM-5.3", "context": 1000000},
        # Same label but different context must NOT collapse.
        {"id": "x-lite", "label": "GLM-5.3", "context": 200000},
    ]
    out = CodeBuddyExecutor._dedupe_models(models)
    assert [m["id"] for m in out] == ["hy4flash-free", "glm-5.3", "x-lite"]
    assert out[0]["aliases"] == ["hy4flash"]
    assert "aliases" not in out[1]


def test_dedupe_keeps_current_flag_from_dropped_alias():
    """When the dropped alias is the current model, the kept entry is flagged."""
    models = [
        {"id": "hy4flash-free", "label": "Hy4 flash", "context": 960000},
        {"id": "hy4flash", "label": "Hy4 flash", "context": 960000, "current": True},
    ]
    out = CodeBuddyExecutor._dedupe_models(models)
    assert len(out) == 1
    assert out[0]["id"] == "hy4flash-free"
    assert out[0]["current"] is True


def test_current_model_from_sessions(monkeypatch, tmp_path):
    """Newest session's last providerData.requestModelId marks the entry."""
    home = tmp_path / "home"
    proj = home / ".codebuddy" / "projects" / "some-repo"
    proj.mkdir(parents=True)
    older = proj / "old.jsonl"
    older.write_text(
        json.dumps({"type": "message", "providerData": {"requestModelId": "glm-5.2"}}) + "\n",
        encoding="utf-8",
    )
    newer = proj / "new.jsonl"
    newer.write_text(
        json.dumps({"type": "message", "providerData": {"requestModelId": "hy4flash-free"}}) + "\n"
        + json.dumps({"type": "message", "providerData": {"requestModelId": "hy4flash"}}) + "\n",
        encoding="utf-8",
    )
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))
    monkeypatch.setattr(Path, "home", lambda: home)

    ex = CodeBuddyExecutor(command="/nonexistent/codebuddy")
    assert ex._current_model_from_sessions() == "hy4flash"

    models = [
        {"id": "hy4flash-free", "label": "Hy4 flash", "context": 960000},
        {"id": "hy4flash", "label": "Hy4 flash", "context": 960000},
        {"id": "glm-5.2", "label": "GLM-5.2", "context": 1000000},
    ]
    out = ex._dedupe_models(ex._mark_current(models))
    assert len(out) == 2
    assert out[0]["current"] is True
    assert "current" not in out[1]


def test_current_model_missing_sessions_is_noop(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    ex = CodeBuddyExecutor(command="/nonexistent/codebuddy")
    models = [{"id": "glm-5.3", "label": "GLM-5.3"}]
    assert ex._mark_current(models) == [{"id": "glm-5.3", "label": "GLM-5.3"}]


def test_cache_allowlist_id_without_metadata_still_listed():
    """An allowlisted id absent from the models array still shows (id=label)."""
    payload = {
        "models": [{"id": "glm-5.3", "name": "GLM-5.3", "maxInputTokens": 1000000}],
        "agents": [{"name": "cli", "models": ["glm-5.3", "brand-new-model"]}],
    }
    found = CodeBuddyExecutor._models_from_cache_payload(payload)
    assert [m["id"] for m in found] == ["glm-5.3", "brand-new-model"]
    assert found[1] == {"id": "brand-new-model", "label": "brand-new-model"}


def test_list_models_falls_back_to_help_without_cache(monkeypatch, tmp_path):
    """When no local cache exists, list_models falls back to --help parsing."""
    home = tmp_path / "empty-home"
    home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)

    ex = CodeBuddyExecutor(command="/nonexistent/codebuddy")
    # No cache + nonexistent CLI => empty, not a crash.
    assert ex.list_models() == []
