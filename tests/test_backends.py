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
        command=str(_fake_dsh(tmp_path, supports_patch=True, applies_patch=True)),
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
    """The UI needs to know which backends can honour a model choice.

    codebuddy advertises ``--model`` in its help, so selection is always
    supported; DSH is capability-probed, and a missing binary must degrade to
    False rather than promising a choice it cannot apply.
    """
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
    # /api/config stays optimistic: probing is deferred to the first model
    # selection (POST /api/backends/{id}/probe), so page loads never shell out.
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


def test_dsh_marks_pinned_model_as_current(tmp_path):
    """The pinned profile model carries ``current`` so the UI can name it.

    Without the flag the settings page can only say "由 CLI 决定"; codebuddy
    already reports ``current`` from its local cache, and DSH should match.
    """
    profiles = tmp_path / "profiles" / "desktop"
    profiles.mkdir(parents=True)
    (profiles / "cordis.patch.yml").write_text(
        "- id: llm-pi-ai\n"
        '  name: "@deepseek-ai/dsh-llm-pi-ai"\n'
        "  config:\n"
        "    providers:\n"
        "      qwen-token-plan:\n"
        "        models:\n"
        "          - id: deepseek-v4.1-flash\n"
        "            name: DeepSeek V4.1 Flash\n"
        "            contextWindow: 1000000\n",
        encoding="utf-8",
    )
    dsh = DshExecutor(
        command="/bin/true",
        worktrees_dir=tmp_path / "wt",
        patch_dir=tmp_path / "patches",
        profile_home=tmp_path,
    )
    dsh._pinned_cache = ("qwen-token-plan", "deepseek-v4.1-flash")  # avoid a CLI call

    models = {entry["id"]: entry for entry in dsh.list_models()}
    assert models["deepseek-v4.1-flash"]["current"] is True
    assert models["deepseek-v4.1-flash"]["label"] == "DeepSeek V4.1 Flash"
    assert models["deepseek-v4.1-flash"]["context"] == 1000000


def test_parse_pinned_target_reads_real_dump_shape():
    """Parsing is pinned to the shape `dsh --dump-config` actually emits.

    Fixture is a verbatim excerpt from a real headless dump; the entry ends at
    the next ``- id:`` marker, so the parser must not run past it.
    """
    dump = """- id: agent-default-model
  name: '@deepseek-ai/dsh-agent-default-model'
  config:
    provider: deepseek-official
    model: deepseek-flash
- id: jobs
  name: '@deepseek-ai/dsh-jobs-local'
- id: llm-retry
"""
    assert dsh_profile.parse_pinned_target(dump) == ("deepseek-official", "deepseek-flash")
    assert dsh_profile.parse_pinned_target("no such entry here") is None
    # provider 存在但缺少 model 时不返回半截结果
    assert dsh_profile.parse_pinned_target(
        "- id: agent-default-model\n  config:\n    provider: x\n- id: next\n"
    ) is None


def _fake_dsh(tmp_path: Path, *, supports_patch: bool, applies_patch: bool) -> Path:
    """Stand-in CLI: emulates just the two commands discovery/probing uses."""
    script = tmp_path / "fake-dsh"
    patch_help = "  --patch <path>  extra patch-list overlay\n" if supports_patch else ""
    # 3.11 的 f-string 不允许反斜杠/复用引号，脚本片段先拼好再插入
    apply_line = "model=$(sed -n 's/^ *model: //p' \"$patch\" | tail -1)" if applies_patch else ":"
    script.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        "  --help)\n"
        f'    printf "Usage: dsh [--profile] <name>\\n{patch_help}"\n'
        "    exit 0;;\n"
        "esac\n"
        'patch=""; prev=""\n'
        'for a in "$@"; do if [ "$prev" = "--patch" ]; then patch="$a"; fi; prev="$a"; done\n'
        'model="deepseek-flash"\n'
        'if [ -n "$patch" ]; then\n'
        f"  {apply_line}\n"
        "fi\n"
        'printf -- "- id: agent-default-model\\n  name: \'@deepseek-ai/dsh-agent-default-model\'\\n'
        '  config:\\n    provider: deepseek-official\\n    model: %s\\n- id: next\\n" "$model"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _write_two_model_catalog(root: Path) -> None:
    path = root / "profiles" / "desktop"
    path.mkdir(parents=True, exist_ok=True)
    (path / "cordis.patch.yml").write_text(
        "- id: llm-pi-ai\n"
        '  name: "@deepseek-ai/dsh-llm-pi-ai"\n'
        "  config:\n"
        "    providers:\n"
        "      qwen-token-plan:\n"
        "        models:\n"
        "          - id: deepseek-v4-pro\n"
        "            name: DeepSeek V4 Pro\n"
        "          - id: kimi-k2.7-code\n"
        "            name: Kimi K2.7 Code\n",
        encoding="utf-8",
    )


def test_dsh_capability_requires_patch_flag(tmp_path):
    """A DSH build without --patch must not offer a model choice."""
    script = _fake_dsh(tmp_path, supports_patch=False, applies_patch=False)
    ex = DshExecutor(
        command=str(script), worktrees_dir=tmp_path / "wt",
        patch_dir=tmp_path / "patches", profile_home=tmp_path,
    )
    assert ex.supports_model_selection_check() is False
    item = WorkItem.create(title="t", description="d", kind="task", model="kimi-k2.7-code")
    assert "--patch" not in ex.build_command(item)


def test_dsh_capability_requires_applied_overlay(tmp_path):
    """If the CLI ignores the overlay, selection is disabled (no phantom choice)."""
    _write_two_model_catalog(tmp_path)
    script = _fake_dsh(tmp_path, supports_patch=True, applies_patch=False)
    ex = DshExecutor(
        command=str(script), worktrees_dir=tmp_path / "wt",
        patch_dir=tmp_path / "patches", profile_home=tmp_path,
    )
    assert ex.supports_model_selection_check() is False


def test_dsh_capability_true_when_overlay_applies(tmp_path):
    _write_two_model_catalog(tmp_path)
    script = _fake_dsh(tmp_path, supports_patch=True, applies_patch=True)
    ex = DshExecutor(
        command=str(script), worktrees_dir=tmp_path / "wt",
        patch_dir=tmp_path / "patches", profile_home=tmp_path,
    )
    assert ex.supports_model_selection_check() is True
    item = WorkItem.create(title="t", description="d", kind="task", model="kimi-k2.7-code")
    assert "--patch" in ex.build_command(item)


def test_dsh_capability_false_without_alternate_model(tmp_path):
    """Only the pinned model available => nothing to select, nothing to verify."""
    script = _fake_dsh(tmp_path, supports_patch=True, applies_patch=True)
    ex = DshExecutor(
        command=str(script), worktrees_dir=tmp_path / "wt",
        patch_dir=tmp_path / "patches", profile_home=tmp_path,
    )
    assert ex.supports_model_selection_check() is False


def test_patch_ignored_warning_flags_silent_default():
    """An ignored overlay must not be reported as a successful model run."""
    stderr = 'dsh: [/tmp/bad.yml] patch: entry "agent-default-model" not found\n'
    warning = dsh_profile.patch_ignored_warning(stderr)
    assert warning and "默认模型" in warning
    assert dsh_profile.patch_ignored_warning("all good") is None


def test_parse_pinned_target_ignores_composer_comments():
    """Patched dumps wrap the entry in "# == … patched by …" comment lines."""
    composed = (
        "# == @deepseek-ai/dsh-base, patched by /tmp/probe.yml\n"
        "- id: agent-default-model\n"
        "  name: '@deepseek-ai/dsh-agent-default-model'\n"
        "  config:\n"
        "    provider: qwen-token-plan\n"
        "    model: deepseek-v4-pro\n"
        "# == @deepseek-ai/dsh-base\n"
        "- id: jobs\n"
    )
    assert dsh_profile.parse_pinned_target(composed) == ("qwen-token-plan", "deepseek-v4-pro")


def test_dsh_build_command_uses_headless_profile_and_app_flags(tmp_path):
    """DSH's headless app has its own protocol; launcher flags must come first.

    A `--patch` after an app flag is parsed as an app argument and rejected as
    "unknown option", so ordering is part of the contract.
    """
    _write_two_model_catalog(tmp_path)
    dsh = DshExecutor(
        command=str(_fake_dsh(tmp_path, supports_patch=True, applies_patch=True)),
        worktrees_dir=tmp_path / "wt",
        patch_dir=tmp_path / "patches",
        profile_home=tmp_path,
    )
    dsh._pinned_cache = ("deepseek-official", "deepseek-flash")
    item = WorkItem.create(title="t", description="d", kind="task", model="kimi-k2.7-code")
    argv = dsh.build_command(item)

    assert argv[1:3] == ["--profile", "headless"]
    assert "--json" in argv
    assert "-p" not in argv and "--output-format" not in argv
    # permission preset is translated into DSH's own vocabulary
    assert argv[argv.index("--permission-mode") + 1] == "workspace-write"
    # launcher flags precede app flags
    assert argv.index("--patch") < argv.index("--permission-mode") < argv.index("--json")


def test_dsh_permission_preset_translation(tmp_path):
    _write_two_model_catalog(tmp_path)
    dsh = DshExecutor(
        command=str(_fake_dsh(tmp_path, supports_patch=True, applies_patch=True)),
        worktrees_dir=tmp_path / "wt",
        patch_dir=tmp_path / "patches",
        profile_home=tmp_path,
        permission_mode="readOnly",
    )
    dsh._pinned_cache = ("deepseek-official", "deepseek-flash")
    item = WorkItem.create(title="t", description="d", kind="task", model="kimi-k2.7-code")
    argv = dsh.build_command(item)
    assert argv[argv.index("--permission-mode") + 1] == "read-only"


def test_ndjson_verdict_uses_event_stream():
    from taskagent.dialect import NdjsonStrictVerdict

    policy = NdjsonStrictVerdict()
    ok_events = (
        '{"type":"session","sessionId":"s"}\n'
        '{"type":"status","phase":"turn_start","turn":1}\n'
        '{"type":"message","role":"assistant","content":"done"}\n'
        '{"type":"status","phase":"turn_end","turn":1,"reason":{"kind":"stop"}}\n'
    )
    ok, error = policy.decide(returncode=0, stdout=ok_events, stderr="", label="DSH")
    assert ok and error is None

    # a turn that ended in error is a failure even with exit code 0
    failed_events = (
        '{"type":"status","phase":"turn_end","turn":1,'
        '"reason":{"kind":"error","error":{"message":"no API key"}}}\n'
    )
    ok, error = policy.decide(returncode=0, stdout=failed_events, stderr="", label="DSH")
    assert not ok and "no API key" in (error or "")

    # only lifecycle noise, no real work
    ok, error = policy.decide(
        returncode=0, stdout='{"type":"session"}\n', stderr="", label="DSH"
    )
    assert not ok


def test_dsh_patch_path_is_absolute(tmp_path, monkeypatch):
    """The CLI runs with cwd=worktree; a relative --patch would be ENOENT."""
    _write_two_model_catalog(tmp_path)
    dsh = DshExecutor(
        command=str(_fake_dsh(tmp_path, supports_patch=True, applies_patch=True)),
        worktrees_dir=tmp_path / "wt",
        patch_dir=Path("relative/patches"),  # relative on purpose
        profile_home=tmp_path,
    )
    dsh._pinned_cache = ("deepseek-official", "deepseek-flash")
    item = WorkItem.create(title="t", description="d", kind="task", model="kimi-k2.7-code")
    argv = dsh.build_command(item)
    patch_path = Path(argv[argv.index("--patch") + 1])
    assert patch_path.is_absolute()
    assert patch_path.is_file()
    dsh.cleanup_patch(item.id)


def test_credential_refs_reads_flow_map(tmp_path):
    """The desktop app writes refs as a flow map; headless only reads env."""
    creds = tmp_path / ".credentials.yaml"
    creds.write_text(
        "version: 1\n"
        "records:\n"
        "  x:\n"
        "    kind: grant\n"
        "refs:\n"
        "  {\n"
        "    QWEN_TOKEN_PLAN_API_KEY: sk-test-value\n"
        "  }\n"
        "other:\n"
        "  KEY: ignored\n",
        encoding="utf-8",
    )
    assert dsh_profile.credential_refs(tmp_path) == {
        "QWEN_TOKEN_PLAN_API_KEY": "sk-test-value"
    }


def test_credential_refs_reads_list_form(tmp_path):
    creds = tmp_path / ".credentials.yaml"
    creds.write_text(
        "refs:\n  - QWEN_TOKEN_PLAN_API_KEY: sk-a\n  - DEEPSEEK_API_KEY: sk-b\n"
        "tail: 1\n",
        encoding="utf-8",
    )
    assert dsh_profile.credential_refs(tmp_path) == {
        "QWEN_TOKEN_PLAN_API_KEY": "sk-a",
        "DEEPSEEK_API_KEY": "sk-b",
    }


def test_dsh_env_injects_stored_credentials(tmp_path, monkeypatch):
    """A key configured in the DSH app must reach the headless subprocess."""
    _write_two_model_catalog(tmp_path)
    (tmp_path / ".credentials.yaml").write_text(
        "refs:\n  {\n    QWEN_TOKEN_PLAN_API_KEY: from-store\n  }\n", encoding="utf-8"
    )
    monkeypatch.delenv("QWEN_TOKEN_PLAN_API_KEY", raising=False)
    dsh = DshExecutor(
        command="/bin/true", worktrees_dir=tmp_path / "wt",
        patch_dir=tmp_path / "patches", profile_home=tmp_path,
    )
    assert dsh._clean_env()["QWEN_TOKEN_PLAN_API_KEY"] == "from-store"

    # an explicit environment variable still wins over the stored value
    monkeypatch.setenv("QWEN_TOKEN_PLAN_API_KEY", "from-env")
    assert dsh._clean_env()["QWEN_TOKEN_PLAN_API_KEY"] == "from-env"
